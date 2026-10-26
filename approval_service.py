"""Local SQLite-backed human approval workflow for hotel bookings."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from mcp import Client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from auth.tokens import LocalJWTVerifier, issue_token
from servers import idempotency
from servers.policy_server import get_hotel_nightly_limit_inr
from servers.travel_server import server as travel_server
from settings import settings


def _database_path() -> Path:
    return idempotency.DEFAULT_DATABASE_PATH


@contextmanager
def _connection(
    database_path: Path | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    path = database_path or _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                params TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_by TEXT,
                decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                decided_by TEXT,
                recorded_at TEXT NOT NULL,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_records_action_id
                ON audit_records(action_id);
            """
        )
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_hotel_booking(
    city: str,
    nights: int,
    hotel_rate_inr: int,
    hotel_class: str = "4-star",
) -> dict[str, str]:
    """Check policy, then persist a pending request with an expiry."""
    if isinstance(hotel_rate_inr, bool) or not isinstance(hotel_rate_inr, int):
        raise ValueError("hotel_rate_inr must be a positive whole number.")
    if hotel_rate_inr <= 0:
        raise ValueError("hotel_rate_inr must be a positive whole number.")

    nightly_limit = get_hotel_nightly_limit_inr()
    if hotel_rate_inr > nightly_limit:
        raise ValueError(
            f"Hotel rate INR {hotel_rate_inr} exceeds the policy limit "
            f"of INR {nightly_limit} per night; no approval action was created."
        )

    if isinstance(nights, bool) or not isinstance(nights, int) or not 1 <= nights <= 30:
        raise ValueError("nights must be an integer between 1 and 30.")
    if not isinstance(city, str) or not city.strip() or len(city.strip()) > 100:
        raise ValueError("city must contain between 1 and 100 characters.")
    if not isinstance(hotel_class, str) or hotel_class not in {
        "3-star",
        "4-star",
        "5-star",
    }:
        raise ValueError("hotel_class must be 3-star, 4-star, or 5-star.")

    action_id = str(uuid.uuid4())
    requested_at = _utc_now()
    expires_at = requested_at + timedelta(minutes=settings.approval_ttl_minutes)
    params = {
        "city": city.strip(),
        "nights": nights,
        "hotel_rate_inr": hotel_rate_inr,
        "hotel_class": hotel_class,
    }
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO pending_actions
                (id, action, params, status, requested_at, expires_at)
            VALUES (?, 'book_hotel', ?, 'pending', ?, ?)
            """,
            (
                action_id,
                json.dumps(params, sort_keys=True),
                requested_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
    return {
        "id": action_id,
        "action": "book_hotel",
        "status": "pending",
        "requested_at": requested_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def _record_audit(
    action_id: str,
    status: str,
    decided_by: str | None,
    details: dict[str, object] | None = None,
) -> None:
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO audit_records
                (action_id, action, status, decided_by, recorded_at, details)
            VALUES (?, 'book_hotel', ?, ?, ?, ?)
            """,
            (
                action_id,
                status,
                decided_by,
                _utc_now().isoformat(),
                json.dumps(details, sort_keys=True) if details is not None else None,
            ),
        )


async def _book_with_approval_token(
    params: dict[str, str | int],
    action_id: str,
) -> dict[str, object]:
    token = issue_token(
        settings.travel_http_url,
        "local-human-approver",
        ["travel:read", "travel:book"],
        secret=settings.token_secret,
        expires_minutes=5,
    )
    verifier = LocalJWTVerifier(
        secret=settings.token_secret,
        expected_resource_url=settings.travel_http_url,
        required_scopes=["travel:read"],
    )
    access_token = await verifier.verify_token(token)
    if access_token is None:
        raise RuntimeError("Issued approval token failed local verification.")

    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        async with Client(travel_server) as client:
            result = await client.call_tool(
                "book_hotel",
                {
                    "city": params["city"],
                    "nights": params["nights"],
                    "hotel_class": params["hotel_class"],
                    "idempotency_key": f"approval-{action_id}",
                },
            )
        if result.is_error:
            error_text = "\n".join(
                item.text for item in result.content if hasattr(item, "text")
            )
            raise RuntimeError(f"Hotel booking tool failed: {error_text}")
        if result.structured_content is not None:
            return result.structured_content
        return {
            "content": [
                item.text for item in result.content if hasattr(item, "text")
            ]
        }
    finally:
        auth_context_var.reset(context_token)


async def decide_hotel_booking(
    action_id: str,
    approved: bool,
    decided_by: str = "local-user",
) -> dict[str, object]:
    """Record a human decision and execute a valid approved booking."""
    if not isinstance(approved, bool):
        raise ValueError("approved must be a boolean decision.")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("decided_by must be non-empty text.")

    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        action = connection.execute(
            "SELECT * FROM pending_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if action is None:
            raise ValueError("Pending action was not found.")
        if action["status"] != "pending":
            raise ValueError(f"Action has already been decided ({action['status']}).")

        now = _utc_now()
        expires_at = datetime.fromisoformat(action["expires_at"])
        if now >= expires_at:
            connection.execute(
                """
                UPDATE pending_actions
                SET status = 'expired', decided_by = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (decided_by.strip(), now.isoformat(), action_id),
            )
            expired = True
        else:
            status = "approved" if approved else "rejected"
            connection.execute(
                """
                UPDATE pending_actions
                SET status = ?, decided_by = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, decided_by.strip(), now.isoformat(), action_id),
            )
            expired = False
            params = json.loads(action["params"])

    if expired:
        _record_audit(action_id, "expired", decided_by.strip())
        return {"id": action_id, "status": "expired"}
    if not approved:
        _record_audit(action_id, "rejected", decided_by.strip())
        return {"id": action_id, "status": "rejected"}

    try:
        result = await _book_with_approval_token(params, action_id)
    except Exception as error:
        _record_audit(
            action_id,
            "booking_failed",
            decided_by.strip(),
            {"params": params, "error": str(error)},
        )
        raise

    _record_audit(
        action_id,
        "booked",
        decided_by.strip(),
        {"params": params, "result": result},
    )
    return {"id": action_id, "status": "booked", "result": result}
