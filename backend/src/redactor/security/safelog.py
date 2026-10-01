"""PII-safe logging (plan §2.2): log records never carry entity text or exception payloads.

Log messages are built from counts, IDs, hashes and fixed strings. The formatter additionally
drops exception messages (keeping only the exception type) because exceptions raised while
parsing documents can embed document text.
"""

from __future__ import annotations

import logging
import sys


class PIISafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info:
            exc_type = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            record = logging.makeLogRecord(record.__dict__)
            record.exc_info = None
            record.exc_text = None
            record.msg = f"{record.msg} [exception: {exc_type}]"
        return super().format(record)


def get_logger(name: str = "redactor") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(PIISafeFormatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def describe_exception(exc: BaseException) -> str:
    """A PII-free description of an exception: its type only."""
    return type(exc).__name__
