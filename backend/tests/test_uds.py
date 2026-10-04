import io
import time
from pathlib import Path

from openpyxl import Workbook

from app.database import SessionLocal
from app.models import UdsLine, UdsObservation

ROOT = Path(__file__).resolve().parents[2]


def _wait(client, import_id: str) -> dict:
    deadline = time.time() + 20
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/imports/{import_id}").json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def _book(sheets: dict[str, list[list]]) -> bytes:
    workbook = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet(name)
        sheet.title = name
        first = False
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


ORDER_HEADER = [
    "Facility",
    "Appointment/Servicing Provider",
    "Order Name",
    "Order Status",
    "Primary Insurance Name",
    "Patient Acct No",
    "Encounter Date",
    "Result Date",
    "Order Reviewed Date",
    "Lab Attribute",
    "Lab Attribute Value",
    "ICD Code",
    "Order Cancelled",
    "Report ID",
]


def _orders(rows: list[list]) -> bytes:
    return _book({"page": [ORDER_HEADER, *rows]})


def _mapping() -> bytes:
    return _book(
        {
            "PRACTICES": [
                ["Facility", "Practice"],
                ["Main Clinic", "PRC Associates"],
                ["Bridgeview Center", "Florida Pain Physicians"],
                ["Bridgeview Center", "VIP Pain"],
            ],
            "PAYER AND CATEGORIES": [
                ["Payer", "Insurance Payer ID", "Payer UDS Category"],
                ["Aetna", "1", "Screening and Confirmation"],
            ],
            "LAB CO and LABS": [
                ["Lab Order Name", "Lab Company", "Lab Code", "Lab Type"],
                ["Screening Panel", "SYMPHONY LAB", "EIA200", "Presumptive"],
                ["Confirmation Panel", "Lighthouse", "3094", "Confirmation"],
            ],
        }
    )


def _upload(client, payload: bytes, name: str, acknowledge: str = "false"):
    return client.post(
        "/api/imports",
        files={"file": (name, payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"acknowledge_duplicate": acknowledge},
    )


def _process_orders(client, payload: bytes, name: str, acknowledge: str = "false") -> dict:
    uploaded = _upload(client, payload, name, acknowledge)
    assert uploaded.status_code == 201, uploaded.text
    import_id = uploaded.json()["id"]
    preview = client.get(f"/api/imports/{import_id}/preview", params={"sheet": "page"}).json()
    assert preview["order_details_profile"] is True
    columns = []
    for column in preview["columns"]:
        kind = column["suggested_type"]
        label = column["original_name"].strip().lower()
        if label in {"report id", "patient acct no"}:
            kind = "identifier"
        elif "date" in label:
            kind = "date"
        elif kind in {"integer", "decimal"}:
            kind = "text"
        columns.append({**{key: column[key] for key in ("index", "original_name", "normalized_name")}, "type": kind, "date_format": None})
    saved = client.put(
        f"/api/imports/{import_id}/configuration",
        json={"locale": "en-US", "profile": "order_details", "sheets": [{"name": "page", "include": True, "header_row": 1, "columns": columns}]},
    )
    assert saved.status_code == 200, saved.text
    assert client.post(f"/api/imports/{import_id}/process").status_code == 200
    finished = _wait(client, import_id)
    assert finished["status"] == "completed", finished.get("error_message")
    return finished


def test_order_profile_keeps_observations_and_flags_mapping_issues(client):
    mapping = client.post(
        "/api/uds/mappings",
        files={"file": ("Requirements Document.xlsx", _mapping(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert mapping.status_code == 200, mapping.text
    assert mapping.json()["version_number"] == 1

    first = _orders(
        [
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", None, None, "F11.20", "No", "100"],
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Amphetamine", "POS", "F11.20", "No", "100"],
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Benzodiazepine", "NEG", "F11.20", "No", "100"],
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Order Comment", "see note", "F11.20", "No", "100"],
            ["Bridgeview Center", "Dr B", "Drug Screen Special", "Open", "Mystery Payer", "222", "2026-05-10", "2026-05-11", "", "Amphetamine", "NEG", "F11.20", "No", "200"],
            ["Bridgeview Center", "Dr B", "Drug Screen Special", "Open", "Mystery Payer", "222", "2026-05-10", "2026-05-11", "", "Cocaine", "NEG", "F11.21", "No", "200"],
        ]
    )
    finished = _process_orders(client, first, "orders-a.xlsx")
    assert finished["report"]["uds_order_count"] == 2
    quality = client.get("/api/uds/quality", params={"import_id": finished["id"]}).json()
    assert quality["order_count"] == 2
    assert "UNMAPPED_PAYER" in quality["groups"]
    assert "FACILITY_CONFLICT" in quality["groups"]
    assert "UNCLASSIFIED_ORDER" in quality["groups"]
    assert "ICD_VARIANCE" in quality["groups"]
    orders = client.get("/api/uds/orders", params={"import_id": finished["id"], "current_only": False}).json()["orders"]
    screening = next(item for item in orders if item["report_id"] == "100")
    assert screening["provisional_account"] == "111"
    assert screening["practice"] == "PRC Associates"
    assert screening["payer_category"] == "Screening and Confirmation"
    assert screening["order_class"] == "screening"
    assert screening["analyte_count"] == 2
    assert screening["comment_count"] == 1
    assert screening["classification"]["result"]["value"] == "positive"
    assert screening["classification"]["billing"]["value"] == "pending"
    detail = client.get("/api/uds/orders/100", params={"import_id": finished["id"]}).json()
    roles = {line["role"] for line in detail["observations"][0]["lines"]}
    assert roles == {"order_line", "analyte", "comment"}

    second = _orders(
        [
            ["Main Clinic", "Dr A", "Screening Panel", "Reviewed", "Aetna", "111", "2026-05-10", "2026-06-01", "2026-06-02", "Amphetamine", "POS", "F11.20", "No", "100"],
        ]
    )
    later = _process_orders(client, second, "orders-b.xlsx")
    with SessionLocal() as session:
        observations = session.query(UdsObservation).filter(UdsObservation.report_id == "100").all()
        assert len(observations) == 2
        current = [item for item in observations if item.is_current]
        assert len(current) == 1
        assert current[0].result_date == "2026-06-01"
        assert current[0].import_id.hex != finished["id"].replace("-", "")
        kept = session.query(UdsLine).filter(UdsLine.observation_id == observations[0].id).count()
        assert kept >= 1
    assert later["report"]["uds_order_count"] == 1


def test_confirmation_links_stay_suggestions_until_accepted(client):
    client.post(
        "/api/uds/mappings",
        files={"file": ("Requirements Document.xlsx", _mapping(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    payload = _orders(
        [
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Amphetamine", "POS", "F11.20", "No", "100"],
            ["Main Clinic", "Dr A", "Confirmation Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Amphetamine", "POS", "F11.20", "No", "300"],
            ["Main Clinic", "Dr A", "Confirmation Panel", "Open", "Aetna", "111", "2026-06-01", "2026-06-02", "2026-06-03", "Amphetamine", "POS", "F11.20", "No", "301"],
            ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Amphetamine", "POS", "F11.20", "Yes", "400"],
        ]
    )
    finished = _process_orders(client, payload, "links.xlsx")
    review = client.get("/api/uds/review").json()
    suggestions = [link for link in review["suggested_links"] if link["screening_report_id"] == "100"]
    assert len(suggestions) == 1
    assert suggestions[0]["confirmation_report_id"] == "300"
    assert suggestions[0]["status"] == "suggested"
    summary = client.get("/api/uds/summary", params={"import_id": finished["id"], "current_only": False}).json()
    assert summary["conversion"]["denominator"] == 1
    assert summary["conversion"]["numerator"] == 0
    assert summary["conversion"]["rate"] == 0
    assert summary["billing_excluded_cancelled"] == 1
    accepted = client.post(
        f"/api/uds/links/{suggestions[0]['id']}",
        json={"status": "accepted", "reason": "Same encounter, one candidate.", "actor": "reviewer"},
    )
    assert accepted.status_code == 200
    after = client.get("/api/uds/summary", params={"import_id": finished["id"], "current_only": False}).json()
    assert after["conversion"]["numerator"] == 1
    assert after["conversion"]["rate"] == 1
    empty = client.get("/api/uds/summary", params={"import_id": finished["id"], "lifecycle": "cancelled"}).json()
    assert empty["conversion"]["display"] == "N/A"


def test_repeat_import_without_a_later_date_is_an_identity_conflict(client):
    client.post(
        "/api/uds/mappings",
        files={"file": ("Requirements Document.xlsx", _mapping(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    row = ["Main Clinic", "Dr A", "Screening Panel", "Open", "Aetna", "111", "2026-05-10", "2026-05-11", "2026-05-12", "Amphetamine", "NEG", "F11.20", "No", "100"]
    _process_orders(client, _orders([row]), "same-a.xlsx")
    _process_orders(client, _orders([row]), "same-b.xlsx", acknowledge="true")
    quality_codes = []
    for item in client.get("/api/uds/orders", params={"current_only": False}).json()["orders"]:
        if item["report_id"] == "100":
            quality_codes.append(item["identity_conflict"])
    assert quality_codes == [True, True]


def test_generic_workbook_is_not_an_order_profile(client):
    sample = ROOT / "samples" / "synthetic_demo.xlsx"
    uploaded = client.post(
        "/api/imports",
        files={"file": ("synthetic_demo.xlsx", sample.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"acknowledge_duplicate": "false"},
    )
    preview = client.get(f"/api/imports/{uploaded.json()['id']}/preview", params={"sheet": "Orders"}).json()
    assert preview["order_details_profile"] is False
