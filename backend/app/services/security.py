import hashlib
import re
from pathlib import Path

from app.services.errors import WorkbookReadError

XLSX_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def sanitize_filename(filename: str | None) -> str:
    raw = filename or "upload"
    name = Path(raw.replace("\\", "/")).name
    name = name.replace("\x00", "")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if name in {"", ".", ".."}:
        name = "upload"
    return name[:180]


def extension_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise WorkbookReadError("Only .xls and .xlsx files can be imported. Macros and other workbook types are rejected.")
    return suffix


def assert_magic(payload: bytes, extension: str) -> None:
    if extension == ".xlsx":
        if not payload.startswith(XLSX_MAGIC):
            raise WorkbookReadError("This file does not look like an .xlsx workbook.")
    elif extension == ".xls":
        if not payload.startswith(XLS_MAGIC):
            raise WorkbookReadError("This file does not look like an .xls workbook.")
    else:
        raise WorkbookReadError("Only .xls and .xlsx files can be imported.")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def path_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise WorkbookReadError("Stored file path is outside the storage directory.")
    return resolved


def sanitize_export_value(value: object) -> object:
    """Keep real numbers numeric. Prefix formula-like text so spreadsheet apps do not execute it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def content_disposition(filename: str) -> str:
    safe = sanitize_filename(filename).replace('"', "")
    return f'attachment; filename="{safe}"'
