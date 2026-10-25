"""Shared logging configuration for local app and server entry points."""

import logging
import sys

from settings import settings


def configure_logging() -> None:
    """Configure concise logs on stderr so STDIO MCP messages remain intact."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
