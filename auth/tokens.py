"""JWT issuance and verification for local MCP HTTP auth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier


ALLOWED_SCOPES = frozenset(
    {"travel:read", "travel:book", "policy:read", "work:write"}
)


def _normalize_scopes(scope_value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize scope claims from a space-delimited string or list."""
    if scope_value is None:
        return []
    if isinstance(scope_value, str):
        items = scope_value.split()
    elif isinstance(scope_value, (list, tuple)) and all(
        isinstance(item, str) for item in scope_value
    ):
        items = list(scope_value)
    else:
        return []
    return [item.strip() for item in items if item and item.strip()]


def format_scopes(scopes: Iterable[str]) -> str:
    """Serialize scope values into the JWT scope claim format."""
    return " ".join(sorted(set(str(scope).strip() for scope in scopes if str(scope).strip())))


def issue_token(
    server_url: str,
    subject: str,
    scopes: str | list[str] | tuple[str, ...],
    *,
    secret: str,
    expires_minutes: int = 30,
    client_id: str = "local-agent",
) -> str:
    """Issue a signed local JWT for a specific MCP server and scope set."""
    if not secret:
        raise ValueError("TOKEN_SECRET must be configured before issuing a JWT.")
    if not server_url.strip():
        raise ValueError("A server URL is required for the JWT audience.")
    if not subject.strip():
        raise ValueError("A subject is required for the JWT.")
    if not 1 <= expires_minutes <= 60:
        raise ValueError("Local JWT expiry must be between 1 and 60 minutes.")
    token_scopes = _normalize_scopes(scopes)
    unknown_scopes = set(token_scopes) - ALLOWED_SCOPES
    if unknown_scopes:
        raise ValueError(f"Unsupported scope(s): {', '.join(sorted(unknown_scopes))}.")
    if not token_scopes:
        raise ValueError("At least one scope is required.")

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=expires_minutes)
    payload = {
        "sub": subject,
        "aud": server_url,
        "scope": format_scopes(token_scopes),
        "exp": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
        "client_id": client_id,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class LocalJWTVerifier(TokenVerifier):
    """Verify short-lived JWTs created by the local dev issuer."""

    def __init__(
        self,
        *,
        secret: str,
        expected_resource_url: str,
        required_scopes: list[str] | None = None,
    ) -> None:
        self.secret = secret
        self.expected_resource_url = expected_resource_url
        self.required_scopes = required_scopes or []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an AccessToken when the token is valid for this MCP server."""
        if not token:
            return None

        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                audience=self.expected_resource_url,
                options={"require": ["exp", "iat", "aud", "sub", "scope"]},
            )
        except jwt.InvalidTokenError:
            return None

        payload_aud = payload.get("aud")
        if payload_aud != self.expected_resource_url:
            return None

        expires_at = payload.get("exp")
        issued_at = payload.get("iat")
        if (
            not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
        ):
            return None
        if expires_at <= issued_at or expires_at - issued_at > 60 * 60:
            return None
        if expires_at <= int(datetime.now(timezone.utc).timestamp()):
            return None

        granted_scopes = set(_normalize_scopes(payload.get("scope")))
        if not granted_scopes.issubset(ALLOWED_SCOPES):
            return None
        if self.required_scopes and not set(self.required_scopes).issubset(granted_scopes):
            return None

        subject = payload.get("sub")
        client_id = payload.get("client_id", "local-client")
        if not isinstance(subject, str) or not subject.strip():
            return None
        if not isinstance(client_id, str) or not client_id.strip():
            return None

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=sorted(granted_scopes),
            expires_at=int(expires_at),
            resource=self.expected_resource_url,
            subject=subject,
            claims=payload,
        )
