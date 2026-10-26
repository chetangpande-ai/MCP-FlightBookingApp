"""MCP server for simple business-trip preparation tasks."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from servers.idempotency import execute_idempotent
from mcp.server import MCPServer


MAX_TASK_TITLE_LENGTH = 200
MAX_TASK_OWNER_LENGTH = 80
MAX_TASKS_RETURNED = 50
MAX_TASKS_STORED = 500
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
def _validate_text(name: str, value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty.")
    if len(normalized) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters.")
    return normalized


@server.tool()
def create_trip_task(
    title: str,
    idempotency_key: str,
    owner: str = "Sunny",
) -> dict[str, str]:
    """Create a demo trip task; a real version could create a Jira or GitHub Issue."""
    title = _validate_text("title", title, MAX_TASK_TITLE_LENGTH)
    owner = _validate_text("owner", owner, MAX_TASK_OWNER_LENGTH)

    def create_task() -> dict[str, str]:
        if len(trip_tasks) >= MAX_TASKS_STORED:
            raise ValueError(f"No more than {MAX_TASKS_STORED} demo tasks can be stored.")
        return {"title": title, "owner": owner, "status": "open"}

    def add_task(task: dict[str, str]) -> None:
        if len(trip_tasks) >= MAX_TASKS_STORED:
            raise ValueError(f"No more than {MAX_TASKS_STORED} demo tasks can be stored.")
        trip_tasks.append(task)

    return execute_idempotent(
        "create_trip_task",
        idempotency_key,
        {"title": title, "owner": owner},
        create_task,
        on_created=add_task,
    )


@server.tool()
def list_trip_tasks() -> list[dict[str, str]]:
    """Return the current demo trip tasks."""
    return trip_tasks[-MAX_TASKS_RETURNED:]


if __name__ == "__main__":
    server.run()