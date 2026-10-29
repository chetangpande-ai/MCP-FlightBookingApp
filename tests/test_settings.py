"""Tests for shared local configuration."""

from pathlib import Path

import pytest

from settings import PROJECT_ROOT, Settings


def test_defaults_use_local_urls_and_database_path() -> None:
    config = Settings.from_env({})

    assert config.http_host == "127.0.0.1"
    assert config.travel_http_port == 8001
    assert config.travel_http_url == "http://127.0.0.1:8001/mcp"
    assert config.policy_http_url == "http://127.0.0.1:8002/mcp"
    assert config.work_http_url == "http://127.0.0.1:8003/mcp"
    assert config.gateway_url == "http://127.0.0.1:8000/mcp"
    assert config.cluster_base_port == 8001
    assert config.cluster_size == 3
    assert config.gateway_rate_capacity == 100
    assert config.gateway_health_timeout_seconds == 0.5
    assert config.load_test_requests == 50
    assert config.load_test_concurrency == 10
    assert config.database_path == PROJECT_ROOT / "data" / "business_trip.sqlite3"
    assert config.policy_search_mode == "tfidf"
    assert config.policy_cache_path == PROJECT_ROOT / "data" / "policy_vectors.json"
    assert config.audit_log_path == PROJECT_ROOT / "data" / "tool_audit.jsonl"
    assert config.langsmith_tracing is False
    assert config.token_secret == "LOCAL_DEV_JWT_SECRET_FOR_DEMO_32_BYTES"
    assert config.approval_ttl_minutes == 5


def test_environment_overrides_ports_urls_model_and_path() -> None:
    config = Settings.from_env(
        {
            "MCP_HOST": "localhost",
            "TRAVEL_HTTP_PORT": "8123",
            "GROQ_MODEL": "local-test-model",
            "DATABASE_PATH": "tmp/test.sqlite3",
            "HTTP_TIMEOUT_SECONDS": "3.5",
        }
    )

    assert config.travel_http_port == 8123
    assert config.travel_http_url == "http://localhost:8123/mcp"
    assert config.policy_http_url == "http://localhost:8002/mcp"
    assert config.groq_model == "local-test-model"
    assert config.database_path == PROJECT_ROOT / "tmp" / "test.sqlite3"
    assert config.http_timeout_seconds == 3.5


def test_server_url_can_be_overridden_explicitly() -> None:
    config = Settings.from_env(
        {"TRAVEL_HTTP_URL": "http://localhost:9001/custom-mcp"}
    )

    assert config.travel_http_url == "http://localhost:9001/custom-mcp"


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_invalid_ports_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="TRAVEL_HTTP_PORT"):
        Settings.from_env({"TRAVEL_HTTP_PORT": value})


@pytest.mark.parametrize("value", ["0", "-1", "not-a-timeout"])
def test_invalid_timeouts_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="HTTP_TIMEOUT_SECONDS"):
        Settings.from_env({"HTTP_TIMEOUT_SECONDS": value})


def test_absolute_database_path_is_preserved(tmp_path: Path) -> None:
    config = Settings.from_env({"DATABASE_PATH": str(tmp_path / "copilot.sqlite3")})

    assert config.database_path == tmp_path / "copilot.sqlite3"


def test_policy_search_mode_and_cache_path_can_be_configured(tmp_path: Path) -> None:
    config = Settings.from_env(
        {
            "POLICY_SEARCH_MODE": "offline",
            "POLICY_CACHE_PATH": str(tmp_path / "policy-index.json"),
        }
    )

    assert config.policy_search_mode == "offline"
    assert config.policy_cache_path == tmp_path / "policy-index.json"


def test_audit_path_and_langsmith_tracing_are_configurable(tmp_path: Path) -> None:
    config = Settings.from_env(
        {
            "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
            "LANGSMITH_TRACING": "true",
        }
    )

    assert config.audit_log_path == tmp_path / "audit.jsonl"
    assert config.langsmith_tracing is True


@pytest.mark.parametrize(
    "values",
    [
        {"CLUSTER_SIZE": "9"},
        {"CLUSTER_BASE_PORT": "65535", "CLUSTER_SIZE": "2"},
        {"GATEWAY_PORT": "8001"},
    ],
)
def test_invalid_cluster_port_configuration_is_rejected(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(values)


@pytest.mark.parametrize("value", ["", "openai", "keyword"])
def test_invalid_policy_search_mode_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="POLICY_SEARCH_MODE"):
        Settings.from_env({"POLICY_SEARCH_MODE": value})


@pytest.mark.parametrize("value", ["0", "61", "not-an-integer"])
def test_invalid_approval_ttl_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="APPROVAL_TTL_MINUTES"):
        Settings.from_env({"APPROVAL_TTL_MINUTES": value})
