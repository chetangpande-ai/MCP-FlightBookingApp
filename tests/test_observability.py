"""Tests for bounded metrics and privacy-preserving tool audit events."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import observability
from servers import travel_server


@pytest.fixture(autouse=True)
def isolated_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        observability,
        "settings",
        replace(
            observability.settings,
            audit_log_path=tmp_path / "tool-audit.jsonl",
        ),
    )
    observability.reset_metrics()


def _events() -> list[dict[str, Any]]:
    path = observability.settings.audit_log_path
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_observed_tool_logs_hash_without_raw_arguments() -> None:
    result = travel_server.search_flights(
        origin="Top Secret Origin",
        destination="Dubai",
    )

    assert len(result) == 2
    event = _events()[0]
    assert event["tool"] == "search_flights"
    assert event["status"] == "success"
    assert event["caller"] == "local-or-stdio"
    assert len(event["arguments_hash"]) == 64
    assert "Top Secret Origin" not in json.dumps(event)
    assert event["request_id"]
    assert event["latency_ms"] >= 0


def test_positional_tool_arguments_are_included_in_hash() -> None:
    travel_server.search_flights("Secret Origin One", "Dubai")
    first_hash = _events()[0]["arguments_hash"]
    travel_server.search_flights("Secret Origin Two", "Dubai")
    second_hash = _events()[1]["arguments_hash"]

    assert first_hash != second_hash


def test_failed_tool_call_is_counted_and_logged() -> None:
    result = travel_server.get_live_weather(" ")
    metrics = observability.metrics_snapshot()

    assert result["ok"] is False
    assert metrics["calls"] == 1
    assert metrics["errors"] == 1
    assert metrics["latency_samples"] == 1
    assert _events()[0]["status"] == "error"


def test_metrics_endpoint_returns_snapshot() -> None:
    travel_server.search_flights("Bangalore", "Dubai")
    payload = observability.metrics_snapshot()

    assert payload["calls"] == 1
    assert payload["errors"] == 0
    assert payload["p50_latency_ms"] >= 0
    assert payload["p95_latency_ms"] >= 0
