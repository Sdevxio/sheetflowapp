from datetime import datetime

from app.services.parsing import convert_value, parse_date_value, parse_number, suggest_type
from app.services.security import sanitize_export_value, sanitize_filename


def test_ambiguous_and_unambiguous_dates():
    parsed, issues = parse_date_value("01/02/2024", "en-US", None, "order_date", "Order Date")
    assert parsed == "2024-01-02"
    assert issues[0]["code"] == "AMBIGUOUS_DATE"
    parsed, issues = parse_date_value("01/02/2024", "de-DE", None, "order_date", "Order Date")
    assert parsed == "2024-02-01"
    assert issues[0]["code"] == "AMBIGUOUS_DATE"
    parsed, issues = parse_date_value("13/01/2024", "en-US", None, "order_date", "Order Date")
    assert parsed == "2024-01-13"
    assert issues == []
    parsed, issues = parse_date_value("2024-03-01", "en-US", None, "order_date", "Order Date")
    assert parsed == "2024-03-01"
    assert issues == []
    parsed, issues = parse_date_value("not-a-date", "en-US", None, "order_date", "Order Date")
    assert parsed is None
    assert issues[0]["code"] == "INVALID_DATE"
    parsed, issues = parse_date_value(datetime(2024, 5, 1, 15, 30), "en-US", None, "order_date", "Order Date")
    assert parsed == "2024-05-01T15:30:00"


def test_locale_numbers_and_rejected_values():
    assert parse_number("1,234.50", "en-US") == parse_number("1234.50", "en-US")
    assert str(parse_number("1.234,50", "de-DE")) == "1234.50"
    assert parse_number("n/a", "en-US") is None
    column = {"normalized_name": "amount", "original_name": "Amount", "type": "integer"}
    value, issues = convert_value("10.5", column, "en-US")
    assert value is None
    assert issues[0]["code"] == "INVALID_INTEGER"
    column["type"] = "identifier"
    value, issues = convert_value("00123", column, "en-US")
    assert value == "00123"
    assert issues == []
    value, issues = convert_value(123, column, "en-US")
    assert value == "123"
    assert issues[0]["code"] == "IDENTIFIER_STORED_AS_NUMBER"


def test_percentage_fraction_and_type_suggestion():
    column = {"normalized_name": "tax_rate", "original_name": "Tax Rate", "type": "percentage"}
    value, issues = convert_value("8%", column, "en-US")
    assert value == "0.08"
    assert issues == []
    kind, mixed = suggest_type(["00123", "00456"])
    assert kind == "identifier"
    assert mixed is False
    kind, mixed = suggest_type([10.5, 20, "n/a", 5, 10.5])
    assert kind == "decimal"
    assert mixed is True


def test_filename_and_formula_injection_guards():
    assert sanitize_filename("../../etc/passwd.xlsx") == "passwd.xlsx"
    assert sanitize_filename("..") == "upload"
    assert sanitize_export_value("=1+1") == "'=1+1"
    assert sanitize_export_value("+1") == "'+1"
    assert sanitize_export_value("@sum") == "'@sum"
    assert sanitize_export_value(-3) == -3
    assert sanitize_export_value(10.5) == 10.5
