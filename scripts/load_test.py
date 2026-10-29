"""Send a bounded local MCP request load through the travel gateway."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.error import URLError
from urllib.request import urlopen

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auth.tokens import issue_token
from settings import settings


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return round(ordered[index], 2)


async def _run_load(request_count: int, concurrency: int) -> None:
    token = issue_token(
        settings.gateway_url,
        "local-load-test",
        ["travel:read"],
        secret=settings.token_secret,
        expires_minutes=5,
    )
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures: list[str] = []
    request_id = 0

    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        async def send_request(index: int) -> None:
            nonlocal request_id
            async with semaphore:
                request_id += 1
                started = perf_counter()
                try:
                    response = await client.post(
                        settings.gateway_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {
                                    "name": "local-load-test",
                                    "version": "1",
                                },
                            },
                        },
                    )
                    if response.status_code != 200:
                        failures.append(
                            f"request {index}: HTTP {response.status_code}"
                        )
                    else:
                        payload = response.json()
                        if "result" not in payload or "error" in payload:
                            failures.append(f"request {index}: invalid MCP response")
                        else:
                            latencies.append((perf_counter() - started) * 1000)
                except (httpx.HTTPError, ValueError) as error:
                    failures.append(f"request {index}: {type(error).__name__}")

        await asyncio.gather(*(send_request(index) for index in range(request_count)))

    print(f"Requests: {request_count}")
    print(f"Concurrency: {concurrency}")
    print(f"Successes: {len(latencies)}")
    print(f"Failures: {len(failures)}")
    print(f"Success rate: {len(latencies) / request_count:.1%}")
    print(f"p50 latency: {_percentile(latencies, 0.50)} ms")
    print(f"p95 latency: {_percentile(latencies, 0.95)} ms")
    if failures:
        print("Sample failures:", failures[:5])

    metrics_url = settings.gateway_url.rsplit("/mcp", 1)[0] + "/gateway/metrics"
    try:
        with urlopen(metrics_url, timeout=settings.http_timeout_seconds) as response:
            metrics = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read gateway metrics: {error}") from error
    print("Requests per instance:")
    print(json.dumps(metrics["requests_per_instance"], indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requests",
        type=int,
        default=settings.load_test_requests,
        help="Number of MCP initialize requests (1-5000).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.load_test_concurrency,
        help="Maximum simultaneous requests (1-100).",
    )
    args = parser.parse_args()
    if not 1 <= args.requests <= 5000:
        raise SystemExit("--requests must be between 1 and 5000.")
    if not 1 <= args.concurrency <= 100:
        raise SystemExit("--concurrency must be between 1 and 100.")
    asyncio.run(_run_load(args.requests, args.concurrency))


if __name__ == "__main__":
    main()
