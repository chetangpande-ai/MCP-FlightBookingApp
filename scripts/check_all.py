"""Run preflight, the full pytest suite, and local STDIO/HTTP smoke checks."""

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from settings import settings


def _run_step(label: str, command: list[str], *, timeout: int = 180) -> None:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise SystemExit(f"{label} failed with exit code {result.returncode}.")


def _smoke_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((settings.http_host, settings.travel_http_port))
            return settings.travel_http_port
        except OSError:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary:
                temporary.bind((settings.http_host, 0))
                return temporary.getsockname()[1]


def _wait_for_server(process: subprocess.Popen, health_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Travel HTTP server exited with code {process.returncode}."
            )
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise TimeoutError(f"Travel server did not become healthy at {health_url}.")


def _http_app_smoke() -> None:
    port = _smoke_port()
    server_url = f"http://{settings.http_host}:{port}/mcp"
    child_env = os.environ.copy()
    child_env["TRAVEL_HTTP_PORT"] = str(port)
    child_env["TRAVEL_HTTP_URL"] = server_url
    child_env["TRAVEL_INSTANCE_ID"] = "check-all"
    server_script = PROJECT_ROOT / "http-servers" / "travel_http_server.py"
    process = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=PROJECT_ROOT,
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(process, f"http://{settings.http_host}:{port}/health")
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "app_http.py")],
            cwd=PROJECT_ROOT,
            env=child_env,
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise SystemExit(
                "HTTP app smoke failed:\n" + result.stdout + result.stderr
            )
        print("Authenticated app_http.py smoke: passed")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _optional_agent_smoke() -> None:
    print("\n=== app.py agent smoke ===", flush=True)
    if not settings.groq_api_key:
        print("Skipped: GROQ_API_KEY is not set; no remote model call was attempted.")
        return
    print(
        "Skipped by default even though GROQ_API_KEY is set. Pass --include-llm "
        "to make an explicit live Groq request."
    )


def _run_checks(include_llm: bool) -> None:
    _run_step("Preflight", [sys.executable, "scripts/preflight.py"], timeout=30)
    _run_step("Pytest", [sys.executable, "-m", "pytest", "-q"])
    _run_step(
        "STDIO travel MCP smoke",
        [sys.executable, "tests/check_travel_server.py"],
        timeout=90,
    )
    print("\n=== Authenticated HTTP client smoke ===", flush=True)
    _http_app_smoke()

    print("\n=== app.py import smoke ===", flush=True)
    _run_step("Import host app", [sys.executable, "-c", "import app"])
    if include_llm:
        if not settings.groq_api_key:
            raise SystemExit("--include-llm requires GROQ_API_KEY in .env.")
        print("\n=== Optional live Groq agent smoke ===", flush=True)
        result = subprocess.run(
            [sys.executable, "app.py"],
            cwd=PROJECT_ROOT,
            input="Dubai\n5\nBangalore\n12000\nno\n",
            capture_output=True,
            check=False,
            text=True,
            timeout=180,
        )
        if result.returncode:
            raise SystemExit(
                "Live app.py smoke failed:\n" + result.stdout + result.stderr
            )
        print("Live app.py smoke: passed")
    else:
        _optional_agent_smoke()

    print("\nAll requested local checks completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-llm",
        action="store_true",
        help="Run app.py with Groq when GROQ_API_KEY is configured (may use API quota).",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="business-trip-check-") as temp_dir:
        os.environ["AUDIT_LOG_PATH"] = str(Path(temp_dir) / "tool_audit.jsonl")
        _run_checks(args.include_llm)


if __name__ == "__main__":
    main()
