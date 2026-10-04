import csv
import io
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models import Import
from app.services.parsing import NUMERIC_TYPES
from app.services.queries import FilterSet, fetch_matching
from app.services.security import sanitize_export_value

_NUMBER = __import__("re").compile(r"^-?\d+(\.\d+)?$")


def export_rows(session: Session, job: Import, sheet_name: str, filters: FilterSet, fmt: str) -> tuple[bytes, str, str]:
    rows, _columns, sheet = fetch_matching(session, job, sheet_name, filters, ("accepted",))
    columns = sheet["columns"]
    headers = ["source_row", *[column["original_name"] for column in columns]]
    records = []
    for row in rows:
        values = row.transformed or {}
        records.append([row.source_row, *[values.get(column["normalized_name"]) for column in columns]])
    filename = f"{job.id}-{_slug(sheet_name)}-data.{fmt}"
    if fmt == "csv":
        return _csv(headers, records, columns), "text/csv; charset=utf-8", filename
    return _xlsx(headers, records, columns, "Processed data"), _XLSX, filename


def export_issues(session: Session, job: Import, sheet_name: str, filters: FilterSet, fmt: str) -> tuple[bytes, str, str]:
    rows, _columns, _sheet = fetch_matching(session, job, sheet_name, filters, ("accepted", "rejected", "skipped"))
    headers = ["scope", "source_row", "severity", "code", "column", "message", "raw_value"]
    records = []
    sheet_stats = ((job.report or {}).get("sheets") or {}).get(sheet_name) or {}
    for warning in sheet_stats.get("sheet_warnings") or []:
        records.append(["sheet", "", "warning", "SHEET_WARNING", "", warning, ""])
    for row in rows:
        for issue in row.issues or []:
            records.append(
                [
                    "row",
                    row.source_row,
                    issue.get("severity", ""),
                    issue.get("code", ""),
                    issue.get("original_column") or issue.get("column") or "",
                    issue.get("message", ""),
                    issue.get("raw_value") or "",
                ]
            )
    filename = f"{job.id}-{_slug(sheet_name)}-issues.{fmt}"
    issue_columns = [{"type": "text"} for _ in headers]
    if fmt == "csv":
        return _csv(headers, records, issue_columns, leading_source=False), "text/csv; charset=utf-8", filename
    return _xlsx(headers, records, issue_columns, "Validation issues", leading_source=False), _XLSX, filename


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _csv(headers: list[str], records: list[list], columns: list[dict], leading_source: bool = True) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for record in records:
        writer.writerow([_csv_value(value, column, index, leading_source) for index, (value, column) in enumerate(zip(record, _column_types(columns, leading_source), strict=False))])
    return buffer.getvalue().encode("utf-8")


def _column_types(columns: list[dict], leading_source: bool) -> list[dict]:
    if leading_source:
        return [{"type": "integer"}, *columns]
    return columns


def _csv_value(value, column: dict, index: int, leading_source: bool) -> object:
    if leading_source and index == 0:
        return value
    if value is None:
        return ""
    if column.get("type") in NUMERIC_TYPES and _NUMBER.fullmatch(str(value)):
        return str(value)
    return sanitize_export_value(value)


def _xlsx(headers: list[str], records: list[list], columns: list[dict], title: str, leading_source: bool = True) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.append(headers)
    types = _column_types(columns, leading_source)
    for record in records:
        excel_row = []
        for index, value in enumerate(record):
            column = types[index] if index < len(types) else {"type": "text"}
            excel_row.append(_xlsx_value(value, column, source_row=leading_source and index == 0))
        sheet.append(excel_row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _xlsx_value(value, column: dict, *, source_row: bool):
    if source_row:
        return value
    if value is None or value == "":
        return None
    if column.get("type") in NUMERIC_TYPES and _NUMBER.fullmatch(str(value)):
        number = Decimal(str(value))
        if abs(number) < Decimal("1e15"):
            return float(number)
    if isinstance(value, bool):
        return value
    return sanitize_export_value(value)


def _slug(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in value).strip("-")
    return cleaned[:40] or "sheet"
