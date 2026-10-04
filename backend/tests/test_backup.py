import io
import zipfile

from app.database import SessionLocal
from app.models import Import

from tests.test_e2e import ROOT, _configure, _process, _upload

XLSX = ROOT / "samples" / "synthetic_demo.xlsx"


def test_backup_round_trip_and_refuses_busy_or_unsafe_archives(client):
    uploaded = _upload(client, XLSX)
    import_id = uploaded.json()["id"]
    _configure(client, import_id, ["Orders"])
    done = _process(client, import_id)
    assert done["status"] == "completed", done

    saved = client.get("/api/backup")
    assert saved.status_code == 200, saved.text
    assert saved.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(saved.content)) as archive:
        names = archive.namelist()
    assert "sheetflow.db" in names
    assert any(name.startswith("storage/") and name.endswith(".xlsx") for name in names)

    with SessionLocal() as session:
        job = session.get(Import, import_id)
        assert job is not None
        job.status = "processing"
        session.commit()
    refused = client.post("/api/backup/restore", files={"file": ("sheetflow-backup.zip", saved.content, "application/zip")})
    assert refused.status_code == 400
    assert "processing" in refused.json()["detail"].lower() or "wait" in refused.json()["detail"].lower()
    with SessionLocal() as session:
        job = session.get(Import, import_id)
        job.status = "completed"
        session.commit()

    assert client.delete(f"/api/imports/{import_id}").status_code == 204
    assert client.get(f"/api/imports/{import_id}").status_code == 404
    restored = client.post("/api/backup/restore", files={"file": ("sheetflow-backup.zip", saved.content, "application/zip")})
    assert restored.status_code == 200, restored.text
    assert restored.json()["imports"] == 1
    detail = client.get(f"/api/imports/{import_id}")
    assert detail.status_code == 200
    assert detail.json()["report"]["sheets"]["Orders"]["accepted"] == 5

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../evil.txt", "nope")
    rejected = client.post(
        "/api/backup/restore",
        files={"file": ("bad.zip", unsafe.getvalue(), "application/zip")},
    )
    assert rejected.status_code == 400
    assert client.get(f"/api/imports/{import_id}").status_code == 200
