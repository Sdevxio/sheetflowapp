import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.logging_config import log_event
from app.models import Import, ImportRow
from app.services.errors import ImportProcessingError, WorkbookReadError
from app.services.reader import SheetPayload, extract_workbook
from app.services.security import path_within
from app.services.transform import ColumnSpec, SheetBuild, build_sheet, column_specs_from_config, suggested_header

log = logging.getLogger("symphony")

_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def storage_root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def summarize_inspection(sheets: list[SheetPayload]) -> dict:
    settings = get_settings()
    return {
        "sheet_count": len(sheets),
        "limits": {
            "max_rows_per_sheet": settings.max_rows_per_sheet,
            "max_columns": settings.max_columns,
            "max_sheets": settings.max_sheets,
            "preview_rows": settings.preview_rows,
        },
        "sheets": [
            {
                "name": sheet.name,
                "position": index,
                "physical_rows": len(sheet.matrix) if sheet.physical_rows_exact else None,
                "rows_read": len(sheet.matrix),
                "physical_rows_exact": sheet.physical_rows_exact,
                "column_count": sheet.column_count,
                "suggested_header_row": suggested_header(sheet),
                "exceeds_row_limit": sheet.exceeds_row_limit,
                "exceeds_column_limit": sheet.exceeds_column_limit,
                "merged_count": len(sheet.merged_ranges),
                "merged_ranges": sheet.merged_ranges[:50],
                "formula_count": len(sheet.formulas),
                "formula_missing_cache": sum(1 for ref in sheet.formulas.values() if not ref.cached or ref.error),
            }
            for index, sheet in enumerate(sheets)
        ],
    }


def load_sheets(job: Import) -> list[SheetPayload]:
    settings = get_settings()
    path = path_within(storage_root(), Path(job.stored_path))
    if not path.exists():
        raise ImportProcessingError("The stored workbook is missing.")
    try:
        return extract_workbook(
            path,
            job.extension,
            max_rows=settings.max_rows_per_sheet,
            max_columns=settings.max_columns,
            max_sheets=settings.max_sheets,
        )
    except WorkbookReadError as exc:
        raise ImportProcessingError(str(exc)) from exc


def assemble_import(job: Import) -> tuple[list[dict], dict]:
    config = job.configuration or {}
    locale = config.get("locale") or job.locale or get_settings().default_locale
    selected = [sheet for sheet in config.get("sheets", []) if sheet.get("include")]
    if not selected:
        raise ImportProcessingError("Select at least one sheet before processing.")
    started = time.perf_counter()
    sheets = {sheet.name: sheet for sheet in load_sheets(job)}
    built: list[tuple[str, SheetBuild]] = []
    for spec in selected:
        name = spec.get("name")
        if name not in sheets:
            raise ImportProcessingError("A selected sheet is no longer in the workbook.")
        columns = column_specs_from_config(spec.get("columns") or [])
        built.append((name, build_sheet(sheets[name], int(spec["header_row"]), columns, locale)))
    duration_ms = int((time.perf_counter() - started) * 1000)
    payloads: list[dict] = []
    stats = {}
    for name, result in built:
        stats[name] = result.stats
        for row in result.rows:
            payloads.append(
                {
                    "import_id": job.id,
                    "sheet_name": name,
                    "source_row": row.source_row,
                    "status": row.status,
                    "skip_reason": row.skip_reason,
                    "raw": row.raw,
                    "transformed": row.transformed,
                    "issues": row.issues,
                }
            )
    report = _report(stats, locale, duration_ms)
    report["issue_groups"] = _issue_groups(payloads)
    return payloads, report


def _report(stats: dict[str, dict], locale: str, duration_ms: int) -> dict:
    keys = [
        "data_rows_inspected",
        "skipped_empty",
        "skipped_explicit",
        "rejected",
        "accepted",
        "warning_count",
        "error_count",
        "duplicate_candidate_rows",
        "formula_cells",
        "formula_missing_cache",
        "preamble_rows",
    ]
    totals = {key: sum(int(sheet.get(key) or 0) for sheet in stats.values()) for key in keys}
    totals["reconciled"] = all(
        sheet["data_rows_inspected"] == sheet["skipped_empty"] + sheet["skipped_explicit"] + sheet["rejected"] + sheet["accepted"]
        for sheet in stats.values()
    )
    return {
        "transform_version": get_settings().transform_version,
        "locale": locale,
        "duration_ms": duration_ms,
        "pipeline": ["upload", "inspect", "configure", "validate", "transform", "save", "summarize"],
        "definitions": {
            "data_rows_inspected": "Rows after the selected header that are present in the sheet.",
            "accepted": "Rows saved for the dashboard.",
            "rejected": "Rows with at least one error. Raw values are kept. The row is excluded from dashboard totals.",
            "skipped_empty": "Rows where every selected cell is empty. Counted, not stored.",
            "skipped_explicit": "Rows whose first non-empty cell is Total or Grand Total. Stored as skipped and excluded from dashboard totals.",
            "preamble_rows": "Rows before the header. They are not data rows.",
        },
        "identity": "data_rows_inspected = accepted + rejected + skipped_empty + skipped_explicit",
        "reconciled": totals["reconciled"],
        "sheets": stats,
        "totals": totals,
    }


def _issue_groups(payloads: list[dict]) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for row in payloads:
        for issue in row.get("issues") or []:
            key = (
                row["sheet_name"],
                issue.get("code"),
                issue.get("original_column") or issue.get("column") or "",
                issue.get("severity"),
            )
            bucket = grouped.get(key)
            if bucket is None:
                bucket = {
                    "sheet": row["sheet_name"],
                    "code": issue.get("code"),
                    "severity": issue.get("severity"),
                    "column": issue.get("original_column") or issue.get("column") or "",
                    "message": issue.get("message"),
                    "count": 0,
                    "source_rows": [],
                }
                grouped[key] = bucket
            bucket["count"] += 1
            if len(bucket["source_rows"]) < 12 and row["source_row"] not in bucket["source_rows"]:
                bucket["source_rows"].append(row["source_row"])
    return sorted(grouped.values(), key=lambda item: (-item["count"], str(item["code"])))


def execute_job(import_id: uuid.UUID) -> None:
    session = SessionLocal()
    try:
        job = session.get(Import, import_id)
        if job is None or job.status != "processing":
            return
        log_event("import_started", "Import processing started.", import_id=str(import_id))
        try:
            payloads, report = assemble_import(job)
        except ImportProcessingError as exc:
            _mark_failed(session, import_id, str(exc))
            return
        except Exception:
            log.exception("import_failed", extra={"import_id": str(import_id), "event": "import_failed"})
            _mark_failed(session, import_id, "Processing failed unexpectedly. The import was rolled back and no dataset was published.")
            return
        try:
            locked = session.scalar(select(Import).where(Import.id == import_id))
            if locked is None or locked.status != "processing":
                session.rollback()
                return
            session.execute(delete(ImportRow).where(ImportRow.import_id == import_id))
            if payloads:
                session.execute(ImportRow.__table__.insert(), payloads)
            session.flush()
            from app.services.uds import build_observations

            order_count = build_observations(session, locked)
            if order_count:
                report = {**report, "uds_order_count": order_count, "counting_unit": "order (distinct Report ID in this import)"}
            finished = datetime.now(timezone.utc)
            locked.status = "completed"
            locked.report = report
            locked.error_message = None
            locked.finished_at = finished
            locked.duration_ms = _duration_ms(locked.started_at, finished, report["duration_ms"])
            session.commit()
            log_event(
                "import_completed",
                "Import processing completed.",
                import_id=str(import_id),
            )
        except Exception:
            session.rollback()
            log.exception("import_save_failed", extra={"import_id": str(import_id), "event": "import_failed"})
            _mark_failed(session, import_id, "Saving the import failed. Partial rows were not published.")
    finally:
        session.close()


def _duration_ms(started: datetime | None, finished: datetime, parse_ms: int) -> int:
    if started is None:
        return parse_ms
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((finished - started).total_seconds() * 1000))


def _mark_failed(session: Session, import_id: uuid.UUID, message: str) -> None:
    session.rollback()
    job = session.get(Import, import_id)
    if job is None:
        return
    session.execute(delete(ImportRow).where(ImportRow.import_id == import_id))
    job.status = "failed"
    job.error_message = message
    job.finished_at = datetime.now(timezone.utc)
    if job.started_at is not None:
        job.duration_ms = _duration_ms(job.started_at, job.finished_at, 0)
    job.report = None
    session.commit()
    log_event("import_failed", message, import_id=str(import_id), level=logging.ERROR)


def recover_interrupted() -> int:
    session = SessionLocal()
    try:
        stuck = list(session.scalars(select(Import).where(Import.status == "processing")))
        for job in stuck:
            session.execute(delete(ImportRow).where(ImportRow.import_id == job.id))
            job.status = "queued"
            job.started_at = None
            job.error_message = None
            job.queued_at = datetime.now(timezone.utc)
        session.commit()
        if stuck:
            log_event("import_requeued", f"Requeued {len(stuck)} interrupted import(s).")
        return len(stuck)
    finally:
        session.close()


def claim_next() -> uuid.UUID | None:
    session = SessionLocal()
    try:
        job = session.scalars(
            select(Import)
            .where(Import.status == "queued")
            .order_by(Import.queued_at.is_(None), Import.queued_at.asc(), Import.created_at.asc())
            .limit(1)
        ).first()
        if job is None:
            session.rollback()
            return None
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.error_message = None
        session.commit()
        return job.id
    except Exception:
        session.rollback()
        log.exception("claim_failed", extra={"event": "claim_failed"})
        return None
    finally:
        session.close()


def _loop() -> None:
    settings = get_settings()
    while not _worker_stop.is_set():
        import_id = claim_next()
        if import_id is None:
            _worker_stop.wait(settings.worker_poll_seconds)
            continue
        try:
            execute_job(import_id)
        except Exception:
            log.exception("worker_crash", extra={"import_id": str(import_id), "event": "import_failed"})


def start_worker() -> None:
    global _worker_thread
    if not get_settings().worker_enabled:
        return
    recover_interrupted()
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_loop, name="import-worker", daemon=True)
    _worker_thread.start()


def stop_worker() -> None:
    _worker_stop.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=2)


def worker_alive() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())
