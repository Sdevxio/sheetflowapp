import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

ALLOWED_TYPES = (
    "text",
    "identifier",
    "integer",
    "decimal",
    "percentage",
    "date",
    "datetime",
    "boolean",
)
ADDITIVE_TYPES = {"integer", "decimal"}
ALLOWED_LOCALES = ("en-US", "de-DE")
ALLOWED_DATE_FORMATS = {
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
}
NUMERIC_TYPES = {"integer", "decimal", "percentage"}
DATE_TYPES = {"date", "datetime"}

_TOTAL_RE = re.compile(r"(grand\s+)?totals?", re.IGNORECASE)
_IDENT_RE = re.compile(r"0\d+")
_DATE_RE = re.compile(r"^(\d{1,2})([/.-])(\d{1,2})\2(\d{2,4})$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$")


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def is_total_row(values: list[Any]) -> bool:
    for value in values:
        if is_empty(value):
            continue
        return isinstance(value, str) and bool(_TOTAL_RE.fullmatch(value.strip()))
    return False


def normalize_header(name: str, used: set[str]) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    if not text or text[0].isdigit():
        text = f"column_{text}" if text else "column"
    base = text
    suffix = 2
    while text in used:
        text = f"{base}_{suffix}"
        suffix += 1
    used.add(text)
    return text


def decimal_to_canonical(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def parse_number(value: Any, locale: str) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "")
    if text == "":
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    for symbol in ("$", "€", "£"):
        text = text.replace(symbol, "")
    if locale == "de-DE":
        if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", text):
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d+(,\d+)?", text):
            text = text.replace(",", ".")
        elif not re.fullmatch(r"\d+(\.\d+)?", text):
            return None
    else:
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", text):
            text = text.replace(",", "")
        elif not re.fullmatch(r"\d+(\.\d+)?", text):
            return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        number = -number
    return number


def _issue(severity: str, code: str, message: str, column: str | None = None, original: str | None = None, raw_value: Any = None) -> dict:
    payload = {"severity": severity, "code": code, "message": message, "column": column, "original_column": original}
    if raw_value is not None and not is_empty(raw_value):
        payload["raw_value"] = str(raw_value)[:200]
    return payload


def _parse_iso(text: str) -> datetime | None:
    match = _ISO_RE.fullmatch(text.strip())
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _year_from_token(token: str) -> int:
    if len(token) == 2:
        return datetime.strptime(token, "%y").year
    return int(token)


def parse_date_value(value: Any, locale: str, explicit_format: str | None, column: str, original: str) -> tuple[str | None, list[dict]]:
    if is_empty(value):
        return None, []
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None)
        if naive.time() == time.min:
            return naive.date().isoformat(), []
        return naive.isoformat(timespec="seconds"), []
    if isinstance(value, date):
        return value.isoformat(), []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        serial = _excel_serial(value)
        if serial is None:
            return None, [_issue("error", "INVALID_DATE", "Numeric value is not a usable Excel date serial.", column, original, value)]
        return serial, [_issue("warning", "EXCEL_SERIAL_DATE", "A number was interpreted as an Excel date serial. Confirm the column type.", column, original, value)]
    text = str(value).strip()
    if explicit_format:
        try:
            parsed = datetime.strptime(text, explicit_format)
        except ValueError:
            return None, [_issue("error", "INVALID_DATE", "Value does not match the selected date format.", column, original, text)]
        return parsed.date().isoformat(), []
    iso = _parse_iso(text)
    if iso is not None:
        if iso.time() == time(0, 0, 0):
            return iso.date().isoformat(), []
        return iso.isoformat(timespec="seconds"), []
    match = _DATE_RE.fullmatch(text)
    if not match:
        return None, [_issue("error", "INVALID_DATE", "Value could not be parsed as a date.", column, original, text)]
    first = int(match.group(1))
    second = int(match.group(3))
    try:
        year = _year_from_token(match.group(4))
    except ValueError:
        return None, [_issue("error", "INVALID_DATE", "Value could not be parsed as a date.", column, original, text)]
    ambiguous = first <= 12 and second <= 12
    if first > 12 and second <= 12:
        day, month = first, second
        ambiguous = False
    elif second > 12 and first <= 12:
        month, day = first, second
        ambiguous = False
    elif locale == "de-DE":
        day, month = first, second
    else:
        month, day = first, second
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None, [_issue("error", "INVALID_DATE", "Value could not be parsed as a date.", column, original, text)]
    issues = []
    if ambiguous:
        issues.append(
            _issue(
                "warning",
                "AMBIGUOUS_DATE",
                f"This date matches more than one calendar order. It was read as {parsed.isoformat()} using locale {locale}.",
                column,
                original,
                text,
            )
        )
    return parsed.isoformat(), issues


def _excel_serial(value: int | float) -> str | None:
    number = float(value)
    if not math.isfinite(number) or number < 1 or number > 60000:
        return None
    whole = int(number)
    fractional = number - whole
    try:
        parsed = datetime(1899, 12, 30) + timedelta(days=whole, seconds=round(fractional * 86400))
    except OverflowError:
        return None
    if parsed.time() == time(0, 0, 0):
        return parsed.date().isoformat()
    return parsed.isoformat(timespec="seconds")


def convert_value(value: Any, column: dict, locale: str) -> tuple[Any, list[dict]]:
    name = column["normalized_name"]
    original = column["original_name"]
    kind = column["type"]
    if is_empty(value):
        return None, []
    if kind == "text":
        return str(value).strip(), []
    if kind == "identifier":
        if isinstance(value, str):
            return value.strip(), []
        if isinstance(value, bool):
            return None, [_issue("error", "INVALID_IDENTIFIER", "Boolean values cannot be identifiers.", name, original, value)]
        if isinstance(value, int):
            return str(value), [
                _issue(
                    "warning",
                    "IDENTIFIER_STORED_AS_NUMBER",
                    "Excel stored this identifier as a number, so leading zeros cannot be recovered.",
                    name,
                    original,
                    value,
                )
            ]
        if isinstance(value, float):
            if not math.isfinite(value):
                return None, [_issue("error", "INVALID_IDENTIFIER", "Value is not a usable identifier.", name, original, value)]
            rendered = str(int(value)) if value.is_integer() else format(value, "f")
            return rendered, [
                _issue(
                    "warning",
                    "IDENTIFIER_STORED_AS_NUMBER",
                    "Excel stored this identifier as a number, so leading zeros cannot be recovered.",
                    name,
                    original,
                    value,
                )
            ]
        return str(value).strip(), []
    if kind == "boolean":
        if isinstance(value, bool):
            return value, []
        token = str(value).strip().lower()
        mapping = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False, "1": True, "0": False}
        if token in mapping:
            return mapping[token], []
        return None, [_issue("error", "INVALID_BOOLEAN", "Value could not be parsed as a boolean.", name, original, value)]
    if kind in {"date", "datetime"}:
        return parse_date_value(value, locale, column.get("date_format"), name, original)
    if kind == "percentage" and isinstance(value, str) and value.strip().endswith("%"):
        number = parse_number(value.strip()[:-1], locale)
        if number is None:
            return None, [_issue("error", "INVALID_NUMBER", "Percent value could not be parsed.", name, original, value)]
        return decimal_to_canonical(number / Decimal(100)), []
    if kind in NUMERIC_TYPES:
        number = parse_number(value, locale)
        if number is None:
            return None, [_issue("error", "INVALID_NUMBER", "Value could not be parsed as a number.", name, original, value)]
        if kind == "integer" and number != number.to_integral_value():
            return None, [_issue("error", "INVALID_INTEGER", "Value is not a whole number and was not rounded.", name, original, value)]
        return decimal_to_canonical(number), []
    return None, [_issue("error", "UNKNOWN_TYPE", "Column type is not supported.", name, original, kind)]


def looks_like_date(value: Any) -> bool:
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return _parse_iso(text) is not None or _DATE_RE.fullmatch(text) is not None


def looks_like_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no", "y", "n", "1", "0"}


def suggest_type(values: list[Any]) -> tuple[str, bool]:
    nonempty = [value for value in values if not is_empty(value) and not is_total_marker(value)]
    if not nonempty:
        return "text", False
    if all(isinstance(value, str) and _IDENT_RE.fullmatch(value.strip()) for value in nonempty):
        return "identifier", False
    if any(isinstance(value, str) and _IDENT_RE.fullmatch(value.strip()) for value in nonempty) and all(
        isinstance(value, str) and re.fullmatch(r"\d+", value.strip()) for value in nonempty
    ):
        return "identifier", False
    if any(isinstance(value, str) and value.strip().endswith("%") for value in nonempty):
        return "percentage", False
    if all(looks_like_bool(value) for value in nonempty):
        return "boolean", False
    date_hits = sum(1 for value in nonempty if looks_like_date(value))
    number_hits = sum(1 for value in nonempty if parse_number(value, "en-US") is not None or parse_number(value, "de-DE") is not None)
    int_hits = 0
    for value in nonempty:
        number = parse_number(value, "en-US")
        if number is None:
            number = parse_number(value, "de-DE")
        if number is not None and number == number.to_integral_value():
            int_hits += 1
    ratio_base = len(nonempty)
    if date_hits / ratio_base >= 0.8 and date_hits >= number_hits:
        return "date", date_hits != ratio_base
    if number_hits / ratio_base >= 0.8:
        kind = "integer" if int_hits == number_hits else "decimal"
        return kind, number_hits != ratio_base
    return "text", False


def is_total_marker(value: Any) -> bool:
    return isinstance(value, str) and bool(_TOTAL_RE.fullmatch(value.strip()))


def suggest_header_row(matrix: list[list[Any]]) -> int:
    best_row = 1
    best_score = -1.0
    for index, row in enumerate(matrix[:25]):
        nonempty = [value for value in row if not is_empty(value)]
        if not nonempty:
            continue
        strings = 0
        for value in nonempty:
            if isinstance(value, str) and parse_number(value, "en-US") is None and not looks_like_date(value):
                strings += 1
        score = strings * 2 + len(nonempty) - index * 0.01
        if score > best_score:
            best_score = score
            best_row = index + 1
    return best_row
