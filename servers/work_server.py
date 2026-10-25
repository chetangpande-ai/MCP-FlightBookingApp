"""MCP server for simple business-trip preparation tasks."""

from mcp.server import MCPServer


server = MCPServer("Business Trip Work MCP Server")

# These demo tasks live only while this server process is running.
trip_tasks: list[dict[str, str]] = [
    {
        "title": "Review destination meeting schedule",
        "owner": "Sunny",
        "status": "open",
    },
    {
        "title": "Prepare presentation materials",
        "owner": "Sunny",
        "status": "open",
    },
]


# TOOLS = DO
@server.tool()
def create_trip_task(title: str, owner: str = "Sunny") -> dict[str, str]:
    """Create a demo trip task; a real version could create a Jira or GitHub Issue."""
    task = {"title": title, "owner": owner, "status": "open"}
    trip_tasks.append(task)
    return task


@server.tool()
def list_trip_tasks() -> list[dict[str, str]]:
    """Return the current demo trip tasks."""
    return trip_tasks.copy()


if __name__ == "__main__":
    server.run()