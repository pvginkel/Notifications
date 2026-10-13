"""The bridge's log: one JSON object per line, beside uvicorn's own logging."""

import copy
import json
import logging

import uvicorn.config

log = logging.getLogger("notification_bridge")


class JsonLines(logging.Formatter):
    """A record as one JSON object: its level, its message as `event`, and the `fields` it was
    logged with (`extra={"fields": {...}}`, which every record of `log` carries)."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"level": record.levelname.lower(), "event": record.getMessage(), **record.fields}
        )


def log_config() -> dict:
    """uvicorn's logging configuration, with the bridge's log added as JSON lines on stderr."""
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["formatters"]["json"] = {"()": JsonLines}
    config["handlers"]["json"] = {
        "class": "logging.StreamHandler",
        "formatter": "json",
        "stream": "ext://sys.stderr",
    }
    config["loggers"][log.name] = {"handlers": ["json"], "level": "INFO", "propagate": False}
    return config
