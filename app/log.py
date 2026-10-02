"""Logging: one place that decides how the app talks about itself.

Every stage of a note says what it did, so a note that comes out wrong can be
read back step by step: what was heard, what the model read, which company it
proposed, whether the market's own list accepted it, and what was applied.

The symbol lookup is logged in detail on purpose. A null symbol has four
possible causes — the model named no company, it named one that does not trade
in this market, the name did not match the reference, or the name matched more
than one company — and the log says which one happened, with the scores.

    LOG_LEVEL=INFO    every stage, every decision  (the default)
    LOG_LEVEL=DEBUG   plus the raw model output and every candidate scored
    LOG_LEVEL=WARNING only problems
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup(level: str = "INFO") -> None:
    """Install one stdout handler for the whole app.

    uvicorn installs its own handlers, which would print the same record twice;
    clearing them and letting the record propagate keeps one line per event,
    formatted the same way as everything else.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.strip().upper() or "INFO")

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # Third-party chatter. One line per outbound HTTP call would drown the log
    # the app writes about itself, which is the part worth reading.
    for name in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(name).setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
