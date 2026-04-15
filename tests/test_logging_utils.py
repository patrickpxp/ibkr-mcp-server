import logging
import json

from mcp_ibkr.logging_utils import JsonLogFormatter, configure_logging


def test_configure_logging_sets_root_handler_and_level():
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level

    try:
        configure_logging(logging.WARNING)

        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert isinstance(handler.formatter, JsonLogFormatter)

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            assert logger.handlers == []
            assert logger.propagate is True
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_json_log_formatter_includes_extra_fields():
    formatter = JsonLogFormatter()
    record = logging.makeLogRecord(
        {
            "name": "test.logger",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "hello",
            "pathname": __file__,
            "lineno": 1,
            "ibkr_client_id": 100,
        }
    )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "hello"
    assert payload["ibkr_client_id"] == 100
