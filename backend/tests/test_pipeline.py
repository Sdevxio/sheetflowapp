from pathlib import Path

from app.services.reader import extract_workbook
from app.services.transform import build_preview, build_sheet, column_specs_from_config

ROOT = Path(__file__).resolve().parents[2]


def _sheets(name: str, extension: str):
    return extract_workbook(ROOT / "samples" / name, extension, max_rows=50_000, max_columns=200, max_sheets=30)


def _build(sheet, locale="en-US"):
    preview = build_preview(sheet, None, preview_rows=50, cell_chars=500, locale=locale)
    columns = column_specs_from_config(
        [
            {
                "index": column["index"],
                "original_name": column["original_name"],
                "normalized_name": column["normalized_name"],
                "type": column["suggested_type"],
                "date_format": None,
            }
            for column in preview["columns"]
        ]
    )
    return preview, build_sheet(sheet, preview["header_row"], columns, locale)


def test_xlsx_orders_reconciliation_and_leading_zeros():
    sheets = {sheet.name: sheet for sheet in _sheets("synthetic_demo.xlsx", ".xlsx")}
    preview, built = _build(sheets["Orders"])
    assert preview["suggested_header_row"] == 3
    assert preview["grid_truncated"] is False
    assert preview["data_row_count"] == 8
    stats = built.stats
    assert stats["data_rows_inspected"] == stats["accepted"] + stats["rejected"] + stats["skipped_empty"] + stats["skipped_explicit"]
    assert stats["accepted"] == 5
    assert stats["rejected"] == 1
    assert stats["skipped_empty"] == 1
    assert stats["skipped_explicit"] == 1
    accepted = {row.source_row: row for row in built.rows if row.status == "accepted"}
    assert accepted[4].transformed["account_code"] == "00123"
    assert accepted[5].transformed["order_date"] == "2024-01-02"
    assert any(issue["code"] == "AMBIGUOUS_DATE" for issue in accepted[5].issues)
    assert accepted[6].transformed["amount"] is None
    assert any(issue["code"] == "DUPLICATE_ROW" for issue in accepted[4].issues)
    assert any(issue["code"] == "DUPLICATE_IDENTIFIER" for issue in accepted[6].issues)
    rejected = next(row for row in built.rows if row.status == "rejected")
    assert rejected.source_row == 7
    assert {issue["code"] for issue in rejected.issues} == {"INVALID_DATE", "INVALID_NUMBER"}
    assert rejected.raw["amount"] == "n/a"
    assert next(row for row in built.rows if row.status == "skipped").skip_reason == "total_row"


def test_formula_cache_is_not_recalculated_and_text_equals_is_preserved():
    sheets = {sheet.name: sheet for sheet in _sheets("synthetic_demo.xlsx", ".xlsx")}
    _preview, calculations = _build(sheets["Calculations"])
    row = calculations.rows[0]
    assert row.transformed["left"] == "2"
    assert row.transformed["right"] == "3"
    assert row.transformed["total"] is None
    assert row.issues[0]["code"] == "FORMULA_NO_CACHE"
    assert row.raw["total"]["formula"] == "=B2+C2"
    assert row.raw["total"]["cached"] is None
    _preview, orders = _build(sheets["Orders"])
    note = next(row for row in orders.rows if row.source_row == 9)
    assert note.transformed["note"] == "=1+1"
    assert all(issue["code"] != "FORMULA_NO_CACHE" for issue in note.issues)


def test_xls_leading_zeros_and_uncached_formula():
    sheet = _sheets("synthetic_demo.xls", ".xls")[0]
    _preview, built = _build(sheet)
    assert built.rows[0].transformed["account_code"] == "00123"
    assert built.rows[0].issues[0]["code"] == "FORMULA_NO_CACHE"
    assert built.rows[0].raw["computed"]["formula"] is None
    assert built.stats["accepted"] == 2


def test_sheets_are_not_combined():
    sheets = {sheet.name: sheet for sheet in _sheets("synthetic_demo.xlsx", ".xlsx")}
    _preview, comments = _build(sheets["Comments"])
    assert [row.transformed["author"] for row in comments.rows] == ["Ada Lovelace", "Grace Hopper", "Sam"]
    assert comments.rows[-1].transformed["comment"] == "=1+1"
