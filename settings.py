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


@dataclass(frozen=True)
class Settings:
    """Resolved settings, with environment variables overriding local defaults."""

    http_host: str
    travel_http_port: int
    policy_http_port: int
    work_http_port: int
    gateway_port: int
    travel_http_url: str
    policy_http_url: str
    work_http_url: str
    token_secret: str
    groq_api_key: str
    groq_model: str
    http_timeout_seconds: float
    weather_timeout_seconds: float
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
        database_path = Path(
            values.get("DATABASE_PATH", "data/business_trip.sqlite3")
        ).expanduser()
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path

        return cls(
            http_host=host,
            travel_http_port=travel_port,
            policy_http_port=policy_port,
            work_http_port=work_port,
            gateway_port=gateway_port,
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
            database_path=database_path,
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_env()
