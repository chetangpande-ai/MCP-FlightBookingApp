"""SQLite-backed idempotency for local side-effecting tools."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from settings import settings


ResultT = TypeVar("ResultT")
DEFAULT_DATABASE_PATH = settings.database_path
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def validate_idempotency_key(key: str) -> str:
    """Validate and normalize a caller-provided idempotency key."""
    if not isinstance(key, str):
        raise ValueError("idempotency_key must be a string.")
    normalized = key.strip()
    if not normalized:
        raise ValueError("idempotency_key must not be empty.")
    if len(normalized) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValueError(
            f"idempotency_key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
        )
    return normalized


def execute_idempotent(
    operation: str,
    idempotency_key: str,
    params: dict[str, str | int],
    create_result: Callable[[], ResultT],
    *,
    database_path: Path | None = None,
    on_created: Callable[[ResultT], None] | None = None,
) -> ResultT:
    """Run once for a key and persist the result for retries."""
    key = validate_idempotency_key(idempotency_key)
    canonical_params = json.dumps(
        params,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    params_hash = hashlib.sha256(canonical_params.encode("utf-8")).hexdigest()
    path = database_path or DEFAULT_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path, timeout=10) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_results (
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (operation, idempotency_key)
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT params_hash, result_json
            FROM idempotency_results
            WHERE operation = ? AND idempotency_key = ?
            """,
            (operation, key),
        ).fetchone()
        was_created = existing is None
        if existing is not None:
            if existing[0] != params_hash:
                raise ValueError(
                    "idempotency_key was already used with different parameters."
                )
            result = json.loads(existing[1])
        else:
            result = create_result()
            result_json = json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            connection.execute(
                """
                INSERT INTO idempotency_results
                    (operation, idempotency_key, params_hash, result_json)
                VALUES (?, ?, ?, ?)
                """,
                (operation, key, params_hash, result_json),
            )

    if was_created and on_created is not None:
        on_created(result)
    return result
