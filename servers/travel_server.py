"""MCP server with demo travel tools and reference information."""

import json
import os
import socket
import sys
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from mcp.server.auth.middleware.auth_context import get_access_token

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auth.tokens import LocalJWTVerifier
from observability import metrics_snapshot, observe_tool
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from servers.idempotency import execute_idempotent
from settings import settings


MAX_TEXT_LENGTH = 200
MAX_WEATHER_RESPONSE_BYTES = 64 * 1024
WEATHER_MAX_ATTEMPTS = 3
WEATHER_BASE_BACKOFF_SECONDS = 0.25
HOTEL_CLASSES = frozenset({"3-star", "4-star", "5-star"})


server = MCPServer(
    "Business Travel MCP Server",
    token_verifier=LocalJWTVerifier(
        secret=settings.token_secret,
        expected_resource_url=settings.travel_http_url,
        required_scopes=["travel:read"],
    ),
    auth=AuthSettings(
        issuer_url=f"http://{settings.http_host}:{settings.travel_http_port}",
        resource_server_url=settings.travel_http_url,
        required_scopes=["travel:read"],
        validate_token_resource=True,
    ),
)

from starlette.requests import Request
from starlette.responses import JSONResponse


@server.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health_check(request: Request) -> JSONResponse:
    """Expose a minimal local readiness response for this MCP server."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "travel",
            "instance": os.getenv("TRAVEL_INSTANCE_ID", "travel"),
        }
    )


@server.custom_route("/metrics", methods=["GET"], include_in_schema=False)
async def metrics_endpoint(request: Request) -> JSONResponse:
    """Expose local process counters for learning and smoke checks."""
    return JSONResponse(metrics_snapshot())


def _validate_text(name: str, value: str, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty.")
    if len(normalized) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters.")
    return normalized


class _WeatherRequestError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _get_weather_json(url: str) -> dict:
    for attempt in range(WEATHER_MAX_ATTEMPTS):
        try:
            with urlopen(url, timeout=settings.weather_timeout_seconds) as response:
                payload = response.read(MAX_WEATHER_RESPONSE_BYTES + 1)
            if len(payload) > MAX_WEATHER_RESPONSE_BYTES:
                raise _WeatherRequestError(
                    "Weather service response exceeded the size limit.",
                    retryable=False,
                )
            parsed = json.loads(payload.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise _WeatherRequestError(
                    "Weather service returned an invalid response.",
                    retryable=False,
                )
            return parsed
        except HTTPError as error:
            retryable = error.code in {408, 429} or 500 <= error.code <= 599
            if not retryable or attempt == WEATHER_MAX_ATTEMPTS - 1:
                raise _WeatherRequestError(
                    f"Weather service returned HTTP {error.code}.",
                    retryable=retryable,
                ) from error
        except (URLError, TimeoutError, socket.timeout) as error:
            if attempt == WEATHER_MAX_ATTEMPTS - 1:
                raise _WeatherRequestError(
                    "Weather service request failed after retries.",
                    retryable=True,
                ) from error

        sleep(WEATHER_BASE_BACKOFF_SECONDS * (2**attempt))

    raise RuntimeError("Weather retry loop exited unexpectedly.")


# TOOLS = DO
@server.tool()
@observe_tool
def search_flights(origin: str, destination: str) -> list[dict[str, str | int]]:
    """Return two sample flights for the requested route."""
    origin = _validate_text("origin", origin, max_length=100)
    destination = _validate_text("destination", destination, max_length=100)
    return [
        {
            "airline": "Air India",
            "flight": f"AI-101 ({origin} to {destination})",
            "price_inr": 24500,
            "stops": 0,
        },
        {
            "airline": "IndiGo",
            "flight": f"6E-205 ({origin} to {destination})",
            "price_inr": 18200,
            "stops": 1,
        },
    ]


@server.tool()
@observe_tool
def get_live_weather(
    city: str,
) -> dict[str, str | float | int | bool]:
    """Look up the current weather using the free Open-Meteo APIs."""
    try:
        city = _validate_text("city", city, max_length=100)
    except ValueError as error:
        return {"ok": False, "error": str(error), "retryable": False}

    try:
        geocoding_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode(
            {"name": city, "count": 1, "language": "en", "format": "json"}
        )
        locations = _get_weather_json(geocoding_url).get("results", [])

        if not locations:
            return {
                "ok": False,
                "error": f"No location found for {city}.",
                "retryable": False,
            }
        if not isinstance(locations, list) or not isinstance(locations[0], dict):
            return {
                "ok": False,
                "error": "Weather service returned an invalid location.",
                "retryable": False,
            }

        location = locations[0]
        forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "timezone": "auto",
            }
        )
        current = _get_weather_json(forecast_url)["current"]
        if not isinstance(current, dict):
            return {
                "ok": False,
                "error": "Weather service returned an invalid forecast.",
                "retryable": False,
            }

        return {
            "ok": True,
            "city": location["name"],
            "temperature_c": current["temperature_2m"],
            "humidity_percent": current["relative_humidity_2m"],
            "wind_speed_kmh": current["wind_speed_10m"],
        }
    except _WeatherRequestError as error:
        return {
            "ok": False,
            "error": str(error),
            "retryable": error.retryable,
        }
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        return {
            "ok": False,
            "error": f"Weather service returned invalid data ({type(error).__name__}).",
            "retryable": False,
        }


@server.tool()
@observe_tool
def book_hotel(
    city: str,
    nights: int,
    idempotency_key: str,
    hotel_class: str = "4-star",
) -> dict[str, str | int]:
    """Create a demo hotel booking confirmation. This tool has a side effect."""
    access_token = get_access_token()
    if access_token is None or "travel:book" not in access_token.scopes:
        raise PermissionError("Hotel booking requires the travel:book scope.")

    city = _validate_text("city", city, max_length=100)
    if isinstance(nights, bool) or not isinstance(nights, int) or not 1 <= nights <= 30:
        raise ValueError("nights must be an integer between 1 and 30.")
    if not isinstance(hotel_class, str) or hotel_class not in HOTEL_CLASSES:
        raise ValueError(
            f"hotel_class must be one of: {', '.join(sorted(HOTEL_CLASSES))}."
        )

    def create_booking() -> dict[str, str | int]:
        return {
            "confirmation": "DEMO-HOTEL-001",
            "city": city,
            "nights": nights,
            "hotel_class": hotel_class,
            "status": "confirmed (demo only)",
        }

    return execute_idempotent(
        "book_hotel",
        idempotency_key,
        {"city": city, "nights": nights, "hotel_class": hotel_class},
        create_booking,
    )


# RESOURCES = KNOW
@server.resource("travel://preferences")
def travel_preferences() -> str:
    """Share the traveler's saved preferences."""
    return "Preferred airline: Air India. Seat: aisle. Class: economy. Hotel category: 4-star."


@server.resource("city://{city}/guide")
def city_guide(city: str) -> str:
    """Return short business-travel tips for a city."""
    return (
        f"Business travel tips for {city}: check local transport options, keep your work ID "
        "available, and confirm local business hours before scheduling meetings."
    )


# PROMPTS = GUIDE
@server.prompt()
def plan_business_trip(destination: str, days: int) -> str:
    """Give numbered steps for planning a business trip."""
    return (
        f"Plan a {days}-day business trip to {destination}:\n"
        "1. Check the destination's current weather and practical travel context.\n"
        "2. Compare flight options and explain the price and stop trade-offs.\n"
        "3. Check company policy before recommending travel or lodging choices.\n"
        "4. Recommend a suitable hotel, but do not autonomously confirm a hotel booking.\n"
        "5. Summarize the plan and any details that still need the traveler's approval."
    )


if __name__ == "__main__":
    server.run()