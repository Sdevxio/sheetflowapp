import io
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Import
from app.services.jobs import start_worker, stop_worker, storage_root

MAX_BACKUP_BYTES = 512 * 1024 * 1024
_SQLITE_HEADER = b"SQLite format 3\x00"


class BackupError(Exception):
    pass


def sqlite_database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise BackupError("Backup is available for the local SheetFlow database.")
    return Path(url.removeprefix("sqlite:///"))


def export_backup() -> bytes:
    database = sqlite_database_path()
    if not database.is_file():
        raise BackupError("The local database file is missing.")
    storage = storage_root()
    snapshot = _snapshot_database(database)
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "sheetflow.db")
            if storage.is_dir():
                for path in sorted(item for item in storage.rglob("*") if item.is_file()):
                    archive.write(path, "storage/" + path.relative_to(storage).as_posix())
    finally:
        snapshot.unlink(missing_ok=True)
    return buffer.getvalue()


def restore_backup(payload: bytes) -> int:
    if len(payload) > MAX_BACKUP_BYTES:
        raise BackupError("The backup is too large.")
    database = sqlite_database_path()
    _ensure_idle()
    temp = Path(tempfile.mkdtemp(prefix="sheetflow-restore-"))
    try:
        database_copy, files = _extract_archive(payload, temp)
        stop_worker()
        try:
            engine.dispose()
            _replace_data(database, database_copy, files)
        finally:
            start_worker()
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    session = SessionLocal()
    try:
        return int(session.scalar(select(func.count()).select_from(Import)) or 0)
    finally:
        session.close()


def _snapshot_database(database: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="sheetflow-backup-", suffix=".db", delete=False)
    handle.close()
    snapshot = Path(handle.name)
    source = sqlite3.connect(database)
    try:
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return snapshot


def _ensure_idle() -> None:
    session = SessionLocal()
    try:
        busy = session.scalar(
            select(func.count()).select_from(Import).where(Import.status.in_(("queued", "processing")))
        )
        if busy:
            raise BackupError("Wait until processing finishes before restoring a backup.")
    finally:
        session.close()


def _extract_archive(payload: bytes, temp: Path) -> tuple[Path, list[tuple[str, Path]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise BackupError("The backup is not a zip file.") from exc
    expanded = 0
    database_copy: Path | None = None
    files: list[tuple[str, Path]] = []
    with archive:
        infos = archive.infolist()
        if len(infos) > 10_000:
            raise BackupError("The backup contains too many files.")
        for info in infos:
            if info.is_dir():
                continue
            name = _safe_member(info.filename)
            expanded += info.file_size
            if expanded > MAX_BACKUP_BYTES:
                raise BackupError("The backup expands beyond the allowed size.")
            target = temp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if name == "sheetflow.db":
                database_copy = target
            else:
                files.append((str(PurePosixPath(name).relative_to("storage")), target))
    if database_copy is None:
        raise BackupError("The backup does not contain the SheetFlow database.")
    header = database_copy.read_bytes()[:16]
    if header != _SQLITE_HEADER:
        raise BackupError("The backup database is not a SQLite file.")
    return database_copy, files


def _safe_member(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BackupError("The backup contains an unsafe path.")
    if name == "sheetflow.db":
        return name
    if path.parts[0] == "storage" and len(path.parts) >= 2:
        return name
    raise BackupError("The backup contains an unexpected file.")


def _replace_data(database: Path, database_copy: Path, files: list[tuple[str, Path]]) -> None:
    storage = Path(get_settings().storage_dir)
    if database.resolve().is_relative_to(storage.resolve()):
        raise BackupError("The database file must stay outside the upload folder.")
    previous_database = database.with_name("sheetflow.db.previous")
    previous_storage = storage.with_name(storage.name + ".previous")
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        if previous_database.exists():
            previous_database.unlink()
        if database.exists():
            database.replace(previous_database)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(database_copy, database)
        if previous_storage.exists():
            shutil.rmtree(previous_storage)
        if storage.exists():
            storage.replace(previous_storage)
        storage.mkdir(parents=True, exist_ok=True)
        for relative, source in files:
            target = (storage / relative).resolve()
            if not target.is_relative_to(storage.resolve()):
                raise BackupError("The backup contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except Exception:
        if previous_database.exists():
            if database.exists():
                database.unlink()
            previous_database.replace(database)
        if previous_storage.exists():
            if storage.exists():
                shutil.rmtree(storage)
            previous_storage.replace(storage)
        raise
    if previous_database.exists():
        previous_database.unlink()
    if previous_storage.exists():
        shutil.rmtree(previous_storage)
