from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.services.errors import ImportProcessingError
from app.services.parsing import (
    ADDITIVE_TYPES,
    convert_value,
    is_empty,
    is_total_row,
    normalize_header,
    suggest_header_row,
    suggest_type,
)
from app.services.reader import FormulaRef, SheetPayload

TRANSFORMATION_NOTES = [
    "Headers are trimmed and normalized. Original header text is kept alongside the normalized name.",
    "Empty rows are counted and skipped. They are not stored as records.",
    "A row whose first non-empty cell is Total or Grand Total is skipped and reported. It is not treated as data.",
    "Identifiers and text stay text. Leading zeros are preserved when Excel stored the cell as text.",
    "Numbers and dates are parsed with the selected locale. Invalid values are reported and are not replaced with guesses.",
    "Ambiguous numeric dates are parsed with the locale and flagged.",
    "Percent values written with a % sign are stored as fractions. Numeric percent cells are kept as read and are not rescaled.",
    "Percentage, identifier, text, date, and boolean columns are not additive. Sum is refused for them.",
    "Duplicate rows and duplicate identifier keys are flagged and kept.",
    "Sheets are processed separately and are not combined.",
    "Formulas are not recalculated. An .xlsx formula contributes its cached value when Excel saved one. A missing cache is reported and stored as empty.",
    ".xls formula text is not always available. A formula whose stored result is blank is reported as a missing cached value.",
]


@dataclass
class ColumnSpec:
    index: int
    original_name: str
    normalized_name: str
    type: str
    date_format: str | None = None
    required: bool = False

    @property
    def additive(self) -> bool:
        return self.type in ADDITIVE_TYPES

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "original_name": self.original_name,
            "normalized_name": self.normalized_name,
            "type": self.type,
            "date_format": self.date_format,
            "required": self.required,
            "additive": self.additive,
        }


@dataclass
class BuiltRow:
    source_row: int
    status: str
    skip_reason: str | None
    raw: dict
    transformed: dict | None
    issues: list[dict] = field(default_factory=list)


@dataclass
class SheetBuild:
    rows: list[BuiltRow]
    stats: dict


def headers_from_matrix(matrix: list[list[Any]], header_row: int) -> list[dict]:
    if header_row < 1 or header_row > len(matrix):
        raise ImportProcessingError("The header row is outside the rows present in this sheet.")
    header = list(matrix[header_row - 1])
    width = max((len(row) for row in matrix), default=len(header))
    if len(header) < width:
        header.extend([None] * (width - len(header)))
    used: set[str] = set()
    columns = []
    for index, value in enumerate(header):
        original = str(value).strip() if isinstance(value, str) and value.strip() else (str(value).strip() if value is not None and not is_empty(value) else f"Column {index + 1}")
        columns.append(
            {
                "index": index,
                "original_name": original,
                "normalized_name": normalize_header(original, used),
            }
        )
    return columns


def suggest_columns(matrix: list[list[Any]], header_row: int, locale: str) -> list[dict]:
    del locale
    specs = headers_from_matrix(matrix, header_row)
    samples: dict[int, list[Any]] = {column["index"]: [] for column in specs}
    for row in matrix[header_row : header_row + 200]:
        if is_total_row(row):
            continue
        for column in specs:
            if column["index"] < len(row):
                samples[column["index"]].append(row[column["index"]])
    for column in specs:
        kind, mixed = suggest_type(samples[column["index"]])
        column["suggested_type"] = kind
        column["mixed"] = mixed
        column["additive"] = kind in ADDITIVE_TYPES
        column["samples"] = [_display(value) for value in samples[column["index"]] if not is_empty(value)][:5]
    return specs


def _display(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_sheet(sheet: SheetPayload, header_row: int, columns: list[ColumnSpec], locale: str) -> SheetBuild:
    if sheet.exceeds_row_limit:
        raise ImportProcessingError(
            f"Sheet {sheet.name} exceeds the row limit. The import was rejected and no rows were saved. Records were not truncated."
        )
    if sheet.exceeds_column_limit:
        raise ImportProcessingError(
            f"Sheet {sheet.name} exceeds the column limit. The import was rejected and no columns were dropped."
        )
    matrix = sheet.matrix
    if header_row < 1 or header_row > max(len(matrix), 1):
        raise ImportProcessingError(f"Sheet {sheet.name} does not contain header row {header_row}.")
    actual = headers_from_matrix(matrix, header_row) if matrix else []
    _validate_columns(sheet.name, actual, columns)
    data_rows = matrix[header_row:]
    built: list[BuiltRow] = []
    skipped_empty = 0
    for offset, row in enumerate(data_rows):
        source_row = header_row + 1 + offset
        padded = list(row) + [None] * max(0, _width(columns) - len(row))
        if all(is_empty(value) for value in padded[: max(_width(columns), len(row))]):
            skipped_empty += 1
            continue
        if is_total_row(padded):
            raw = _raw_record(padded, columns, source_row, sheet.formulas)
            built.append(
                BuiltRow(
                    source_row=source_row,
                    status="skipped",
                    skip_reason="total_row",
                    raw=raw,
                    transformed=None,
                    issues=[
                        {
                            "severity": "warning",
                            "code": "TOTAL_ROW_SKIPPED",
                            "message": "Row skipped because its first non-empty cell is Total or Grand Total.",
                            "column": None,
                            "original_column": None,
                            "source_row": source_row,
                        }
                    ],
                )
            )
            continue
        raw: dict[str, Any] = {}
        transformed: dict[str, Any] = {}
        issues: list[dict] = []
        for column in columns:
            cell = padded[column.index] if column.index < len(padded) else None
            formula = sheet.formulas.get((source_row, column.index + 1))
            raw[column.normalized_name] = _raw_cell(cell, formula)
            if formula and (not formula.cached or formula.error):
                code = "FORMULA_ERROR" if formula.error else "FORMULA_NO_CACHE"
                message = (
                    "Formula cell has an Excel error result and was not recalculated."
                    if formula.error
                    else "Formula cell has no cached result. The formula was not recalculated."
                )
                if formula.formula is None and not formula.error:
                    message = "Formula cell has no cached result. The .xls reader did not expose the formula text, and the formula was not recalculated."
                issues.append(
                    {
                        "severity": "warning",
                        "code": code,
                        "message": message,
                        "column": column.normalized_name,
                        "original_column": column.original_name,
                        "source_row": source_row,
                        "raw_value": formula.formula,
                    }
                )
                transformed[column.normalized_name] = None
                continue
            value, cell_issues = convert_value(cell, column.as_dict(), locale)
            for issue in cell_issues:
                issue["source_row"] = source_row
            issues.extend(cell_issues)
            transformed[column.normalized_name] = value
            if column.required and value in (None, "") and not any(issue["severity"] == "error" and issue.get("column") == column.normalized_name for issue in cell_issues):
                issues.append(
                    {
                        "severity": "error",
                        "code": "MISSING_REQUIRED",
                        "message": f"{column.original_name} is required and this cell is empty.",
                        "column": column.normalized_name,
                        "original_column": column.original_name,
                        "source_row": source_row,
                        "raw_value": None,
                    }
                )
        status = "rejected" if any(issue["severity"] == "error" for issue in issues) else "accepted"
        built.append(BuiltRow(source_row=source_row, status=status, skip_reason=None, raw=raw, transformed=transformed, issues=issues))
    _flag_duplicates(built, columns)
    accepted = sum(1 for row in built if row.status == "accepted")
    rejected = sum(1 for row in built if row.status == "rejected")
    skipped_explicit = sum(1 for row in built if row.status == "skipped")
    inspected = len(data_rows)
    if inspected != skipped_empty + skipped_explicit + rejected + accepted:
        raise ImportProcessingError("Row counts did not reconcile, so the import was not saved.")
    warnings = [issue for row in built for issue in row.issues if issue["severity"] == "warning"]
    errors = [issue for row in built for issue in row.issues if issue["severity"] == "error"]
    duplicate_rows = sorted({issue["source_row"] for row in built for issue in row.issues if issue["code"] in {"DUPLICATE_ROW", "DUPLICATE_IDENTIFIER"}})
    formula_cells = [ref for (row, _col), ref in sheet.formulas.items() if row > header_row]
    sheet_warnings = []
    if sheet.merged_ranges:
        shown = ", ".join(sheet.merged_ranges[:5])
        extra = f" (+{len(sheet.merged_ranges) - 5} more)" if len(sheet.merged_ranges) > 5 else ""
        sheet_warnings.append(
            f"Merged cells are present ({shown}{extra}). Only the top-left cell of each merge is read. Other cells in the merge stay empty."
        )
    if sheet.merge_warning:
        sheet_warnings.append(sheet.merge_warning)
    stats = {
        "physical_rows": len(matrix),
        "physical_rows_exact": sheet.physical_rows_exact,
        "header_row": header_row,
        "preamble_rows": header_row - 1,
        "header_rows": 1,
        "data_rows_inspected": inspected,
        "skipped_empty": skipped_empty,
        "skipped_explicit": skipped_explicit,
        "rejected": rejected,
        "accepted": accepted,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "duplicate_candidate_rows": len(duplicate_rows),
        "duplicate_source_rows": duplicate_rows[:200],
        "formula_cells": len(formula_cells),
        "formula_missing_cache": sum(1 for ref in formula_cells if not ref.cached or ref.error),
        "merged_count": len(sheet.merged_ranges),
        "merged_ranges": sheet.merged_ranges[:50],
        "sheet_warnings": sheet_warnings,
        "columns": [column.as_dict() for column in columns],
        "transformations": list(TRANSFORMATION_NOTES),
    }
    return SheetBuild(rows=built, stats=stats)


def _width(columns: list[ColumnSpec]) -> int:
    return max((column.index for column in columns), default=-1) + 1


def _validate_columns(sheet_name: str, actual: list[dict], columns: list[ColumnSpec]) -> None:
    if not columns:
        raise ImportProcessingError(f"Sheet {sheet_name} has no configured columns.")
    actual_by_index = {column["index"]: column["original_name"] for column in actual}
    seen = set()
    for column in columns:
        if column.index in seen:
            raise ImportProcessingError(f"Sheet {sheet_name} repeats a column index.")
        seen.add(column.index)
        if column.index not in actual_by_index:
            raise ImportProcessingError(f"Sheet {sheet_name} does not have column index {column.index}.")
        if actual_by_index[column.index] != column.original_name:
            raise ImportProcessingError(
                f"Sheet {sheet_name} header at column {column.index + 1} does not match the saved configuration. Preview the sheet again."
            )
        if column.normalized_name != normalize_header(column.original_name, set()):
            # Recomputed name can gain a suffix when earlier columns collide. Compare against the full header instead.
            pass
    expected_names = {column["index"]: column["normalized_name"] for column in actual}
    for column in columns:
        if expected_names.get(column.index) != column.normalized_name:
            raise ImportProcessingError(
                f"Sheet {sheet_name} column {column.original_name} has an unexpected normalized name. Preview the sheet again."
            )
    missing = [index for index in actual_by_index if index not in seen]
    if missing:
        raise ImportProcessingError(
            f"Sheet {sheet_name} has {len(missing)} configured columns missing from the request. No columns were dropped."
        )


def _raw_cell(value: Any, formula: FormulaRef | None) -> Any:
    if formula:
        return {"formula": formula.formula, "cached": _json_safe(value) if formula.cached else None}
    return _json_safe(value)


def _raw_record(row: list[Any], columns: list[ColumnSpec], source_row: int, formulas: dict[tuple[int, int], FormulaRef]) -> dict:
    raw = {}
    for column in columns:
        cell = row[column.index] if column.index < len(row) else None
        raw[column.normalized_name] = _raw_cell(cell, formulas.get((source_row, column.index + 1)))
    return raw


def _json_safe(value: Any) -> Any:
    if is_empty(value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _flag_duplicates(rows: list[BuiltRow], columns: list[ColumnSpec]) -> None:
    accepted = [row for row in rows if row.status == "accepted"]
    if len(accepted) < 2:
        return
    names = [column.normalized_name for column in columns]
    frame = pd.DataFrame([row.transformed for row in accepted]).reindex(columns=names)
    duplicate_mask = frame.duplicated(keep=False)
    identifier_names = [column.normalized_name for column in columns if column.type == "identifier"]
    identifier_mask = None
    if identifier_names:
        identifier_frame = frame[identifier_names]
        identifier_mask = identifier_frame.duplicated(keep=False) & ~identifier_frame.isna().all(axis=1)
    for index, row in enumerate(accepted):
        if bool(duplicate_mask.iloc[index]):
            row.issues.append(
                {
                    "severity": "warning",
                    "code": "DUPLICATE_ROW",
                    "message": "This row matches another accepted row in the same sheet. Neither row was deleted.",
                    "column": None,
                    "original_column": None,
                    "source_row": row.source_row,
                }
            )
        if identifier_mask is not None and bool(identifier_mask.iloc[index]):
            row.issues.append(
                {
                    "severity": "warning",
                    "code": "DUPLICATE_IDENTIFIER",
                    "message": "This identifier matches another accepted row in the same sheet. Neither row was deleted.",
                    "column": identifier_names[0],
                    "original_column": next(column.original_name for column in columns if column.normalized_name == identifier_names[0]),
                    "source_row": row.source_row,
                }
            )


def column_specs_from_config(columns: list[dict]) -> list[ColumnSpec]:
    return [
        ColumnSpec(
            index=int(column["index"]),
            original_name=str(column["original_name"]),
            normalized_name=str(column["normalized_name"]),
            type=str(column["type"]),
            date_format=column.get("date_format") or None,
            required=bool(column.get("required") or False),
        )
        for column in columns
    ]


def suggested_header(sheet: SheetPayload) -> int:
    if not sheet.matrix:
        return 1
    return suggest_header_row(sheet.matrix)


def build_preview(sheet: SheetPayload, header_row: int | None, *, preview_rows: int, cell_chars: int, locale: str) -> dict:
    suggested = suggested_header(sheet)
    selected = header_row or suggested
    if sheet.matrix and not (1 <= selected <= len(sheet.matrix)):
        selected = suggested
    columns = suggest_columns(sheet.matrix, selected, locale) if sheet.matrix and selected <= len(sheet.matrix) else []
    truncated_values = False
    grid = []
    for index, row in enumerate(sheet.matrix[:preview_rows]):
        cells = []
        for value in row:
            text = "" if is_empty(value) else _display(value)
            if len(text) > cell_chars:
                text = text[:cell_chars]
                truncated_values = True
            cells.append(text)
        grid.append({"source_row": index + 1, "cells": cells})
    warnings = []
    if sheet.exceeds_row_limit:
        warnings.append("This sheet exceeds the row limit. Preview shows the first rows only. Processing will reject the sheet and will not truncate the import.")
    if sheet.exceeds_column_limit:
        warnings.append("This sheet exceeds the column limit. Processing will reject the sheet and will not drop columns.")
    if sheet.merged_ranges:
        warnings.append(
            "Merged cells are present. Only the top-left cell of each merge is read. This preview is not the full sheet."
            if len(sheet.matrix) > preview_rows
            else "Merged cells are present. Only the top-left cell of each merge is read."
        )
    if sheet.merge_warning:
        warnings.append(sheet.merge_warning)
    missing_cache = sum(1 for ref in sheet.formulas.values() if not ref.cached or ref.error)
    if missing_cache:
        warnings.append(
            f"{missing_cache} formula cell(s) have no cached result. Formulas are not recalculated. Those cells will be empty unless a cached value exists."
        )
    data_row_count = None
    if sheet.physical_rows_exact and sheet.matrix and 1 <= selected <= len(sheet.matrix):
        data_row_count = len(sheet.matrix) - selected
    return {
        "sheet": sheet.name,
        "physical_rows": len(sheet.matrix) if sheet.physical_rows_exact else None,
        "physical_rows_exact": sheet.physical_rows_exact,
        "rows_read": len(sheet.matrix),
        "column_count": sheet.column_count,
        "exceeds_row_limit": sheet.exceeds_row_limit,
        "exceeds_column_limit": sheet.exceeds_column_limit,
        "preview_row_limit": preview_rows,
        "grid_truncated": len(sheet.matrix) > preview_rows or not sheet.physical_rows_exact,
        "preview_values_truncated": truncated_values,
        "grid": grid,
        "suggested_header_row": suggested,
        "header_row": selected,
        "columns": columns,
        "data_row_count": data_row_count,
        "merged_ranges": sheet.merged_ranges[:50],
        "merged_count": len(sheet.merged_ranges),
        "formula_count": len(sheet.formulas),
        "formula_missing_cache": missing_cache,
        "warnings": warnings,
        "preview_note": "The grid is a preview. Processing reads every data row unless a limit is exceeded, in which case the import is rejected.",
        "order_details_profile": _order_details_headers(columns),
    }


def _order_details_headers(columns: list[dict]) -> bool:
    from app.services.uds import is_order_details

    return is_order_details([column["original_name"] for column in columns])
