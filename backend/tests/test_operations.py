from pathlib import Path

from test_e2e import _process, _upload, _wait

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "samples" / "operations-september.xlsx"


def _configure_orders(client, import_id: str) -> None:
    preview = client.get(f"/api/imports/{import_id}/preview", params={"sheet": "Orders"}).json()
    columns = []
    for column in preview["columns"]:
        label = column["original_name"].strip().lower()
        kind = "identifier" if label == "order id" else "date" if label == "date" else "text"
        columns.append(
            {
                "index": column["index"],
                "original_name": column["original_name"],
                "normalized_name": column["normalized_name"],
                "type": kind,
                "date_format": None,
                "required": label == "order id",
            }
        )
    saved = client.put(
        f"/api/imports/{import_id}/configuration",
        json={"locale": "en-US", "sheets": [{"name": "Orders", "include": True, "header_row": 1, "columns": columns}]},
    )
    assert saved.status_code == 200, saved.text


def test_operations_workbook_counts_come_from_the_pipeline(client):
    uploaded = _upload(client, OPERATIONS)
    assert uploaded.status_code == 201, uploaded.text
    import_id = uploaded.json()["id"]
    _configure_orders(client, import_id)
    reviewed = client.post(f"/api/imports/{import_id}/review")
    assert reviewed.status_code == 200, reviewed.text
    sheet = reviewed.json()["report"]["sheets"]["Orders"]
    assert sheet["data_rows_inspected"] == 1248
    assert sheet["accepted"] == 1230
    assert sheet["rejected"] == 18
    assert sheet["skipped_empty"] == 0
    assert sheet["skipped_explicit"] == 0
    groups = {item["code"]: item["count"] for item in reviewed.json()["report"]["issue_groups"]}
    assert groups["INVALID_DATE"] == 12
    assert groups["MISSING_REQUIRED"] == 6
    finished = _process(client, import_id)
    assert finished["status"] == "completed"
    saved = finished["report"]["sheets"]["Orders"]
    assert saved["accepted"] == 1230
    assert saved["rejected"] == 18
    summary = client.get(
        f"/api/imports/{import_id}/aggregate",
        params={"sheet": "Orders", "metric": "_count", "aggregation": "count", "group_by": "status"},
    ).json()
    counts = {group["key"]: group["rows"] for group in summary["groups"]}
    assert counts == {"Cancelled": 90, "Completed": 940, "Pending": 200}
    issues = client.get(f"/api/imports/{import_id}/issues", params={"sheet": "Orders", "code": "INVALID_DATE", "page_size": 25}).json()
    assert issues["total"] == 12
    assert _wait(client, import_id)["status"] == "completed"
