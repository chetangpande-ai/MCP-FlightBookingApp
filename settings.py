"""Central local configuration for the Business Trip Copilot."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _port(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer port.") from error
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535.")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a positive number.") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive number.")
    return value


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _bounded_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class Settings:
    """Resolved settings, with environment variables overriding local defaults."""

    http_host: str
    travel_http_port: int
    policy_http_port: int
    work_http_port: int
    gateway_port: int
    gateway_url: str
    cluster_base_port: int
    cluster_size: int
    gateway_rate_capacity: int
    gateway_rate_refill_per_second: float
    gateway_health_timeout_seconds: float
    load_test_requests: int
    load_test_concurrency: int
    travel_http_url: str
    policy_http_url: str
    work_http_url: str
    token_secret: str
    groq_api_key: str
    groq_model: str
    http_timeout_seconds: float
    weather_timeout_seconds: float
    approval_ttl_minutes: int
    policy_search_mode: str
    policy_cache_path: Path
    audit_log_path: Path
    langsmith_tracing: bool
    database_path: Path
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        host = values.get("MCP_HOST", "127.0.0.1")
        travel_port = _port(values, "TRAVEL_HTTP_PORT", 8001)
        policy_port = _port(values, "POLICY_HTTP_PORT", 8002)
        work_port = _port(values, "WORK_HTTP_PORT", 8003)
        gateway_port = _port(values, "GATEWAY_PORT", 8000)
        cluster_base_port = _port(
            values, "CLUSTER_BASE_PORT", travel_port
        )
        database_path = Path(
            values.get("DATABASE_PATH", "data/business_trip.sqlite3")
        ).expanduser()
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        policy_cache_path = Path(
            values.get("POLICY_CACHE_PATH", "data/policy_vectors.json")
        ).expanduser()
        if not policy_cache_path.is_absolute():
            policy_cache_path = PROJECT_ROOT / policy_cache_path
        audit_log_path = Path(
            values.get("AUDIT_LOG_PATH", "data/tool_audit.jsonl")
        ).expanduser()
        if not audit_log_path.is_absolute():
            audit_log_path = PROJECT_ROOT / audit_log_path
        policy_search_mode = values.get("POLICY_SEARCH_MODE", "tfidf").strip().lower()
        if policy_search_mode not in {"tfidf", "offline"}:
            raise ValueError("POLICY_SEARCH_MODE must be 'tfidf' or 'offline'.")
        cluster_size = _bounded_int(values, "CLUSTER_SIZE", 3, 1, 8)
        if cluster_base_port + cluster_size - 1 > 65535:
            raise ValueError("CLUSTER_BASE_PORT and CLUSTER_SIZE exceed port 65535.")
        cluster_ports = {
            cluster_base_port + index for index in range(cluster_size)
        }
        if gateway_port in cluster_ports:
            raise ValueError(
                "GATEWAY_PORT overlaps a travel cluster port: "
                + str(gateway_port)
            )
        tracing_value = values.get("LANGSMITH_TRACING", "false").strip().lower()
        if tracing_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("LANGSMITH_TRACING must be a boolean value.")

        return cls(
            http_host=host,
            travel_http_port=travel_port,
            policy_http_port=policy_port,
            work_http_port=work_port,
            gateway_port=gateway_port,
            gateway_url=values.get(
                "GATEWAY_URL", f"http://{host}:{gateway_port}/mcp"
            ),
            cluster_base_port=cluster_base_port,
            cluster_size=cluster_size,
            gateway_rate_capacity=_bounded_int(
                values, "GATEWAY_RATE_CAPACITY", 100, 1, 10_000
            ),
            gateway_rate_refill_per_second=_bounded_float(
                values, "GATEWAY_RATE_REFILL_PER_SECOND", 50.0, 0.1, 10_000.0
            ),
            gateway_health_timeout_seconds=_bounded_float(
                values, "GATEWAY_HEALTH_TIMEOUT_SECONDS", 0.5, 0.1, 5.0
            ),
            load_test_requests=_bounded_int(
                values, "LOAD_TEST_REQUESTS", 50, 1, 5_000
            ),
            load_test_concurrency=_bounded_int(
                values, "LOAD_TEST_CONCURRENCY", 10, 1, 100
            ),
            travel_http_url=values.get(
                "TRAVEL_HTTP_URL", f"http://{host}:{travel_port}/mcp"
            ),
            policy_http_url=values.get(
                "POLICY_HTTP_URL", f"http://{host}:{policy_port}/mcp"
            ),
            work_http_url=values.get(
                "WORK_HTTP_URL", f"http://{host}:{work_port}/mcp"
            ),
            # This fallback is a demo-only local secret; use a unique .env value elsewhere.
            token_secret=values.get(
                "TOKEN_SECRET", "LOCAL_DEV_JWT_SECRET_FOR_DEMO_32_BYTES"
            ),
            groq_api_key=values.get("GROQ_API_KEY", ""),
            groq_model=values.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            http_timeout_seconds=_positive_float(
                values, "HTTP_TIMEOUT_SECONDS", 10.0
            ),
            weather_timeout_seconds=_positive_float(
                values, "WEATHER_TIMEOUT_SECONDS", 10.0
            ),
            approval_ttl_minutes=_bounded_int(
                values, "APPROVAL_TTL_MINUTES", 5, 1, 60
            ),
            policy_search_mode=policy_search_mode,
            policy_cache_path=policy_cache_path,
            audit_log_path=audit_log_path,
            langsmith_tracing=tracing_value in {"true", "1", "yes"},
            database_path=database_path,
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_env()
