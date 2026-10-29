"""Local JSON-line tool audit and bounded in-process latency metrics."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from functools import wraps
import hashlib
import inspect
import json
import logging
import sys
from threading import Lock
from time import perf_counter
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import uuid4

from mcp.server.auth.middleware.auth_context import get_access_token

from settings import settings


P = ParamSpec("P")
R = TypeVar("R")
MAX_LATENCY_SAMPLES = 1000
_lock = Lock()
_calls = 0
_errors = 0
_latencies: deque[float] = deque(maxlen=MAX_LATENCY_SAMPLES)
_logger = logging.getLogger("tool_audit")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def metrics_snapshot() -> dict[str, int | float]:
    """Return a consistent snapshot of process-local call and latency metrics."""
    with _lock:
        latencies = list(_latencies)
        return {
            "calls": _calls,
            "errors": _errors,
            "latency_samples": len(latencies),
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
        }


def reset_metrics() -> None:
    """Reset process-local metrics, intended for isolated tests and demos."""
    global _calls, _errors
    with _lock:
        _calls = 0
        _errors = 0
        _latencies.clear()


def _caller() -> str:
    token = get_access_token()
    return token.subject if token is not None else "local-or-stdio"


def _arguments_hash(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"args": args, "kwargs": kwargs},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record(
    *,
    request_id: str,
    caller: str,
    tool: str,
    arguments_hash: str,
    status: str,
    latency_ms: float,
) -> None:
    global _calls, _errors
    with _lock:
        _calls += 1
        _errors += status == "error"
        _latencies.append(latency_ms)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "caller": caller,
        "tool": tool,
        "arguments_hash": arguments_hash,
        "status": status,
        "latency_ms": round(latency_ms, 3),
    }
    line = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    try:
        settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.audit_log_path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(line + "\n")
    except OSError:
        _logger.exception("Failed to persist tool audit event.")
        raise
    _logger.info("%s", line)


def observe_tool(function: Callable[P, R]) -> Callable[P, R]:
    """Audit a synchronous or asynchronous tool without recording raw arguments."""
    tool_name = function.__name__

    def finish(
        started: float,
        request_id: str,
        caller: str,
        arguments_hash: str,
        status: str,
    ) -> None:
        _record(
            request_id=request_id,
            caller=caller,
            tool=tool_name,
            arguments_hash=arguments_hash,
            status=status,
            latency_ms=(perf_counter() - started) * 1000,
        )

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = perf_counter()
            request_id = str(uuid4())
            caller = _caller()
            arguments_hash = _arguments_hash(args, kwargs)
            status = "error"
            try:
                result = await function(*args, **kwargs)
                status = (
                    "error"
                    if isinstance(result, dict) and result.get("ok") is False
                    else "success"
                )
                return result
            finally:
                finish(started, request_id, caller, arguments_hash, status)

        return async_wrapper

    @wraps(function)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        started = perf_counter()
        request_id = str(uuid4())
        caller = _caller()
        arguments_hash = _arguments_hash(args, kwargs)
        status = "error"
        try:
            result = function(*args, **kwargs)
            status = (
                "error"
                if isinstance(result, dict) and result.get("ok") is False
                else "success"
            )
            return result
        finally:
            finish(started, request_id, caller, arguments_hash, status)

    return sync_wrapper
