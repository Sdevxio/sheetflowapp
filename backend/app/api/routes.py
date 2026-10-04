import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.logging_config import log_event
from app.models import Import, ImportRow
from app.services.backup import BackupError, export_backup, restore_backup
from app.services.errors import ImportProcessingError, WorkbookReadError
from app.services.exporters import export_issues, export_rows
from app.services.jobs import assemble_import, load_sheets, storage_root, summarize_inspection
from app.services.parsing import ALLOWED_DATE_FORMATS, ALLOWED_LOCALES, ALLOWED_TYPES
from app.services.queries import QueryError, aggregate, list_issues, list_rows, parse_filter_tokens, profile
from app.services.reader import extract_workbook
from app.services.security import assert_magic, content_disposition, extension_of, path_within, sanitize_filename, sha256_bytes
from app.services.transform import _validate_columns, build_preview, column_specs_from_config, headers_from_matrix

router = APIRouter(prefix="/api")


class ColumnConfig(BaseModel):
    index: int = Field(ge=0)
    original_name: str = Field(min_length=1, max_length=300)
    normalized_name: str
    type: Literal["text", "identifier", "integer", "decimal", "percentage", "date", "datetime", "boolean"]
    date_format: str | None = None
    required: bool = False

    @field_validator("normalized_name")
    @classmethod
    def normalized(cls, value: str) -> str:
        if not value.replace("_", "a").isalnum() or not value[0].isalpha() and value[0] != "_":
            raise ValueError("Invalid normalized column name.")
        return value

    @field_validator("date_format")
    @classmethod
    def date_fmt(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value not in ALLOWED_DATE_FORMATS:
            raise ValueError("Unsupported date format.")
        return value

    @field_validator("type")
    @classmethod
    def type_ok(cls, value: str) -> str:
        if value not in ALLOWED_TYPES:
            raise ValueError("Unsupported column type.")
        return value


class SheetConfig(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    include: bool
    header_row: int = Field(ge=1, le=1_000_000)
    columns: list[ColumnConfig] = Field(default_factory=list)


class ImportConfig(BaseModel):
    locale: Literal["en-US", "de-DE"]
    sheets: list[SheetConfig] = Field(min_length=1)
    profile: Literal["generic", "order_details"] = "generic"


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    database = "ok"
    try:
        db.execute(select(1))
    except Exception:
        database = "error"
    from app.services.jobs import worker_alive

    worker = "running" if worker_alive() else "stopped"
    status = "ok" if database == "ok" and (worker == "running" or not settings.worker_enabled) else "degraded"
    return {
        "status": status,
        "database": database,
        "worker": worker,
        "transform_version": settings.transform_version,
        "auth": "disabled",
        "access": "private-local",
        "limits": _limits(),
    }


@router.get("/backup")
def download_backup() -> Response:
    try:
        payload = export_backup()
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition("sheetflow-backup.zip")},
    )


@router.post("/backup/restore")
async def restore_saved_backup(file: UploadFile = File(...)) -> dict:
    payload = await file.read(512 * 1024 * 1024 + 1)
    if len(payload) > 512 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The backup is too large.")
    try:
        count = restore_backup(payload)
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"imports": count}


@router.post("/imports", status_code=201)
async def upload_import(
    file: UploadFile = File(...),
    acknowledge_duplicate: str = Form("false"),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    filename = sanitize_filename(file.filename)
    try:
        extension = extension_of(filename)
    except WorkbookReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = await _read_limited(file, settings.max_upload_bytes)
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        assert_magic(payload, extension)
    except WorkbookReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    digest = sha256_bytes(payload)
    existing = list(db.scalars(select(Import.id).where(Import.content_hash == digest).order_by(Import.created_at.desc())))
    acknowledged = acknowledge_duplicate.strip().lower() in {"1", "true", "yes", "on"}
    if existing and not acknowledged:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This file matches an earlier upload. Create another import only if you want a separate copy.",
                "existing_import_ids": [str(item) for item in existing],
            },
        )
    import_id = uuid.uuid4()
    destination = storage_root() / f"{import_id}{extension}"
    destination.write_bytes(payload)
    try:
        sheets = extract_workbook(
            destination,
            extension,
            max_rows=settings.max_rows_per_sheet,
            max_columns=settings.max_columns,
            max_sheets=settings.max_sheets,
        )
    except WorkbookReadError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        destination.unlink(missing_ok=True)
        log_event("inspect_failed", "Workbook inspection failed.", error_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="The workbook could not be read. It may be corrupted, encrypted, or incomplete.") from exc
    now = datetime.now(timezone.utc)
    job = Import(
        id=import_id,
        original_filename=filename,
        stored_path=str(destination),
        extension=extension,
        content_hash=digest,
        byte_size=len(payload),
        status="inspected",
        duplicate_acknowledged=acknowledged,
        related_import_ids=[str(item) for item in existing] if existing else None,
        locale=settings.default_locale,
        transform_version=settings.transform_version,
        inspection=summarize_inspection(sheets),
        created_at=now,
        attempt_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log_event("import_uploaded", "Workbook uploaded and inspected.", import_id=str(job.id))
    body = _public(job)
    body["duplicate_warning"] = bool(existing)
    return body


@router.get("/imports")
def list_imports(db: Session = Depends(get_db)) -> dict:
    jobs = list(db.scalars(select(Import).order_by(Import.created_at.desc())))
    return {"imports": [_summary(job) for job in jobs]}


@router.get("/imports/{import_id}")
def get_import(import_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return _public(_get(db, import_id))


@router.get("/imports/{import_id}/preview")
def preview_import(
    import_id: uuid.UUID,
    sheet: str = Query(min_length=1),
    header_row: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    job = _get(db, import_id)
    settings = get_settings()
    try:
        sheets = load_sheets(job)
    except ImportProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    found = next((item for item in sheets if item.name == sheet), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Sheet not found in this workbook.")
    return build_preview(
        found,
        header_row,
        preview_rows=settings.preview_rows,
        cell_chars=settings.preview_cell_chars,
        locale=job.locale or settings.default_locale,
    )


@router.put("/imports/{import_id}/configuration")
def save_configuration(import_id: uuid.UUID, config: ImportConfig, db: Session = Depends(get_db)) -> dict:
    job = _get(db, import_id)
    if job.status in {"queued", "processing"}:
        raise HTTPException(status_code=409, detail="This import is already processing.")
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="This import is already complete. Upload the file again to create another import.")
    if config.locale not in ALLOWED_LOCALES:
        raise HTTPException(status_code=400, detail="Unsupported locale.")
    included = [sheet for sheet in config.sheets if sheet.include]
    if not included:
        raise HTTPException(status_code=400, detail="Select at least one sheet. Sheets are imported separately and are not combined.")
    try:
        loaded = {sheet.name: sheet for sheet in load_sheets(job)}
    except ImportProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for sheet in included:
        if sheet.name not in loaded:
            raise HTTPException(status_code=400, detail="A selected sheet is not in this workbook.")
        if not sheet.columns:
            raise HTTPException(status_code=400, detail=f"Sheet {sheet.name} needs a column configuration.")
        payload = loaded[sheet.name]
        if payload.exceeds_row_limit or payload.exceeds_column_limit:
            continue
        try:
            actual = headers_from_matrix(payload.matrix, sheet.header_row)
            _validate_columns(sheet.name, actual, column_specs_from_config([column.model_dump() for column in sheet.columns]))
        except ImportProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    job.configuration = config.model_dump()
    job.locale = config.locale
    job.status = "configured"
    job.error_message = None
    db.commit()
    db.refresh(job)
    return _public(job)


@router.post("/imports/{import_id}/review")
def review_import(import_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = _get(db, import_id)
    if job.status in {"queued", "processing"}:
        raise HTTPException(status_code=409, detail="This import is already processing.")
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="This import is already complete.")
    if not job.configuration:
        raise HTTPException(status_code=400, detail="Choose sheets and column types before review.")
    try:
        _payloads, report = assemble_import(job)
    except ImportProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report["review_only"] = True
    job.report = report
    job.status = "reviewed"
    job.error_message = None
    db.commit()
    db.refresh(job)
    return _public(job)


@router.post("/imports/{import_id}/process")
def process_import(import_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = _get(db, import_id)
    if job.status in {"queued", "processing"}:
        return _public(job)
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="This import is already complete. Upload the file again to create another import.")
    if not job.configuration:
        raise HTTPException(status_code=400, detail="Choose sheets and column types before processing.")
    job.status = "queued"
    job.queued_at = datetime.now(timezone.utc)
    job.started_at = None
    job.finished_at = None
    job.duration_ms = None
    job.error_message = None
    job.report = None
    job.attempt_count = (job.attempt_count or 0) + 1
    db.commit()
    db.refresh(job)
    log_event("import_queued", "Import queued.", import_id=str(job.id))
    return _public(job)


@router.delete("/imports/{import_id}", status_code=204)
def delete_import(import_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    job = _get(db, import_id)
    if job.status in {"queued", "processing"}:
        raise HTTPException(status_code=409, detail="Wait until processing finishes before deleting this import.")
    try:
        path = path_within(storage_root(), Path(job.stored_path))
        path.unlink(missing_ok=True)
    except WorkbookReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.execute(delete(ImportRow).where(ImportRow.import_id == job.id))
    db.delete(job)
    db.commit()
    log_event("import_deleted", "Import deleted.", import_id=str(import_id))
    return Response(status_code=204)


@router.get("/imports/{import_id}/issues")
def import_issues(
    import_id: uuid.UUID,
    sheet: str | None = None,
    severity: str | None = None,
    code: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    job = _get(db, import_id)
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Validation records are available after processing completes.")
    try:
        return list_issues(db, job, sheet=sheet, severity=severity, code=code, page=page, page_size=page_size)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imports/{import_id}/rows")
def import_rows(
    import_id: uuid.UUID,
    sheet: str,
    page: int = 1,
    page_size: int = 25,
    sort: str = "source_row",
    direction: str = "asc",
    search: str | None = None,
    date_column: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    filter: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    job = _require_completed(db, import_id)
    try:
        filters = parse_filter_tokens(search, date_column, date_from, date_to, filter)
        return list_rows(db, job, sheet, filters, page=page, page_size=page_size, sort=sort, direction=direction)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imports/{import_id}/aggregate")
def import_aggregate(
    import_id: uuid.UUID,
    sheet: str,
    metric: str = "_count",
    aggregation: str = "count",
    group_by: str | None = None,
    search: str | None = None,
    date_column: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    filter: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    job = _require_completed(db, import_id)
    try:
        filters = parse_filter_tokens(search, date_column, date_from, date_to, filter)
        return aggregate(db, job, sheet, filters, metric=metric, aggregation=aggregation, group_by=group_by or None)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imports/{import_id}/profile")
def import_profile(import_id: uuid.UUID, sheet: str, db: Session = Depends(get_db)) -> dict:
    job = _require_completed(db, import_id)
    try:
        return profile(db, job, sheet)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imports/{import_id}/export")
def import_export(
    import_id: uuid.UUID,
    sheet: str,
    kind: Literal["data", "issues"] = "data",
    fmt: Literal["csv", "xlsx"] = "csv",
    search: str | None = None,
    date_column: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    filter: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> Response:
    job = _require_completed(db, import_id)
    try:
        filters = parse_filter_tokens(search, date_column, date_from, date_to, filter)
        if kind == "issues":
            payload, media, filename = export_issues(db, job, sheet, filters, fmt)
        else:
            payload, media, filename = export_rows(db, job, sheet, filters, fmt)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=payload, media_type=media, headers={"Content-Disposition": content_disposition(filename)})


def _read_limited(file: UploadFile, limit: int):
    async def inner():
        chunks = []
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413, detail=f"File exceeds the {limit} byte upload limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    return inner()


def _limits() -> dict:
    settings = get_settings()
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_rows_per_sheet": settings.max_rows_per_sheet,
        "max_columns": settings.max_columns,
        "max_sheets": settings.max_sheets,
        "preview_rows": settings.preview_rows,
        "preview_cell_chars": settings.preview_cell_chars,
    }


def _get(db: Session, import_id: uuid.UUID) -> Import:
    job = db.get(Import, import_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return job


def _require_completed(db: Session, import_id: uuid.UUID) -> Import:
    job = _get(db, import_id)
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="This import is not a completed dataset. Partial processing results are not available.",
        )
    return job


def _summary(job: Import) -> dict:
    report = job.report or {}
    totals = report.get("totals") or {}
    sheets = [sheet["name"] for sheet in (job.configuration or {}).get("sheets", []) if sheet.get("include")]
    if not sheets:
        sheets = [sheet["name"] for sheet in (job.inspection or {}).get("sheets", [])]
    return {
        "id": str(job.id),
        "original_filename": job.original_filename,
        "extension": job.extension,
        "byte_size": job.byte_size,
        "content_hash": job.content_hash,
        "status": job.status,
        "error_message": job.error_message,
        "created_at": _iso(job.created_at),
        "queued_at": _iso(job.queued_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "duration_ms": job.duration_ms,
        "attempt_count": job.attempt_count,
        "sheets": sheets,
        "accepted_rows": totals.get("accepted"),
        "rejected_rows": totals.get("rejected"),
        "warning_count": totals.get("warning_count"),
        "duplicate_candidate_rows": totals.get("duplicate_candidate_rows"),
        "duplicate_warning": bool(job.related_import_ids),
        "related_import_ids": job.related_import_ids or [],
        "transform_version": job.transform_version,
        "locale": job.locale,
    }


def _public(job: Import) -> dict:
    body = _summary(job)
    body["configuration"] = job.configuration
    body["inspection"] = job.inspection
    body["report"] = job.report
    body["duplicate_acknowledged"] = job.duplicate_acknowledged
    return body


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
