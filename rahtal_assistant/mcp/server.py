"""Local STDIO MCP server that makes Codex the Rahtal interface."""

from __future__ import annotations

import atexit
import logging
from functools import lru_cache
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..client import RahtalClient
from ..config import RahtalSettings
from ..errors import RahtalError
from ..services import AuthService, PerformanceService, TaskService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SERVER_INSTRUCTIONS = (
    "Use Rahtal tools for the user's Rahtal tasks and performance. Never guess "
    "entity IDs. Resolve natural task references with rahtal_search_my_tasks, "
    "ask the user when candidates are ambiguous, and pass only a returned ID to "
    "rahtal_get_task or a write tool. A task is optional for a performance "
    "record; routine work such as a daily meeting may be recorded without one. "
    "If the performance subject is unknown, resolve it with "
    "rahtal_list_performance_titles. Before recording performance, check what "
    "is already recorded with rahtal_list_performance. For more than a couple "
    "of records, use rahtal_create_performance_batch: run it with dry_run=true, "
    "show the user the plan and any problems, and only then run it with "
    "dry_run=false. Creation is a non-idempotent write: do not repeat a create "
    "after an uncertain failure; list the day first to see whether it landed. "
    "Fix a wrong record with rahtal_update_performance instead of deleting and "
    "recreating it. Never expose credentials or tokens."
)

mcp = FastMCP(
    "rahtal",
    instructions=SERVER_INSTRUCTIONS,
    log_level="WARNING",
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
DESTRUCTIVE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)


class ServiceBundle:
    def __init__(self) -> None:
        self.client = RahtalClient(RahtalSettings.from_env())
        self.auth = AuthService(self.client)
        self.tasks = TaskService(self.client)
        self.performance = PerformanceService(self.client, self.auth, self.tasks)


@lru_cache(maxsize=1)
def services() -> ServiceBundle:
    bundle = ServiceBundle()
    atexit.register(bundle.client.close)
    return bundle


def _run(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except RahtalError as exc:
        raise ToolError(f"{exc.code}: {exc.message}") from exc


@mcp.tool(
    title="Who am I in Rahtal",
    description="Return the currently authenticated Rahtal user.",
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_whoami() -> dict[str, Any]:
    return _run(lambda: services().auth.whoami(refresh=True))


@mcp.tool(
    title="List my Rahtal tasks",
    description=(
        "List tasks assigned to the authenticated Rahtal user. Status accepts "
        "todo, in_progress, active, done, not_done, all, or the verified IDs 1-4. "
        "Rahtal marks every listed task as viewed."
    ),
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_list_my_tasks(
    status: str | int | None = "active",
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    commander_id: int | None = None,
    approval_status: Literal["pending", "approved", "rejected"] | None = None,
    is_completed: bool | None = None,
) -> dict[str, Any]:
    return _run(
        lambda: services().tasks.list_my_tasks(
            status=status,
            page=page,
            page_size=page_size,
            search=search,
            commander_id=commander_id,
            approval_status=approval_status,
            is_completed=is_completed,
        )
    )


@mcp.tool(
    title="Search my Rahtal tasks",
    description=(
        "Find assigned tasks from a natural title, description, person name, or "
        "exact task code. Returns candidates and an ambiguity flag; never invents an ID."
    ),
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_search_my_tasks(
    query: str,
    status: str | int | None = "all",
    limit: int = 8,
) -> dict[str, Any]:
    return _run(
        lambda: services().tasks.search_my_tasks(
            query,
            status=status,
            limit=limit,
        )
    )


@mcp.tool(
    title="Get a Rahtal task",
    description="Get full useful details for one exact assigned Rahtal task ID.",
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_get_task(task_id: int) -> dict[str, Any]:
    return _run(lambda: services().tasks.get_task(task_id))


@mcp.tool(
    title="Update a Rahtal task's status",
    description=(
        "Move one exact assigned task to todo, in_progress, done, or not_done, "
        "like dragging its card on the Rahtal board. Rahtal records the change "
        "in the task history and notifies the task's creator. "
        "non_completion_reason is accepted only with not_done."
    ),
    annotations=IDEMPOTENT_WRITE,
    structured_output=True,
)
def rahtal_update_task_status(
    task_id: int,
    status: Literal["todo", "in_progress", "done", "not_done"],
    non_completion_reason: str | None = None,
) -> dict[str, Any]:
    return _run(
        lambda: services().tasks.update_status(
            task_id, status, non_completion_reason=non_completion_reason
        )
    )


@mcp.tool(
    title="List Rahtal performance subjects",
    description=(
        "List the authenticated user's allowed Rahtal performance subjects and "
        "their real title_id values. Use this before performance creation when "
        "the subject ID is not already known."
    ),
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_list_performance_titles(search: str | None = None) -> dict[str, Any]:
    return _run(lambda: services().performance.list_titles(search=search))


@mcp.tool(
    title="Create Rahtal performance",
    description=(
        "Create one daily Rahtal performance record, optionally linked to an exact "
        "task ID. This is a non-idempotent write. title_id is the Rahtal "
        "performance-subject ID. "
        "Use Gregorian YYYY-MM-DD by default or set date_calendar='jalali'. Provide "
        "start/end, or duration_minutes plus exactly one time anchor; duration alone "
        "is not safe. Do not repeat after an uncertain failure."
    ),
    annotations=WRITE,
    structured_output=True,
)
def rahtal_create_performance(
    title_id: int,
    summary: str,
    task_id: int | None = None,
    date: str | None = "today",
    date_calendar: Literal["gregorian", "jalali"] = "gregorian",
    start_time: str | None = None,
    end_time: str | None = None,
    duration_minutes: int | None = None,
    customer_id: int | None = None,
    product_id: int | None = None,
    importance_id: int | None = 2,
    urgency_id: int | None = 2,
) -> dict[str, Any]:
    return _run(
        lambda: services().performance.create_performance(
            task_id=task_id,
            title_id=title_id,
            summary=summary,
            date=date,
            date_calendar=date_calendar,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            customer_id=customer_id,
            product_id=product_id,
            importance_id=importance_id,
            urgency_id=urgency_id,
        )
    )


@mcp.tool(
    title="List my Rahtal performance",
    description=(
        "List the authenticated user's performance records for one day or a date "
        "range of up to 62 days, with per-day totals. Dates are Gregorian "
        "YYYY-MM-DD by default, or Jalali with date_calendar='jalali'. end_date "
        "defaults to start_date."
    ),
    annotations=READ_ONLY,
    structured_output=True,
)
def rahtal_list_performance(
    start_date: str | None = "today",
    end_date: str | None = None,
    date_calendar: Literal["gregorian", "jalali"] = "gregorian",
) -> dict[str, Any]:
    return _run(
        lambda: services().performance.list_performance(
            start_date=start_date,
            end_date=end_date,
            date_calendar=date_calendar,
        )
    )


@mcp.tool(
    title="Create Rahtal performance in a batch",
    description=(
        "Validate, and optionally create, many performance records at once. Each "
        "entry takes title_id, summary, date, start_time, end_time (or "
        "duration_minutes with one anchor), and optional task_id, customer_id, "
        "product_id, importance_id, urgency_id. dry_run=true (the default) "
        "checks every entry, the past-date limit, task ownership, and overlaps "
        "within the batch and with existing records, and writes nothing. "
        "dry_run=false creates entries in order and stops at the first failure "
        "without retrying; the result lists what was created."
    ),
    annotations=WRITE,
    structured_output=True,
)
def rahtal_create_performance_batch(
    entries: list[dict[str, Any]],
    date_calendar: Literal["gregorian", "jalali"] = "gregorian",
    dry_run: bool = True,
) -> dict[str, Any]:
    return _run(
        lambda: services().performance.create_performance_batch(
            entries,
            date_calendar=date_calendar,
            dry_run=dry_run,
        )
    )


@mcp.tool(
    title="Update Rahtal performance",
    description=(
        "Change one of the user's existing performance records by its record ID "
        "(from rahtal_list_performance). Only the fields passed are changed; "
        "set clear_task=true to unlink its task. Returns the record before and "
        "after the change."
    ),
    annotations=IDEMPOTENT_WRITE,
    structured_output=True,
)
def rahtal_update_performance(
    record_id: int,
    summary: str | None = None,
    title_id: int | None = None,
    task_id: int | None = None,
    clear_task: bool = False,
    date: str | None = None,
    date_calendar: Literal["gregorian", "jalali"] = "gregorian",
    start_time: str | None = None,
    end_time: str | None = None,
    customer_id: int | None = None,
    product_id: int | None = None,
    importance_id: int | None = None,
    urgency_id: int | None = None,
) -> dict[str, Any]:
    return _run(
        lambda: services().performance.update_performance(
            record_id,
            summary=summary,
            title_id=title_id,
            task_id=task_id,
            clear_task=clear_task,
            date=date,
            date_calendar=date_calendar,
            start_time=start_time,
            end_time=end_time,
            customer_id=customer_id,
            product_id=product_id,
            importance_id=importance_id,
            urgency_id=urgency_id,
        )
    )


@mcp.tool(
    title="Delete Rahtal performance",
    description=(
        "Delete one of the user's performance records by its record ID. Rahtal "
        "hides the record rather than erasing it, but the user cannot restore "
        "it from the app. Confirm the exact record with the user first."
    ),
    annotations=DESTRUCTIVE_WRITE,
    structured_output=True,
)
def rahtal_delete_performance(record_id: int) -> dict[str, Any]:
    return _run(lambda: services().performance.delete_performance(record_id))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
