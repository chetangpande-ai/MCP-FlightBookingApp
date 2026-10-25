"""Run the existing travel MCP server over Streamable HTTP."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from logging_setup import configure_logging
from settings import settings
from servers.travel_server import server


if __name__ == "__main__":
    configure_logging()
    server.run(
        transport="streamable-http",
        host=settings.http_host,
        port=settings.travel_http_port,
        stateless_http=True,
        json_response=True,
    )