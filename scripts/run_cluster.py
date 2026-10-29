"""Launch a small local travel MCP cluster and its authenticated gateway."""

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gateway import Gateway, configured_backends
from logging_setup import configure_logging
from settings import settings


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind((host, port))
        except OSError:
            return False
        return True


def _wait_until_healthy(process: subprocess.Popen, health_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Travel server exited with code {process.returncode}.")
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise TimeoutError(f"Travel server did not become healthy at {health_url}.")


def _stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run local travel MCP instances behind an authenticated gateway."
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=settings.cluster_size,
        help="Number of local travel server processes (1-8).",
    )
    args = parser.parse_args()
    if not 1 <= args.instances <= 8:
        raise SystemExit("--instances must be between 1 and 8.")

    backends = configured_backends(count=args.instances)
    ports = [settings.gateway_port] + [
        settings.cluster_base_port + index for index in range(args.instances)
    ]
    if len(set(ports)) != len(ports):
        raise SystemExit("Gateway and travel instance ports must be distinct.")
    unavailable = [
        port for port in ports if not _port_is_available(settings.http_host, port)
    ]
    if unavailable:
        raise SystemExit(
            "These configured ports are already in use: "
            + ", ".join(str(port) for port in unavailable)
        )

    configure_logging()
    processes: list[subprocess.Popen] = []
    server_script = PROJECT_ROOT / "http-servers" / "travel_http_server.py"
    try:
        for index, backend in enumerate(backends):
            port = settings.cluster_base_port + index
            child_env = os.environ.copy()
            child_env["TRAVEL_HTTP_PORT"] = str(port)
            child_env["TRAVEL_HTTP_URL"] = backend.url
            child_env["TRAVEL_INSTANCE_ID"] = backend.instance
            process = subprocess.Popen(
                [sys.executable, str(server_script)],
                cwd=PROJECT_ROOT,
                env=child_env,
            )
            processes.append(process)
            _wait_until_healthy(process, backend.health_url)
            print(
                f"Healthy {backend.instance}: {backend.url} "
                f"(PID {process.pid})"
            )

        print(f"Gateway: {settings.gateway_url}")
        print("Press Ctrl+C to stop the gateway and all travel instances.")
        uvicorn = __import__("uvicorn")
        uvicorn.run(
            Gateway(backends=backends).app,
            host=settings.http_host,
            port=settings.gateway_port,
            log_level=settings.log_level.lower(),
        )
    finally:
        _stop_processes(processes)


if __name__ == "__main__":
    main()
