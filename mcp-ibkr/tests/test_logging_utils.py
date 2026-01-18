import logging

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
