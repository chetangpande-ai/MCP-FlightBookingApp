"""In-memory HTTP tests for MCP bearer authentication."""

from collections.abc import Iterator

import jwt
import pytest
from starlette.testclient import TestClient

from auth.tokens import issue_token
from servers.travel_server import server
from settings import settings


@pytest.fixture
def http_client() -> Iterator[TestClient]:
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{settings.travel_http_port}",
    ) as client:
        yield client


def _initialize(client: TestClient, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "stage-two-test", "version": "1"},
            },
        },
    )


def _call_hotel(client: TestClient, token: str) -> dict:
    response = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "book_hotel",
                "arguments": {"city": "Dubai", "nights": 2},
            },
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def _token(audience: str, scopes: list[str]) -> str:
    return issue_token(
        audience,
        "test-agent",
        scopes,
        secret=settings.token_secret,
    )


def test_http_rejects_missing_token(http_client: TestClient) -> None:
    assert _initialize(http_client).status_code == 401


def test_http_rejects_bad_token(http_client: TestClient) -> None:
    assert _initialize(http_client, "not-a-jwt").status_code == 401


def test_http_rejects_expired_token(http_client: TestClient) -> None:
    token = jwt.encode(
        {
            "sub": "test-agent",
            "aud": settings.travel_http_url,
            "scope": "travel:read",
            "iat": 0,
            "exp": 1,
        },
        settings.token_secret,
        algorithm="HS256",
    )

    assert _initialize(http_client, token).status_code == 401


def test_http_rejects_wrong_audience(http_client: TestClient) -> None:
    token = _token(settings.policy_http_url, ["travel:read"])

    assert _initialize(http_client, token).status_code == 401


def test_http_rejects_missing_scope(http_client: TestClient) -> None:
    token = _token(settings.travel_http_url, ["policy:read"])

    assert _initialize(http_client, token).status_code == 401


def test_http_accepts_valid_read_token(http_client: TestClient) -> None:
    token = _token(settings.travel_http_url, ["travel:read"])

    response = _initialize(http_client, token)

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Business Travel MCP Server"


def test_read_token_cannot_book_hotel_over_http(http_client: TestClient) -> None:
    result = _call_hotel(
        http_client,
        _token(settings.travel_http_url, ["travel:read"]),
    )

    assert result["isError"] is True


def test_booking_scope_allows_authenticated_booking(http_client: TestClient) -> None:
    result = _call_hotel(
        http_client,
        _token(settings.travel_http_url, ["travel:read", "travel:book"]),
    )

    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "confirmed (demo only)"
