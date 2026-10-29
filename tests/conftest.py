"""Shared test isolation for generated tool audit data."""

from dataclasses import replace
from pathlib import Path

import pytest

import observability


@pytest.fixture(autouse=True)
def isolate_tool_audit_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        observability,
        "settings",
        replace(
            observability.settings,
            audit_log_path=tmp_path / "tool-audit.jsonl",
        ),
    )
    observability.reset_metrics()
