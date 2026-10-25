"""MCP client for the travel server's Streamable HTTP endpoint."""

import asyncio

from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from auth.tokens import issue_token
from logging_setup import configure_logging
from settings import settings


def build_bearer_token() -> str:
    """Mint a short-lived local dev token for this specific server URL."""
    if not settings.token_secret:
        raise RuntimeError("TOKEN_SECRET is required for local MCP HTTP auth.")
    # Never forward a caller token to a downstream API; validate and mint a local token
    # for the specific server instead of propagating the incoming credential.
    return issue_token(
        settings.travel_http_url,
        "local-agent",
        ["travel:read"],
        secret=settings.token_secret,
        expires_minutes=30,
    )


async def main() -> None:
    """Connect to the separately running server and exercise its MCP features."""
    token = build_bearer_token()
    transport = streamable_http_client(
        settings.travel_http_url,
        http_client=create_mcp_http_client(
            headers={"Authorization": f"Bearer {token}"}
        ),
    )

    async with Client(transport) as client:
        tools = await client.list_tools()
        print("Available tools:", [tool.name for tool in tools.tools])

        flights = await client.call_tool(
            "search_flights", {"origin": "Bangalore", "destination": "Dubai"}
        )
        print("Flights:", flights.structured_content or flights.content)

        preferences = await client.read_resource("travel://preferences")
        print("Preferences:", [item.text for item in preferences.contents])

        guide = await client.read_resource("city://Dubai/guide")
        print("City guide:", [item.text for item in guide.contents])

        prompt = await client.get_prompt(
            "plan_business_trip", {"destination": "Dubai", "days": "5"}
        )
        print("Trip planning prompt:", prompt.messages[0].content.text)


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
