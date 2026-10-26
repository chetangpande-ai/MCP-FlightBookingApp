"""Tests for hotel booking approval lifecycle and policy enforcement."""

from datetime import datetime, timedelta, timezone
import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from servers import idempotency
import approval_service


@pytest.fixture
def approval_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    database_path = tmp_path / "approval.sqlite3"
    monkeypatch.setattr(
        idempotency,
        "DEFAULT_DATABASE_PATH",
        database_path,
    )
    return database_path


def _action_status(database_path: Path, action_id: str) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT status FROM pending_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    assert row is not None
    return row[0]


def _audit_statuses(database_path: Path, action_id: str) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT status FROM audit_records WHERE action_id = ? ORDER BY id",
            (action_id,),
        ).fetchall()
    return [row[0] for row in rows]


def test_approved_action_issues_booking_token_calls_tool_and_audits(
    approval_database: Path,
) -> None:
    pending = approval_service.request_hotel_booking(
        "Dubai",
        nights=3,
        hotel_rate_inr=10_000,
    )

    result = asyncio.run(
        approval_service.decide_hotel_booking(
            pending["id"],
            approved=True,
            decided_by="approver@example.local",
        )
    )

    assert result["status"] == "booked"
    assert result["result"]["city"] == "Dubai"
    assert _action_status(approval_database, pending["id"]) == "approved"
    assert _audit_statuses(approval_database, pending["id"]) == ["booked"]
    with sqlite3.connect(approval_database) as connection:
        details = connection.execute(
            "SELECT details FROM audit_records WHERE action_id = ?",
            (pending["id"],),
        ).fetchone()[0]
    assert json.loads(details)["result"]["status"] == "confirmed (demo only)"


def test_rejected_action_is_not_booked_and_is_audited(
    approval_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = approval_service.request_hotel_booking(
        "Paris",
        nights=2,
        hotel_rate_inr=8_000,
    )

    async def unexpected_booking(
        params: dict[str, str | int],
        action_id: str,
    ) -> None:
        pytest.fail(f"rejected action attempted booking {action_id}: {params}")

    monkeypatch.setattr(
        approval_service,
        "_book_with_approval_token",
        unexpected_booking,
    )
    result = asyncio.run(
        approval_service.decide_hotel_booking(pending["id"], approved=False)
    )

    assert result["status"] == "rejected"
    assert _action_status(approval_database, pending["id"]) == "rejected"
    assert _audit_statuses(approval_database, pending["id"]) == ["rejected"]


def test_expired_approval_cannot_book(
    approval_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = approval_service.request_hotel_booking(
        "Tokyo",
        nights=1,
        hotel_rate_inr=10_000,
    )
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(approval_database) as connection:
        connection.execute(
            "UPDATE pending_actions SET expires_at = ? WHERE id = ?",
            (expired_time, pending["id"]),
        )

    async def unexpected_booking(
        params: dict[str, str | int],
        action_id: str,
    ) -> None:
        pytest.fail(f"expired action attempted booking {action_id}: {params}")

    monkeypatch.setattr(
        approval_service,
        "_book_with_approval_token",
        unexpected_booking,
    )
    result = asyncio.run(
        approval_service.decide_hotel_booking(pending["id"], approved=True)
    )

    assert result["status"] == "expired"
    assert _action_status(approval_database, pending["id"]) == "expired"
    assert _audit_statuses(approval_database, pending["id"]) == ["expired"]


def test_over_budget_request_is_rejected_before_pending_action_creation(
    approval_database: Path,
) -> None:
    with pytest.raises(ValueError, match="exceeds the policy limit"):
        approval_service.request_hotel_booking(
            "Dubai",
            nights=2,
            hotel_rate_inr=12_001,
        )

    assert not approval_database.exists()


def test_action_cannot_be_decided_twice(approval_database: Path) -> None:
    pending = approval_service.request_hotel_booking(
        "Dubai",
        nights=1,
        hotel_rate_inr=10_000,
    )
    asyncio.run(
        approval_service.decide_hotel_booking(pending["id"], approved=False)
    )

    with pytest.raises(ValueError, match="already been decided"):
        asyncio.run(
            approval_service.decide_hotel_booking(pending["id"], approved=True)
        )
    assert _action_status(approval_database, pending["id"]) == "rejected"
