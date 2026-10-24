"""Start the travel server over STDIO and check everything it exposes."""

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


async def main() -> None:
    server_file = Path(__file__).resolve().parents[1] / "servers" / "travel_server.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_file)],
    )

    # The client starts the server process and talks to it through STDIO.
    async with Client(params) as client:
        print("TOOLS")
        tools = await client.list_tools()
        print(tools)
        print("search_flights:", await client.call_tool(
            "search_flights", {"origin": "Bangalore", "destination": "Dubai"}
        ))
        print("get_live_weather:", await client.call_tool(
            "get_live_weather", {"city": "Dubai"}
        ))
        print("book_hotel:", await client.call_tool(
            "book_hotel", {"city": "Dubai", "nights": 4}
        ))

        print("RESOURCES")
        print("available:", await client.list_resources())
        print("preferences:", await client.read_resource("travel://preferences"))
        print("TEMPLATES")
        print("available:", await client.list_resource_templates())
        print("city guide:", await client.read_resource("city://Dubai/guide"))

        print("PROMPTS")
        print("available:", await client.list_prompts())
        print(
            "trip plan:",
            await client.get_prompt(
                "plan_business_trip", {"destination": "Dubai", "days": "5"}
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())