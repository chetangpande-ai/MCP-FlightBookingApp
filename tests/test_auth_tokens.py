"""Tests for local JWT issuance and validation."""

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from auth.tokens import LocalJWTVerifier, issue_token


def _verifier(server_url: str, required_scopes: list[str] | None = None) -> LocalJWTVerifier:
    return LocalJWTVerifier(
        secret="this-is-a-long-enough-secret-for-local-dev-123",
        expected_resource_url=server_url,
        required_scopes=required_scopes,
    )


def test_no_token_is_rejected() -> None:
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(""))
    assert result is None


def test_bad_token_is_rejected() -> None:
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token("not-a-jwt"))
    assert result is None


def test_wrong_signature_is_rejected() -> None:
    token = issue_token(
        "http://127.0.0.1:8001/mcp",
        "agent",
        ["travel:read"],
        secret="different-secret-for-signature-test-123456",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(token))

    assert result is None


def test_expired_token_is_rejected() -> None:
    expired = jwt.encode(
        {
            "sub": "agent",
            "aud": "http://127.0.0.1:8001/mcp",
            "scope": "travel:read policy:read",
            "iat": 0,
            "exp": 1,
        },
        "this-is-a-long-enough-secret-for-local-dev-123",
        algorithm="HS256",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(expired))
    assert result is None


def test_wrong_audience_is_rejected() -> None:
    token = issue_token(
        "http://127.0.0.1:8002/mcp",
        "agent",
        ["travel:read", "policy:read"],
        secret="this-is-a-long-enough-secret-for-local-dev-123",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(token))
    assert result is None


def test_audience_with_additional_resources_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "agent",
            "aud": [
                "http://127.0.0.1:8001/mcp",
                "http://127.0.0.1:8002/mcp",
            ],
            "scope": "travel:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        "this-is-a-long-enough-secret-for-local-dev-123",
        algorithm="HS256",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(token))

    assert result is None


def test_token_expiring_after_one_hour_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "agent",
            "aud": "http://127.0.0.1:8001/mcp",
            "scope": "travel:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1, seconds=1)).timestamp()),
        },
        "this-is-a-long-enough-secret-for-local-dev-123",
        algorithm="HS256",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp").verify_token(token))

    assert result is None


def test_missing_scope_is_rejected() -> None:
    token = issue_token(
        "http://127.0.0.1:8001/mcp",
        "agent",
        ["travel:read"],
        secret="this-is-a-long-enough-secret-for-local-dev-123",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp", ["policy:read"]).verify_token(token))
    assert result is None


def test_good_token_is_accepted() -> None:
    token = issue_token(
        "http://127.0.0.1:8001/mcp",
        "agent",
        ["travel:read", "policy:read", "work:write"],
        secret="this-is-a-long-enough-secret-for-local-dev-123",
    )
    result = asyncio.run(_verifier("http://127.0.0.1:8001/mcp", ["travel:read", "policy:read"]).verify_token(token))
    assert result is not None
    assert result.subject == "agent"
    assert result.resource == "http://127.0.0.1:8001/mcp"
    assert set(result.scopes) == {"travel:read", "policy:read", "work:write"}


def test_expiry_above_one_hour_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 1 and 60"):
        issue_token(
            "http://127.0.0.1:8001/mcp",
            "agent",
            ["travel:read"],
            secret="this-is-a-long-enough-secret-for-local-dev-123",
            expires_minutes=61,
        )


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported scope"):
        issue_token(
            "http://127.0.0.1:8001/mcp",
            "agent",
            ["admin"],
            secret="this-is-a-long-enough-secret-for-local-dev-123",
        )
