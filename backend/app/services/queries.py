import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from sqlalchemy import Numeric, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Import, ImportRow
from app.services.parsing import ADDITIVE_TYPES, NUMERIC_TYPES, decimal_to_canonical

_COLUMN_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class QueryError(Exception):
    pass


@dataclass
class FilterSet:
    search: str | None = None
    date_column: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    columns: list[dict[str, Any]] = field(default_factory=list)

    def echo(self) -> dict:
        return {
            "search": self.search,
            "date_column": self.date_column,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "columns": self.columns,
        }


def parse_filter_tokens(
    search: str | None,
    date_column: str | None,
    date_from: str | None,
    date_to: str | None,
    tokens: list[str] | None,
) -> FilterSet:
    filters = FilterSet(
        search=(search or "").strip() or None,
        date_column=date_column or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )
    if filters.search and len(filters.search) > 100:
        raise QueryError("Search text is limited to 100 characters.")
    for label, value in (("date_from", filters.date_from), ("date_to", filters.date_to)):
        if value and not _DATE.fullmatch(value):
            raise QueryError(f"{label} must be YYYY-MM-DD.")
    if filters.date_column and not _COLUMN_NAME.fullmatch(filters.date_column):
        raise QueryError("Unknown date column.")
    for token in tokens or []:
        parts = token.split(":", 2)
        if len(parts) != 3:
            raise QueryError("Each filter must look like column:operator:value.")
        column, op, raw_value = parts
        if not _COLUMN_NAME.fullmatch(column):
            raise QueryError("Unknown filter column.")
        if op not in {"eq", "in", "gte", "lte", "contains"}:
            raise QueryError("Unsupported filter operator.")
        if op == "in":
            values = [item for item in raw_value.split("|") if item != ""]
            if not values:
                raise QueryError("An in-filter needs at least one value.")
            filters.columns.append({"column": column, "op": op, "values": values})
        else:
            filters.columns.append({"column": column, "op": op, "value": raw_value})
    return filters


def included_sheet(job: Import, sheet_name: str) -> dict:
    for sheet in (job.configuration or {}).get("sheets", []):
        if sheet.get("name") == sheet_name and sheet.get("include"):
            return sheet
    raise QueryError("That sheet is not part of this import.")


def column_map(sheet: dict) -> dict[str, dict]:
    mapping = {}
    for column in sheet.get("columns", []):
        name = column["normalized_name"]
        if not _COLUMN_NAME.fullmatch(name):
            raise QueryError("A configured column name is not valid.")
        mapping[name] = column
    return mapping


def apply_filters(stmt, filters: FilterSet, columns: dict[str, dict]):
    if filters.search:
        term = f"%{_escape_like(filters.search)}%"
        clauses = [ImportRow.transformed[name].as_string().ilike(term, escape="\\") for name in columns]
        if clauses:
            stmt = stmt.where(or_(*clauses))
    if filters.date_from or filters.date_to:
        if not filters.date_column or filters.date_column not in columns:
            raise QueryError("Choose a date column that belongs to this sheet.")
        if columns[filters.date_column]["type"] not in {"date", "datetime"}:
            raise QueryError("The date range column is not a date.")
        day = func.substr(ImportRow.transformed[filters.date_column].as_string(), 1, 10)
        if filters.date_from:
            stmt = stmt.where(day >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(day <= filters.date_to)
    for item in filters.columns:
        name = item["column"]
        if name not in columns:
            raise QueryError("A filter refers to a column that is not on this sheet.")
        kind = columns[name]["type"]
        op = item["op"]
        expr = ImportRow.transformed[name].as_string()
        if op == "contains":
            if kind in NUMERIC_TYPES | {"date", "datetime", "boolean"}:
                raise QueryError("Contains filters apply to text and identifier columns.")
            stmt = stmt.where(expr.ilike(f"%{_escape_like(str(item['value']))}%", escape="\\"))
        elif op == "in":
            stmt = stmt.where(expr.in_(item["values"]))
        elif op == "eq":
            if kind in NUMERIC_TYPES:
                stmt = stmt.where(cast(expr, Numeric) == _decimal(item["value"]))
            else:
                stmt = stmt.where(expr == str(item["value"]))
        elif op in {"gte", "lte"}:
            if kind in NUMERIC_TYPES:
                compare = cast(expr, Numeric)
                number = _decimal(item["value"])
                stmt = stmt.where(compare >= number if op == "gte" else compare <= number)
            elif kind in {"date", "datetime"}:
                if not _DATE.fullmatch(str(item["value"])):
                    raise QueryError("Date filters must use YYYY-MM-DD.")
                day = func.substr(expr, 1, 10)
                stmt = stmt.where(day >= item["value"] if op == "gte" else day <= item["value"])
            else:
                raise QueryError("Range filters apply to numeric and date columns.")
    return stmt


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QueryError("Filter value is not a number.") from exc


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def base_statement(job: Import, sheet_name: str, filters: FilterSet, statuses: tuple[str, ...]):
    sheet = included_sheet(job, sheet_name)
    columns = column_map(sheet)
    stmt = select(ImportRow).where(
        ImportRow.import_id == job.id,
        ImportRow.sheet_name == sheet_name,
        ImportRow.status.in_(statuses),
    )
    return apply_filters(stmt, filters, columns), columns, sheet


def list_rows(
    session: Session,
    job: Import,
    sheet_name: str,
    filters: FilterSet,
    *,
    page: int,
    page_size: int,
    sort: str,
    direction: str,
) -> dict:
    if page < 1 or page_size < 1 or page_size > 100:
        raise QueryError("Page size must be between 1 and 100.")
    if direction not in {"asc", "desc"}:
        raise QueryError("Sort direction must be asc or desc.")
    stmt, columns, sheet = base_statement(job, sheet_name, filters, ("accepted",))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = _sort_expression(sort, columns)
    ordered = stmt.order_by(order.asc().nulls_last() if direction == "asc" else order.desc().nulls_last(), ImportRow.source_row.asc())
    rows = session.scalars(ordered.offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "import_id": str(job.id),
        "filename": job.original_filename,
        "sheet": sheet_name,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "sort": sort,
        "direction": direction,
        "filters": filters.echo(),
        "columns": [_public_column(column) for column in sheet["columns"]],
        "rows": [
            {
                "source_row": row.source_row,
                "status": row.status,
                "values": row.transformed,
                "raw": row.raw,
                "issues": row.issues,
            }
            for row in rows
        ],
    }


def _sort_expression(sort: str, columns: dict[str, dict]):
    if sort == "source_row":
        return ImportRow.source_row
    if sort not in columns:
        raise QueryError("Sort column is not on this sheet.")
    if columns[sort]["type"] in NUMERIC_TYPES:
        return cast(ImportRow.transformed[sort].as_string(), Numeric)
    return ImportRow.transformed[sort].as_string()


def _public_column(column: dict) -> dict:
    return {
        "index": column["index"],
        "original_name": column["original_name"],
        "normalized_name": column["normalized_name"],
        "type": column["type"],
        "additive": column["type"] in ADDITIVE_TYPES,
        "date_format": column.get("date_format"),
    }


def aggregate(
    session: Session,
    job: Import,
    sheet_name: str,
    filters: FilterSet,
    *,
    metric: str,
    aggregation: str,
    group_by: str | None,
) -> dict:
    stmt, columns, _sheet = base_statement(job, sheet_name, filters, ("accepted",))
    if aggregation not in {"count", "sum", "avg", "min", "max"}:
        raise QueryError("Aggregation must be count, sum, avg, min, or max.")
    metric_label = "Rows"
    unit = None
    additive = False
    if metric == "_count":
        if aggregation != "count":
            raise QueryError("Row count only supports the count aggregation.")
    else:
        if metric not in columns:
            raise QueryError("Metric column is not on this sheet.")
        kind = columns[metric]["type"]
        metric_label = columns[metric]["original_name"]
        additive = kind in ADDITIVE_TYPES
        if kind == "percentage":
            unit = "fraction"
        if aggregation == "sum" and not additive:
            raise QueryError(
                "This column is not additive. Sum is refused for identifiers, percentages, dates, text, and booleans. Choose count, average, min, or max when the type allows it."
            )
        if aggregation != "count" and kind not in NUMERIC_TYPES:
            raise QueryError("This aggregation needs a numeric column, or use row count.")
        if aggregation == "count":
            pass
    row_count = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    value, non_null = _scalar_aggregate(session, stmt, metric, aggregation, columns)
    groups, truncated = _grouped(session, stmt, metric, aggregation, group_by, columns)
    group_label = None
    if group_by:
        if group_by not in columns:
            raise QueryError("Group column is not on this sheet.")
        group_label = columns[group_by]["original_name"]
    return {
        "import_id": str(job.id),
        "filename": job.original_filename,
        "sheet": sheet_name,
        "metric": metric,
        "metric_label": metric_label,
        "aggregation": aggregation,
        "value": value,
        "unit": unit,
        "additive": additive,
        "row_count": row_count,
        "non_null_count": non_null,
        "filters": filters.echo(),
        "group_by": group_by,
        "group_by_label": group_label,
        "groups": groups,
        "groups_truncated": truncated,
        "note": "The headline value uses every filtered row. Chart groups are limited to 40 buckets.",
    }


def _filtered_ids(stmt):
    return stmt.with_only_columns(ImportRow.id)


def _scalar_aggregate(session: Session, stmt, metric: str, aggregation: str, columns: dict[str, dict]) -> tuple[str | None, int]:
    del columns
    ids = _filtered_ids(stmt)
    if metric == "_count":
        count = int(session.scalar(select(func.count()).select_from(ids.subquery())) or 0)
        return str(count), count
    numeric = cast(ImportRow.transformed[metric].as_string(), Numeric)
    scoped = ImportRow.id.in_(ids)
    non_null = int(session.scalar(select(func.count(numeric)).where(scoped)) or 0)
    if aggregation == "count":
        return str(non_null), non_null
    function = {"sum": func.sum, "avg": func.avg, "min": func.min, "max": func.max}[aggregation]
    raw = session.scalar(select(function(numeric)).where(scoped))
    if raw is None:
        return None, non_null
    return decimal_to_canonical(Decimal(str(raw))), non_null


def _grouped(session: Session, stmt, metric: str, aggregation: str, group_by: str | None, columns: dict[str, dict]):
    if not group_by:
        return [], False
    if group_by not in columns:
        raise QueryError("Group column is not on this sheet.")
    ids = _filtered_ids(stmt)
    key = ImportRow.transformed[group_by].as_string()
    if metric == "_count":
        value_expr = func.count()
    elif aggregation == "count":
        value_expr = func.count(cast(ImportRow.transformed[metric].as_string(), Numeric))
    else:
        numeric = cast(ImportRow.transformed[metric].as_string(), Numeric)
        value_expr = {"sum": func.sum, "avg": func.avg, "min": func.min, "max": func.max}[aggregation](numeric)
    grouped = (
        select(key.label("key"), value_expr.label("value"), func.count().label("rows"))
        .where(ImportRow.id.in_(ids))
        .group_by(key)
    )
    if columns[group_by]["type"] in {"date", "datetime"}:
        grouped = grouped.order_by(key.asc().nulls_last())
    else:
        grouped = grouped.order_by(value_expr.desc().nulls_last(), key.asc().nulls_last())
    records = session.execute(grouped.limit(41)).all()
    truncated = len(records) > 40
    groups = []
    for record in records[:40]:
        raw_value = record.value
        if raw_value is None:
            rendered = None
        elif metric == "_count" or aggregation == "count":
            rendered = str(int(raw_value))
        else:
            rendered = decimal_to_canonical(Decimal(str(raw_value)))
        groups.append({"key": record.key if record.key is not None else "(empty)", "value": rendered, "rows": int(record.rows)})
    return groups, truncated


def profile(session: Session, job: Import, sheet_name: str) -> dict:
    sheet = included_sheet(job, sheet_name)
    columns = column_map(sheet)
    rows = session.scalars(
        select(ImportRow.transformed).where(
            ImportRow.import_id == job.id,
            ImportRow.sheet_name == sheet_name,
            ImportRow.status == "accepted",
        )
    ).all()
    frame = pd.DataFrame([row or {} for row in rows]) if rows else pd.DataFrame(columns=list(columns))
    described = []
    for column in sheet["columns"]:
        name = column["normalized_name"]
        series = frame[name] if name in frame.columns else pd.Series(dtype=object)
        values = [value for value in series.tolist() if not pd.isna(value) and value != ""]
        entry: dict[str, Any] = {
            **_public_column(column),
            "null_count": int(len(series) - len(values)) if len(frame) else 0,
            "distinct_count": int(pd.Series(values).nunique()) if values else 0,
            "distinct_values": [],
            "min": None,
            "max": None,
        }
        if entry["distinct_count"] <= 25 and column["type"] in {"text", "identifier", "boolean"}:
            counts = pd.Series(values).value_counts().head(25)
            entry["distinct_values"] = [{"value": str(index), "count": int(count)} for index, count in counts.items()]
        if values and column["type"] in NUMERIC_TYPES:
            numbers = []
            for value in values:
                try:
                    number = Decimal(str(value))
                except InvalidOperation:
                    continue
                if number.is_finite():
                    numbers.append(number)
            if numbers:
                entry["min"] = decimal_to_canonical(min(numbers))
                entry["max"] = decimal_to_canonical(max(numbers))
        if values and column["type"] in {"date", "datetime"}:
            ordered = sorted(str(value) for value in values)
            entry["min"] = ordered[0][:10]
            entry["max"] = ordered[-1][:10]
        described.append(entry)
    return {
        "import_id": str(job.id),
        "filename": job.original_filename,
        "sheet": sheet_name,
        "accepted_rows": len(rows),
        "columns": described,
        "date_columns": [column["normalized_name"] for column in described if column["type"] in {"date", "datetime"}],
        "profile_scope": "All accepted rows on this sheet. Charts, the table, and exports use the active filters.",
    }


def list_issues(
    session: Session,
    job: Import,
    *,
    sheet: str | None,
    severity: str | None,
    code: str | None,
    page: int,
    page_size: int,
) -> dict:
    if page < 1 or page_size < 1 or page_size > 100:
        raise QueryError("Page size must be between 1 and 100.")
    stmt = select(ImportRow).where(ImportRow.import_id == job.id).order_by(ImportRow.sheet_name, ImportRow.source_row)
    if sheet:
        stmt = stmt.where(ImportRow.sheet_name == sheet)
    rows = session.scalars(stmt).all()
    flat = []
    for row in rows:
        for issue in row.issues or []:
            if severity and issue.get("severity") != severity:
                continue
            if code and issue.get("code") != code:
                continue
            column = issue.get("column")
            raw_value = issue.get("raw_value")
            if raw_value is None and column:
                raw_value = (row.raw or {}).get(column)
            processed = (row.transformed or {}).get(column) if column else None
            flat.append(
                {
                    "sheet": row.sheet_name,
                    "source_row": row.source_row,
                    "severity": issue.get("severity"),
                    "code": issue.get("code"),
                    "column": issue.get("original_column") or column,
                    "message": issue.get("message"),
                    "raw_value": raw_value if not isinstance(raw_value, dict) else raw_value.get("formula") or raw_value.get("cached"),
                    "processed_value": processed,
                }
            )
    start = (page - 1) * page_size
    return {
        "total": len(flat),
        "page": page,
        "page_size": page_size,
        "issues": flat[start : start + page_size],
        "scope": sheet or "all selected sheets",
    }


def fetch_matching(session: Session, job: Import, sheet_name: str, filters: FilterSet, statuses: tuple[str, ...]) -> tuple[list[ImportRow], dict[str, dict], dict]:
    stmt, columns, sheet = base_statement(job, sheet_name, filters, statuses)
    rows = list(session.scalars(stmt.order_by(ImportRow.source_row.asc())))
    return rows, columns, sheet


def finite_number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
