from __future__ import annotations

import unittest
from typing import Any

from rahtal_assistant.errors import RahtalNotFoundError
from rahtal_assistant.services.tasks import TaskService


def task(task_id: int, title: str, *, code: int | None = None) -> dict[str, Any]:
    return {
        "id": task_id,
        "code": code or task_id + 100,
        "order_title": title,
        "details": f"Details for {title}",
        "created_at": "1405-05-20",
        "days_until_deadline": 2,
        "commander_full_name": "Commander One",
        "performer_full_name": "Employee One",
        "approval_status": "pending",
        "is_completed": False,
        "tags": [],
        "has_comment": False,
    }


def page(items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "total_pages": 1,
        "total_items": len(items) if total is None else total,
        "current_page": 1,
        "next_page": None,
        "previous_page": None,
        "results": items,
    }


class FakeTaskClient:
    def __init__(self, pools: dict[int, list[dict[str, Any]]]) -> None:
        self.pools = pools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, *, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        if path.startswith("leadership/get-order-details/"):
            task_id = int(path.rstrip("/").split("/")[-1])
            for items in self.pools.values():
                for item in items:
                    if item["id"] == task_id:
                        detail = dict(item)
                        detail["comments"] = []
                        return detail
            raise RahtalNotFoundError("Task not found.")

        status_id = int(path.rstrip("/").split("/")[-1])
        items = list(self.pools.get(status_id, []))
        search = str(params.get("search", "")).casefold()
        if search:
            items = [
                item
                for item in items
                if search in item["order_title"].casefold()
                or search == str(item["code"])
            ]
        count = int(params.get("count", 20))
        return page(items[:count], total=len(items))


class TaskServiceTests(unittest.TestCase):
    def test_list_active_tasks_aggregates_verified_statuses(self) -> None:
        client = FakeTaskClient(
            {1: [task(1, "Todo task")], 2: [task(2, "Doing task")]}
        )
        result = TaskService(client).list_my_tasks(status="active", page_size=10)
        self.assertEqual({item["id"] for item in result["items"]}, {1, 2})
        self.assertEqual(result["statuses_queried"], ["todo", "in_progress"])
        self.assertEqual(result["total_items"], 2)

    def test_exact_search_returns_real_task(self) -> None:
        client = FakeTaskClient({1: [task(5, "QuerySmith API", code=322)]})
        result = TaskService(client).search_my_tasks("322", status="todo")
        self.assertEqual(result["candidates"][0]["id"], 5)
        self.assertEqual(result["candidates"][0]["match_score"], 1.0)
        self.assertFalse(result["ambiguous"])

    def test_forgiving_search_reports_ambiguity(self) -> None:
        client = FakeTaskClient(
            {
                1: [
                    task(1, "QuerySmith API"),
                    task(2, "QuerySmith worker"),
                ]
            }
        )
        result = TaskService(client).search_my_tasks("QuerySmith", status="todo")
        self.assertEqual([item["id"] for item in result["candidates"]], [2, 1])
        self.assertTrue(result["ambiguous"])

    def test_search_no_match(self) -> None:
        client = FakeTaskClient({1: [task(1, "Persian payroll migration")]})
        result = TaskService(client).search_my_tasks("zzzzzz", status="todo")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["no_match"])

    def test_get_task_returns_details_only_for_real_id(self) -> None:
        client = FakeTaskClient({2: [task(9, "Task details")]})
        service = TaskService(client)
        self.assertEqual(service.get_task(9)["title"], "Task details")
        with self.assertRaises(RahtalNotFoundError):
            service.get_task(999)


if __name__ == "__main__":
    unittest.main()
