from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: Any) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "api_gateway",
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "session_id"):
            payload["session_id"] = record.session_id
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "metadata"):
            payload["metadata"] = record.metadata
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(h)
