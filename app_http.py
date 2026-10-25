"""MCP client for the travel server's Streamable HTTP endpoint."""

import asyncio

from mcp import Client


SERVER_URL = "http://127.0.0.1:8001/mcp"


async def main() -> None:
	"""Connect to the separately running server and exercise its MCP features."""
	async with Client(SERVER_URL) as client:
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
	asyncio.run(main())
