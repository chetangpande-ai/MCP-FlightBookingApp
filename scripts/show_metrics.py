"""Print metrics exposed by a running local travel HTTP server."""

import json
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from settings import settings


def main() -> None:
    url = f"http://{settings.http_host}:{settings.travel_http_port}/metrics"
    try:
        with urlopen(url, timeout=settings.http_timeout_seconds) as response:
            metrics = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read metrics from {url}: {error}") from error
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
