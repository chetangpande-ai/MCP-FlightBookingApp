"""Focused tests for gateway token validation and per-subject rate limiting."""

import asyncio

from starlette.testclient import TestClient

from auth.tokens import LocalJWTVerifier, issue_token
from gateway import Backend, Gateway, TokenBucketRateLimiter
from settings import settings


def _gateway_token(audience: str = settings.gateway_url) -> str:
    return issue_token(
        audience,
        "gateway-test-client",
        ["travel:read"],
        secret=settings.token_secret,
    )


def test_token_bucket_limits_then_refills_per_client() -> None:
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1)

    assert limiter.allow("client-a", now=10)
    assert limiter.allow("client-a", now=10)
    assert not limiter.allow("client-a", now=10)
    assert limiter.allow("client-a", now=11)
    assert limiter.allow("client-b", now=11)


def test_gateway_rejects_missing_and_wrong_audience_tokens() -> None:
    client = TestClient(Gateway(backends=[]).app)

    missing = client.post("/mcp", json={})
    wrong_audience = client.post(
        "/mcp",
        headers={
            "Authorization": (
                "Bearer "
                + _gateway_token("http://127.0.0.1:9999/mcp")
            )
        },
        json={},
    )

    assert missing.status_code == 401
    assert wrong_audience.status_code == 401


def test_gateway_returns_unavailable_when_no_backends_are_healthy() -> None:
    client = TestClient(Gateway(backends=[]).app)

    response = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_gateway_token()}"},
        json={},
    )

    assert response.status_code == 503
    assert response.json()["error"].startswith("No healthy")


def test_gateway_applies_rate_limit_by_token_subject() -> None:
    gateway = Gateway(
        backends=[],
        limiter=TokenBucketRateLimiter(capacity=1, refill_per_second=0.1),
    )
    client = TestClient(gateway.app)
    headers = {"Authorization": f"Bearer {_gateway_token()}"}

    first = client.post("/mcp", headers=headers, json={})
    second = client.post("/mcp", headers=headers, json={})

    assert first.status_code == 503
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "1"


def test_gateway_mints_distinct_downstream_read_token() -> None:
    backend = Backend(
        instance="travel-1",
        url="http://127.0.0.1:8123/mcp",
        health_url="http://127.0.0.1:8123/health",
    )
    gateway = Gateway(backends=[backend])
    client_token_text = issue_token(
        settings.gateway_url,
        "gateway-test-client",
        ["travel:read", "travel:book"],
        secret=settings.token_secret,
    )
    client_token = asyncio.run(
        gateway.verifier.verify_token(client_token_text)
    )
    assert client_token is not None

    downstream_text = gateway._mint_downstream_token(client_token, backend)
    downstream = asyncio.run(
        LocalJWTVerifier(
            secret=settings.token_secret,
            expected_resource_url=backend.url,
            required_scopes=["travel:read"],
        ).verify_token(downstream_text)
    )

    assert downstream_text != client_token_text
    assert downstream is not None
    assert downstream.subject == "gateway-test-client"
    assert downstream.scopes == ["travel:read"]
