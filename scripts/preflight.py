"""Report local laptop readiness without changing project files."""

import ctypes
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from settings import settings


def _ram_bytes() -> tuple[int | None, int | None]:
    windows_kernel = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if windows_kernel is not None:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if windows_kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys, status.ullAvailPhys
        return None, None

    sysconf = getattr(os, "sysconf", None)
    if callable(sysconf):
        try:
            page_size = sysconf("SC_PAGE_SIZE")
            total_pages = sysconf("SC_PHYS_PAGES")
            available_pages = sysconf("SC_AVPHYS_PAGES")
            return page_size * total_pages, page_size * available_pages
        except (OSError, ValueError):
            pass
    return None, None


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind((host, port))
        except OSError:
            return False
        return True


def _internet_reachable() -> bool:
    url = "https://geocoding-api.open-meteo.com/v1/search?name=Dubai&count=1"
    try:
        with urlopen(url, timeout=4) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _docker_status() -> tuple[str, str]:
    executable = shutil.which("docker")
    if executable is None:
        return "not installed", "not installed"
    try:
        result = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "installed", "not running or not responding"
    if result.returncode == 0:
        return "installed", f"running (engine {result.stdout.strip()})"
    return "installed", "not running or unavailable"


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def main() -> None:
    total_ram, available_ram = _ram_bytes()
    disk = shutil.disk_usage(PROJECT_ROOT)
    docker_installed, docker_running = _docker_status()
    ports = sorted(
        {
            settings.travel_http_port,
            settings.policy_http_port,
            settings.work_http_port,
            settings.gateway_port,
            *(
                settings.cluster_base_port + index
                for index in range(settings.cluster_size)
            ),
        }
    )

    print("LOCAL PREFLIGHT")
    print(f"Operating system: {platform.platform()}")
    print(f"Python: {platform.python_version()} ({sys.executable})")
    print(
        "RAM: "
        + (
            f"{total_ram / (1024**3):.2f} GiB total, "
            f"{available_ram / (1024**3):.2f} GiB available"
            if total_ram is not None and available_ram is not None
            else "unavailable"
        )
    )
    print(f"CPU cores: {os.cpu_count() or 'unavailable'}")
    print(f"Free disk: {disk.free / (1024**3):.2f} GiB")
    print("Configured ports:")
    for port in ports:
        print(f"  {port}: {'free' if _port_available(settings.http_host, port) else 'in use'}")
    print(f"OPENAI_API_KEY set: {'yes' if os.getenv('OPENAI_API_KEY') else 'no'}")
    print(f"Open-Meteo reachable: {'yes' if _internet_reachable() else 'no'}")
    print(f"Docker: {docker_installed}; {docker_running}")
    print("Python package versions:")
    for package in ("mcp", "langchain", "langchain-mcp-adapters", "a2a-sdk"):
        print(f"  {package}: {_package_version(package)}")


if __name__ == "__main__":
    main()
