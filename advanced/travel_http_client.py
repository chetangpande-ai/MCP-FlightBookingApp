"""Connect to the travel MCP server over Streamable HTTP."""

import asyncio

from mcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:8001/mcp") as client:
        tools = await client.list_tools()
        print("Available tools:", [tool.name for tool in tools.tools])

        result = await client.call_tool(
            "search_flights", {"origin": "Bangalore", "destination": "Dubai"}
        )
        print("Flights:", result.structured_content or result.content)


if __name__ == "__main__":
    asyncio.run(main())