"""MCP server with demo travel tools and reference information."""

import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from mcp.server import MCPServer


server = MCPServer("Business Travel MCP Server")


# TOOLS = DO
@server.tool()
def search_flights(origin: str, destination: str) -> list[dict[str, str | int]]:
    """Return two sample flights for the requested route."""
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
def get_live_weather(city: str) -> dict[str, str | float | int]:
    """Look up the current weather using the free Open-Meteo APIs."""
    try:
        geocoding_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode(
            {"name": city, "count": 1, "language": "en", "format": "json"}
        )
        with urlopen(geocoding_url, timeout=10) as response:
            locations = json.loads(response.read().decode("utf-8")).get("results", [])

        if not locations:
            return {"error": f"No location found for {city}."}

        location = locations[0]
        forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "timezone": "auto",
            }
        )
        with urlopen(forecast_url, timeout=10) as response:
            current = json.loads(response.read().decode("utf-8"))["current"]

        return {
            "city": location["name"],
            "temperature_c": current["temperature_2m"],
            "humidity_percent": current["relative_humidity_2m"],
            "wind_speed_kmh": current["wind_speed_10m"],
        }
    except (URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
        return {"error": f"Weather lookup failed for {city}: {error}"}


@server.tool()
def book_hotel(city: str, nights: int, hotel_class: str = "4-star") -> dict[str, str | int]:
    """Create a demo hotel booking confirmation. This tool has a side effect."""
    return {
        "confirmation": "DEMO-HOTEL-001",
        "city": city,
        "nights": nights,
        "hotel_class": hotel_class,
        "status": "confirmed (demo only)",
    }


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