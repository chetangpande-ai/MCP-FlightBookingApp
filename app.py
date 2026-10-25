"""Host the travel, policy, and work MCP servers with a Groq agent."""

import asyncio
import json
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from groq import APIStatusError
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from mcp import Client, StdioServerParameters
from mcp.types import TextContent, TextResourceContents
from pydantic import Field, create_model

from logging_setup import configure_logging
from settings import settings

PROJECT_ROOT = Path(__file__).resolve().parent

SERVER_FILES = {
    "travel": PROJECT_ROOT / "servers" / "travel_server.py",
    "policy": PROJECT_ROOT / "servers" / "policy_server.py",
    "work": PROJECT_ROOT / "servers" / "work_server.py",
}


def server_parameters(server_file: Path) -> StdioServerParameters:
    """Build parameters so the MCP client launches a server with this Python."""
    return StdioServerParameters(
        command=sys.executable,
        args=[str(server_file)],
        cwd=str(PROJECT_ROOT),
    )


def content_text(contents: list[Any]) -> str:
    """Join text from MCP resource or prompt content blocks."""
    text_parts = []
    for item in contents:
        if isinstance(item, (TextContent, TextResourceContents)):
            text_parts.append(item.text)
        elif isinstance(getattr(item, "text", None), str):
            text_parts.append(item.text)
    return "\n".join(text_parts)


def python_type(schema: dict[str, Any]) -> type[Any]:
    """Convert the simple JSON types used by these MCP tools to Python types."""
    return {
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(schema.get("type"), str)


def build_arguments_model(server_name: str, tool: Any) -> type[Any]:
    """Build a Pydantic input model from a discovered MCP tool schema."""
    properties = tool.input_schema.get("properties", {})
    required_fields = set(tool.input_schema.get("required", []))
    fields: dict[str, tuple[Any, Any]] = {}

    for field_name, field_schema in properties.items():
        description = field_schema.get("description")
        if field_name in required_fields:
            fields[field_name] = (
                python_type(field_schema),
                Field(..., description=description),
            )
        else:
            fields[field_name] = (
                python_type(field_schema),
                Field(field_schema.get("default"), description=description),
            )

    model_name = re.sub(r"\W", "_", f"{server_name}_{tool.name}_Arguments")
    return create_model(model_name, **fields)


def make_langchain_tool(server_name: str, client: Client, mcp_tool: Any) -> StructuredTool:
    """Wrap an MCP tool call as a LangChain tool with a server-prefixed name."""
    async def call_mcp_tool(**arguments: Any) -> str:
        result = await client.call_tool(mcp_tool.name, arguments)
        if result.is_error:
            raise RuntimeError(content_text(result.content))
        if result.structured_content is not None:
            return json.dumps(result.structured_content, ensure_ascii=True)
        return content_text(result.content)

    return StructuredTool.from_function(
        coroutine=call_mcp_tool,
        name=f"{server_name}_{mcp_tool.name}",
        description=mcp_tool.description or f"Call {mcp_tool.name} on {server_name}.",
        args_schema=build_arguments_model(server_name, mcp_tool),
    )


def print_message_flow(messages: list[Any]) -> str:
    """Print the agent's messages and return the last assistant answer."""
    final_answer = ""
    print("\nMESSAGE FLOW")
    for message in messages:
        role = getattr(message, "type", message.__class__.__name__)
        content = message.content
        print(f"\n{role.upper()}: {content}")
        for tool_call in getattr(message, "tool_calls", []):
            print(f"  tool call: {tool_call['name']} {tool_call['args']}")
        if role == "ai" and content and not getattr(message, "tool_calls", []):
            final_answer = str(content)
    return final_answer


async def run_trip(destination: str, days: int, origin: str) -> None:
    """Read MCP context, run the agent, then ask before booking a hotel."""
    if not settings.groq_api_key:
        raise SystemExit("Set GROQ_API_KEY in your .env file before running the agent.")

    async with Client(server_parameters(SERVER_FILES["travel"])) as travel_client:
        preferences_result = await travel_client.read_resource("travel://preferences")
        guide_result = await travel_client.read_resource(f"city://{destination}/guide")
        prompt_result = await travel_client.get_prompt(
            "plan_business_trip", {"destination": destination, "days": str(days)}
        )

    async with Client(server_parameters(SERVER_FILES["policy"])) as policy_client:
        policy_result = await policy_client.read_resource("policy://travel-summary")

    preferences = content_text(preferences_result.contents)
    city_guide = content_text(guide_result.contents)
    planning_prompt = content_text(
        [message.content for message in prompt_result.messages]
    )
    policy_summary = content_text(policy_result.contents)

    print("TRAVEL PREFERENCES\n" + preferences)
    print("\nCITY GUIDE\n" + city_guide)
    print("\nTRIP PLANNING PROMPT\n" + planning_prompt)
    print("\nPOLICY SUMMARY\n" + policy_summary)
    print(
        "\nThese resources and prompt were read by the host. They reach the model only "
        "because the host adds them to the system prompt."
    )

    async with AsyncExitStack() as stack:
        clients: dict[str, Client] = {}
        for server_name, server_file in SERVER_FILES.items():
            clients[server_name] = await stack.enter_async_context(
                Client(server_parameters(server_file))
            )

        all_tools: list[StructuredTool] = []
        for server_name, client in clients.items():
            discovered_tools = await client.list_tools()
            all_tools.extend(
                make_langchain_tool(server_name, client, tool)
                for tool in discovered_tools.tools
            )

        print("\nDISCOVERED TOOLS")
        for tool in all_tools:
            print(tool.name)

        agent_tools = [tool for tool in all_tools if tool.name != "travel_book_hotel"]
        print("\nAgent tools (hotel booking excluded):")
        for tool in agent_tools:
            print(tool.name)

        system_prompt = f"""You are an enterprise business trip copilot.

Traveler preferences:
{preferences}

Destination context:
{city_guide}

Company policy summary:
{policy_summary}

Planning guidance:
{planning_prompt}

Rules:
- Use travel_get_live_weather for current destination weather.
- Use travel_search_flights to compare the requested route.
- Use policy_search_company_policy to verify policy details; do not guess policy.
- Use work_create_trip_task to create one practical preparation task.
- Do not claim that a hotel is booked. Hotel booking requires separate human approval.
- Explain important findings and keep the plan concise.
"""

        model = ChatGroq(model=settings.groq_model, temperature=0)
        agent = create_agent(model, agent_tools, system_prompt=system_prompt)
        request = (
            f"Plan a {days}-day business trip from {origin} to {destination}. "
            "Check the weather, find a flight, verify the relevant company policy, "
            "and create one preparation task. Recommend a hotel but do not book it."
        )
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": request}]}
            )
        except Exception as error:
            root_error = error
            while hasattr(root_error, "exceptions") and len(root_error.exceptions) == 1:
                root_error = root_error.exceptions[0]

            if isinstance(root_error, APIStatusError):
                print(f"Groq API error ({root_error.status_code}): {root_error}")
                print(
                    "Check GROQ_MODEL in .env and choose a model available to your Groq account."
                )
            else:
                print(f"Agent request failed: {root_error}")
            return

        final_answer = print_message_flow(result["messages"])
        print("\nFINAL ANSWER\n" + (final_answer or "See the message flow above."))

    approval = input("\nApprove booking? (yes/no): ").strip().lower()
    if approval == "yes":
        async with Client(server_parameters(SERVER_FILES["travel"])) as travel_client:
            booking_result = await travel_client.call_tool(
                "book_hotel", {"city": destination, "nights": days}
            )
        print("Booking result:", booking_result.structured_content or booking_result.content)
    else:
        print("Booking not made.")


def main() -> None:
    configure_logging()
    destination = input("Destination [Dubai]: ").strip() or "Dubai"
    days_text = input("Trip length in days [5]: ").strip() or "5"
    origin = input("Origin [Bangalore]: ").strip() or "Bangalore"
    try:
        days = int(days_text)
        if days <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit("Trip length must be a positive whole number.") from None

    asyncio.run(run_trip(destination, days, origin))


if __name__ == "__main__":
    main()