import json
import logging
from datetime import datetime, timezone

from app.config import get_settings

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("import_id", "event", "error_type"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(get_settings().log_level.upper())
    _CONFIGURED = True


def log_event(event: str, message: str, *, import_id: str | None = None, level: int = logging.INFO, error_type: str | None = None) -> None:
    logging.getLogger("symphony").log(
        level,
        message,
        extra={"event": event, "import_id": import_id, "error_type": error_type},
    )
