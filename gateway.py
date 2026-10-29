"""Small authenticated round-robin proxy for local travel MCP instances."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
import logging
from threading import Lock
from time import monotonic

import httpx
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from auth.tokens import LocalJWTVerifier, issue_token
from settings import settings


logger = logging.getLogger(__name__)
_FORWARDED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
    }
)
_HOP_BY_HOP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


@dataclass(frozen=True)
class Backend:
    instance: str
    url: str
    health_url: str


def configured_backends(
    *,
    base_port: int | None = None,
    count: int | None = None,
) -> tuple[Backend, ...]:
    """Build local backend addresses from the shared cluster port settings."""
    first_port = settings.cluster_base_port if base_port is None else base_port
    instance_count = settings.cluster_size if count is None else count
    return tuple(
        Backend(
            instance=f"travel-{index + 1}",
            url=f"http://{settings.http_host}:{first_port + index}/mcp",
            health_url=f"http://{settings.http_host}:{first_port + index}/health",
        )
        for index in range(instance_count)
    )


class TokenBucketRateLimiter:
    """Per-subject token buckets with bounded in-memory client tracking."""

    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        *,
        max_clients: int = 5000,
    ) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = Lock()

    def allow(self, client_key: str, now: float | None = None) -> bool:
        current_time = monotonic() if now is None else now
        with self._lock:
            tokens, last_seen = self._buckets.get(
                client_key,
                (float(self.capacity), current_time),
            )
            tokens = min(
                float(self.capacity),
                tokens + max(0.0, current_time - last_seen) * self.refill_per_second,
            )
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            elif client_key not in self._buckets:
                return False

            if client_key not in self._buckets and len(self._buckets) >= self.max_clients:
                oldest_client = min(
                    self._buckets,
                    key=lambda key: self._buckets[key][1],
                )
                del self._buckets[oldest_client]
            self._buckets[client_key] = (tokens, current_time)
            return allowed


class Gateway:
    """Route authenticated MCP requests to healthy local travel instances."""

    def __init__(
        self,
        backends: Iterable[Backend] | None = None,
        *,
        verifier: LocalJWTVerifier | None = None,
        limiter: TokenBucketRateLimiter | None = None,
    ) -> None:
        self.backends = tuple(backends) if backends is not None else configured_backends()
        self.verifier = verifier or LocalJWTVerifier(
            secret=settings.token_secret,
            expected_resource_url=settings.gateway_url,
            required_scopes=["travel:read"],
        )
        self.limiter = limiter or TokenBucketRateLimiter(
            settings.gateway_rate_capacity,
            settings.gateway_rate_refill_per_second,
        )
        self._next_backend = 0
        self._request_counts = {backend.instance: 0 for backend in self.backends}
        self._counter_lock = Lock()
        self.app = Starlette(
            routes=[
                Route("/health", self.health, methods=["GET"]),
                Route("/gateway/metrics", self.metrics, methods=["GET"]),
                Route("/mcp", self.handle_mcp, methods=["POST", "GET", "DELETE"]),
            ]
        )

    async def health(self, request: Request) -> JSONResponse:
        """Expose gateway process health independently of backend availability."""
        healthy = await self._healthy_backends()
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "endpoint": request.url.path,
                "healthy_instances": [backend.instance for backend in healthy],
            },
            status_code=200 if healthy else 503,
        )

    async def metrics(self, request: Request) -> JSONResponse:
        """Return successful proxy requests grouped by backend instance."""
        with self._counter_lock:
            counts = dict(self._request_counts)
        return JSONResponse(
            {
                "endpoint": request.url.path,
                "requests_per_instance": counts,
                "total_forwarded": sum(counts.values()),
            }
        )

    async def _healthy_backends(self) -> list[Backend]:
        if not self.backends:
            return []
        timeout = settings.gateway_health_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            results = await asyncio.gather(
                *(self._is_healthy(client, backend) for backend in self.backends)
            )
        return [
            backend
            for backend, is_healthy in zip(self.backends, results)
            if is_healthy
        ]

    @staticmethod
    async def _is_healthy(client: httpx.AsyncClient, backend: Backend) -> bool:
        try:
            response = await client.get(backend.health_url)
            if response.status_code != 200:
                return False
            payload = response.json()
            return isinstance(payload, dict) and payload.get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    def _rotate(self, backends: list[Backend]) -> list[Backend]:
        if not backends:
            return []
        start = self._next_backend % len(backends)
        self._next_backend += 1
        return backends[start:] + backends[:start]

    async def _authenticate(self, request: Request) -> AccessToken | None:
        authorization = request.headers.get("authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not credential.strip():
            return None
        return await self.verifier.verify_token(credential.strip())

    def _mint_downstream_token(
        self,
        client_token: AccessToken,
        backend: Backend,
    ) -> str:
        """Mint a distinct read-only backend token; never forward the client token."""
        return issue_token(
            backend.url,
            client_token.subject,
            ["travel:read"],
            secret=settings.token_secret,
            expires_minutes=5,
            client_id="local-gateway",
        )

    async def handle_mcp(self, request: Request) -> Response:
        token = await self._authenticate(request)
        if token is None:
            return JSONResponse(
                {"error": "A valid travel:read bearer token is required."},
                status_code=401,
            )
        if not self.limiter.allow(token.subject):
            return JSONResponse(
                {"error": "Rate limit exceeded; retry shortly."},
                status_code=429,
                headers={"Retry-After": "1"},
            )

        backends = self._rotate(await self._healthy_backends())
        if not backends:
            return JSONResponse(
                {"error": "No healthy travel MCP instances are available."},
                status_code=503,
            )

        body = await request.body() if request.method == "POST" else None
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in _FORWARDED_REQUEST_HEADERS
        }

        for backend in backends:
            downstream_token = self._mint_downstream_token(token, backend)
            internal_headers = dict(headers)
            internal_headers["Authorization"] = f"Bearer {downstream_token}"
            client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
            try:
                upstream = await client.send(
                    client.build_request(
                        request.method,
                        backend.url,
                        params=request.query_params,
                        headers=internal_headers,
                        content=body,
                    ),
                    stream=True,
                )
            except httpx.HTTPError as error:
                await client.aclose()
                logger.warning(
                    "Travel instance %s failed during proxy request: %s",
                    backend.instance,
                    type(error).__name__,
                )
                continue

            with self._counter_lock:
                self._request_counts[backend.instance] += 1
            response_headers = {
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in upstream.headers.raw
                if name.decode("latin-1").lower() not in _HOP_BY_HOP_RESPONSE_HEADERS
            }

            async def close_upstream() -> None:
                await upstream.aclose()
                await client.aclose()

            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                headers=response_headers,
                background=BackgroundTask(close_upstream),
            )

        return JSONResponse(
            {"error": "All healthy travel MCP instances failed to respond."},
            status_code=503,
        )


gateway = Gateway()
app = gateway.app
