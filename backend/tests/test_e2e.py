import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Import, ImportRow
from app.services.jobs import recover_interrupted

ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "samples" / "synthetic_demo.xlsx"
XLS = ROOT / "samples" / "synthetic_demo.xls"


def _wait(client, import_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/imports/{import_id}").json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def _upload(client, path: Path, acknowledge: str = "false"):
    media = "application/vnd.ms-excel" if path.suffix == ".xls" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    with path.open("rb") as handle:
        return client.post(
            "/api/imports",
            files={"file": (path.name, handle, media)},
            data={"acknowledge_duplicate": acknowledge},
        )


def _configure(client, import_id: str, names: list[str] | None = None) -> dict:
    detail = client.get(f"/api/imports/{import_id}").json()
    selected = names or [sheet["name"] for sheet in detail["inspection"]["sheets"]]
    sheets = []
    for name in selected:
        preview = client.get("/api/imports/" + import_id + "/preview", params={"sheet": name}).json()
        sheets.append(
            {
                "name": name,
                "include": True,
                "header_row": preview["suggested_header_row"],
                "columns": [
                    {
                        "index": column["index"],
                        "original_name": column["original_name"],
                        "normalized_name": column["normalized_name"],
                        "type": column["suggested_type"],
                        "date_format": None,
                    }
                    for column in preview["columns"]
                ],
            }
        )
    response = client.put(f"/api/imports/{import_id}/configuration", json={"locale": "en-US", "sheets": sheets})
    assert response.status_code == 200, response.text
    return response.json()


def _process(client, import_id: str) -> dict:
    response = client.post(f"/api/imports/{import_id}/process")
    assert response.status_code == 200, response.text
    return _wait(client, import_id)


def test_health_reports_database_and_worker(client):
    body = client.get("/api/health").json()
    assert body["database"] == "ok"
    assert body["worker"] == "running"
    assert body["auth"] == "disabled"
    assert body["limits"]["preview_rows"] == 50
    assert body["limits"]["max_rows_per_sheet"] == 50000


def test_rejects_corrupt_and_unsupported_files(client):
    bad = client.post("/api/imports", files={"file": ("notes.csv", b"a,b\n1,2\n", "text/csv")})
    assert bad.status_code == 400
    corrupt = client.post("/api/imports", files={"file": ("broken.xlsx", b"this is not a workbook", "application/octet-stream")})
    assert corrupt.status_code == 400
    mismatch = client.post(
        "/api/imports",
        files={"file": ("wrong.xlsx", XLS.read_bytes(), "application/octet-stream")},
    )
    assert mismatch.status_code == 400
    assert client.get("/api/imports").json()["imports"] == []


def test_repeated_upload_requires_acknowledgement_and_sanitizes_names(client):
    first = _upload(client, XLSX)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["original_filename"] == "synthetic_demo.xlsx"
    assert body["status"] == "inspected"
    stored = Path(body["inspection"] and "")  # placeholder to keep lints quiet
    del stored
    with SessionLocal() as session:
        job = session.get(Import, body["id"])
        assert job is not None
        assert ".." not in job.stored_path
        assert job.stored_path.endswith(f"{job.id}.xlsx")
        assert Path(job.stored_path).is_file()
    again = _upload(client, XLSX)
    assert again.status_code == 409
    assert body["id"] in again.json()["detail"]["existing_import_ids"]
    third = _upload(client, XLSX, acknowledge="true")
    assert third.status_code == 201
    assert third.json()["duplicate_warning"] is True
    renamed = client.post(
        "/api/imports",
        files={"file": ("../../etc/passwd.xlsx", XLSX.read_bytes(), "application/octet-stream")},
        data={"acknowledge_duplicate": "true"},
    )
    assert renamed.status_code == 201, renamed.text
    assert renamed.json()["original_filename"] == "passwd.xlsx"
    sneaky = client.post(
        "/api/imports",
        files={"file": ("broken.xlsx", b"PK\x03\x04not-really", "application/octet-stream")},
    )
    assert sneaky.status_code == 400


def test_end_to_end_dashboard_filters_exports_and_retry(client, monkeypatch):
    uploaded = _upload(client, XLSX)
    import_id = uploaded.json()["id"]
    preview = client.get(f"/api/imports/{import_id}/preview", params={"sheet": "Orders"}).json()
    assert preview["suggested_header_row"] == 3
    assert preview["preview_note"].startswith("The grid is a preview")
    types = {column["original_name"]: column["suggested_type"] for column in preview["columns"]}
    assert types["Account Code"] == "identifier"
    assert types["Amount"] == "decimal"
    assert types["Tax Rate"] == "percentage"
    _configure(client, import_id, ["Orders", "Comments", "Calculations"])
    blocked = client.get(f"/api/imports/{import_id}/rows", params={"sheet": "Orders"})
    assert blocked.status_code == 409

    from app.services import jobs as jobs_module

    real = jobs_module.assemble_import
    calls = {"n": 0}

    def flaky(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(job)

    monkeypatch.setattr(jobs_module, "assemble_import", flaky)
    failed = _process(client, import_id)
    assert failed["status"] == "failed"
    assert "rolled back" in failed["error_message"]
    with SessionLocal() as session:
        count = session.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.import_id == import_id))
        assert count == 0
    monkeypatch.setattr(jobs_module, "assemble_import", real)
    completed = _process(client, import_id)
    assert completed["status"] == "completed", completed
    profile = client.get(f"/api/imports/{import_id}/profile", params={"sheet": "Orders"})
    assert profile.status_code == 200, profile.text
    amount = next(column for column in profile.json()["columns"] if column["normalized_name"] == "amount")
    assert amount["min"] == "5"
    assert amount["max"] == "20"
    assert completed["report"]["reconciled"] is True
    orders = completed["report"]["sheets"]["Orders"]
    assert orders["data_rows_inspected"] == 8
    assert orders["accepted"] == 5
    assert orders["rejected"] == 1
    assert orders["skipped_empty"] == 1
    assert orders["skipped_explicit"] == 1
    assert "Ada Lovelace" not in str(orders)
    rows = client.get(f"/api/imports/{import_id}/rows", params={"sheet": "Orders", "page_size": 100}).json()
    assert rows["total"] == 5
    codes = {row["values"]["account_code"] for row in rows["rows"]}
    assert "00123" in codes
    assert "123" not in codes
    comments = client.get(f"/api/imports/{import_id}/rows", params={"sheet": "Comments", "page_size": 100}).json()
    assert comments["total"] == 3
    assert all(row["values"].get("account_code") is None for row in comments["rows"])

    total = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={"sheet": "Orders", "metric": "amount", "aggregation": "sum"},
    ).json()
    assert total["value"] == "46"
    assert total["row_count"] == 5
    assert total["non_null_count"] == 4
    refused = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={"sheet": "Orders", "metric": "tax_rate", "aggregation": "sum"},
    )
    assert refused.status_code == 400
    grouped = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={"sheet": "Orders", "metric": "_count", "aggregation": "count", "group_by": "status"},
    ).json()
    counts = {item["key"]: item["value"] for item in grouped["groups"]}
    assert counts["open"] == "4"
    assert counts["closed"] == "1"
    filtered = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={"sheet": "Orders", "metric": "amount", "aggregation": "sum", "filter": "status:eq:open"},
    ).json()
    assert filtered["value"] == "26"
    assert filtered["row_count"] == 4
    ranged = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={
            "sheet": "Orders",
            "metric": "amount",
            "aggregation": "sum",
            "date_column": "order_date",
            "date_from": "2024-03-01",
            "date_to": "2024-04-01",
        },
    ).json()
    assert ranged["value"] == "5"
    assert ranged["row_count"] == 2
    table = client.get(
        f"/api/imports/{import_id}/rows",
        params={"sheet": "Orders", "filter": "status:eq:open", "page_size": 100},
    ).json()
    assert table["total"] == filtered["row_count"]
    exported = client.get(
        f"/api/imports/{import_id}/export",
        params={"sheet": "Orders", "kind": "data", "fmt": "csv", "filter": "status:eq:open"},
    )
    assert exported.status_code == 200
    lines = [line for line in exported.text.splitlines() if line]
    assert len(lines) - 1 == table["total"]
    comments_export = client.get(
        f"/api/imports/{import_id}/export",
        params={"sheet": "Comments", "kind": "data", "fmt": "csv"},
    )
    assert "'=1+1" in comments_export.text
    assert "\n=1+1" not in comments_export.text
    issues = client.get(
        f"/api/imports/{import_id}/export",
        params={"sheet": "Orders", "kind": "issues", "fmt": "csv"},
    )
    assert "INVALID_DATE" in issues.text
    assert "DUPLICATE_ROW" in issues.text
    again = client.post(f"/api/imports/{import_id}/process")
    assert again.status_code == 409
    with SessionLocal() as session:
        count = session.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.import_id == import_id))
    assert count == 7 + 3 + 1


def test_xls_import_and_restart_recovery(client):
    uploaded = _upload(client, XLS)
    import_id = uploaded.json()["id"]
    _configure(client, import_id)
    done = _process(client, import_id)
    assert done["status"] == "completed", done
    rows = client.get(f"/api/imports/{import_id}/rows", params={"sheet": "Accounts", "page_size": 100}).json()
    assert rows["rows"][0]["values"]["account_code"] == "00123"
    assert any(issue["code"] == "FORMULA_NO_CACHE" for issue in rows["rows"][0]["issues"])
    with SessionLocal() as session:
        job = session.get(Import, import_id)
        assert job is not None
        job.status = "processing"
        session.add(
            ImportRow(
                import_id=import_id,
                sheet_name="Accounts",
                source_row=999,
                status="accepted",
                raw={},
                issues=[],
            )
        )
        session.commit()
    assert recover_interrupted() == 1
    finished = _wait(client, import_id)
    assert finished["status"] == "completed"
    with SessionLocal() as session:
        bogus = session.scalar(
            select(func.count()).select_from(ImportRow).where(ImportRow.import_id == import_id, ImportRow.source_row == 999)
        )
        total = session.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.import_id == import_id))
    assert bogus == 0
    assert total == 2


def test_row_limit_failure_publishes_nothing(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_rows_per_sheet", 3)
    uploaded = _upload(client, XLSX)
    assert uploaded.status_code == 201, uploaded.text
    import_id = uploaded.json()["id"]
    preview = client.get(f"/api/imports/{import_id}/preview", params={"sheet": "Orders"}).json()
    assert preview["exceeds_row_limit"] is True
    assert "not truncate" in preview["warnings"][0]
    _configure(client, import_id, ["Orders"])
    failed = _process(client, import_id)
    assert failed["status"] == "failed"
    assert "not truncated" in failed["error_message"] or "rejected" in failed["error_message"]
    assert client.get(f"/api/imports/{import_id}/rows", params={"sheet": "Orders"}).status_code == 409
    with SessionLocal() as session:
        count = session.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.import_id == import_id))
    assert count == 0


def test_delete_import(client):
    uploaded = _upload(client, XLS)
    import_id = uploaded.json()["id"]
    _configure(client, import_id)
    assert _process(client, import_id)["status"] == "completed"
    assert client.delete(f"/api/imports/{import_id}").status_code == 204
    assert client.get(f"/api/imports/{import_id}").status_code == 404


def test_file_size_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 32)
    response = _upload(client, XLSX)
    assert response.status_code == 413
