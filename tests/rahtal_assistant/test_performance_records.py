from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from rahtal_assistant.errors import (
    RahtalUnavailableError,
    RahtalValidationError,
)
from rahtal_assistant.services import performance as performance_module
from rahtal_assistant.services.performance import PerformanceService
from rahtal_assistant.services.tasks import TaskService


def record(record_id: int, date: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": record_id,
        "date": date,
        "start_time": f"{start}:00",
        "end_time": f"{end}:00",
        "title": {"id": 10083, "name": "Daily Meeting"},
        "summary": "daily",
        "order_code": None,
        "order_title": None,
        "customer": None,
        "product": None,
        **extra,
    }


class FakeRecordClient:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.max_days = 50
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[dict[str, Any]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[str] = []
        self.fail_post_at: int | None = None
        self.next_id = 500

    def get(self, path: str, *, params=None):
        self.gets.append((path, dict(params or {})))
        if path == "functionality/activity-settings/max-day":
            return {"max_days_allowed": self.max_days}
        if path == "functionality/get_activities/":
            start, end = params["start_date"], params["end_date"]
            return [item for item in self.records if start <= item["date"] <= end]
        if path.startswith("functionality/activity/"):
            record_id = int(path.rstrip("/").split("/")[-1])
            return next(item for item in self.records if item["id"] == record_id)
        raise AssertionError(path)

    def post(self, path: str, *, json_body):
        assert path == "functionality/activities/"
        if self.fail_post_at is not None and len(self.posts) == self.fail_post_at:
            self.posts.append(dict(json_body))
            raise RahtalUnavailableError("Rahtal timed out while processing the request.")
        self.posts.append(dict(json_body))
        self.next_id += 1
        return {"id": self.next_id, **json_body}

    def patch(self, path: str, *, json_body):
        self.patches.append((path, dict(json_body)))
        record_id = int(path.rstrip("/").split("/")[-1])
        current = next(item for item in self.records if item["id"] == record_id)
        updated = dict(current)
        for key in ("date", "summary"):
            if key in json_body:
                updated[key] = json_body[key]
        for key in ("start_time", "end_time"):
            if key in json_body:
                updated[key] = f"{json_body[key]}:00"
        return updated

    def delete(self, path: str):
        self.deletes.append(path)
        return None


class FakeAuth:
    def whoami(self):
        return {"id": 7, "full_name": "Employee One"}


class FakeTasks:
    def __init__(self) -> None:
        self.checked: list[int] = []

    def get_task(self, task_id: int):
        self.checked.append(task_id)
        return {"id": task_id, "code": 1907, "title": "HR"}


TODAY = "1405-07-18"


class PerformanceRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        real = performance_module.to_rahtal_jalali_date

        def fixed_today(value, *, calendar="gregorian"):
            return TODAY if value in (None, "today") else real(value, calendar=calendar)

        patcher = mock.patch.object(
            performance_module, "to_rahtal_jalali_date", side_effect=fixed_today
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, client: FakeRecordClient, tasks: FakeTasks | None = None):
        return PerformanceService(client, FakeAuth(), tasks or FakeTasks())

    def test_list_returns_sorted_compact_records_and_day_totals(self) -> None:
        client = FakeRecordClient(
            [
                record(2, "1405-07-15", "08:20", "09:30"),
                record(1, "1405-07-15", "08:00", "08:20"),
                record(3, "1405-07-16", "10:00", "11:15"),
            ]
        )
        result = self.make(client).list_performance(
            start_date="1405-07-15", end_date="1405-07-16", date_calendar="jalali"
        )

        self.assertEqual([item["id"] for item in result["items"]], [1, 2, 3])
        self.assertEqual(result["items"][0]["start_time"], "08:00")
        self.assertEqual(result["items"][1]["duration_minutes"], 70)
        self.assertEqual(
            result["days"],
            [
                {"date": "1405-07-15", "count": 2, "minutes": 90},
                {"date": "1405-07-16", "count": 1, "minutes": 75},
            ],
        )
        self.assertEqual(result["total_minutes"], 165)

    def test_list_rejects_reversed_range(self) -> None:
        with self.assertRaises(RahtalValidationError):
            self.make(FakeRecordClient()).list_performance(
                start_date="1405-07-10", end_date="1405-07-01", date_calendar="jalali"
            )

    def test_batch_dry_run_reports_every_problem_and_writes_nothing(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-18", "08:00", "08:20")])
        client.max_days = 3
        result = self.make(client).create_performance_batch(
            [
                {"title_id": 1, "summary": "a", "date": "1405-07-18", "start_time": "08:10", "end_time": "08:40"},
                {"title_id": 1, "summary": "b", "date": "1405-07-17", "start_time": "10:00", "end_time": "11:00"},
                {"title_id": 1, "summary": "c", "date": "1405-07-17", "start_time": "10:30", "end_time": "11:30"},
                {"title_id": 1, "summary": "d", "date": "1405-07-01", "start_time": "10:00", "end_time": "11:00"},
                {"title_id": 1, "summary": " ", "date": "1405-07-17", "start_time": "12:00", "end_time": "13:00"},
            ],
            date_calendar="jalali",
        )

        self.assertFalse(result["valid"])
        problems = "\n".join(result["problems"])
        self.assertIn("entry 0: overlaps existing record 9", problems)
        self.assertIn("entry 2: overlaps entry 1", problems)
        self.assertIn("entry 3: 1405-07-01 is 17 days ago", problems)
        self.assertIn("entry 4: summary is required", problems)
        self.assertEqual(client.posts, [])

    def test_batch_does_not_write_when_validation_fails_even_without_dry_run(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-18", "08:00", "08:20")])
        result = self.make(client).create_performance_batch(
            [{"title_id": 1, "summary": "a", "date": "1405-07-18", "start_time": "08:00", "end_time": "08:30"}],
            date_calendar="jalali",
            dry_run=False,
        )
        self.assertFalse(result["valid"])
        self.assertEqual(client.posts, [])

    def test_batch_creates_in_order_and_verifies_each_task_once(self) -> None:
        client = FakeRecordClient()
        tasks = FakeTasks()
        result = self.make(client, tasks).create_performance_batch(
            [
                {"title_id": 10083, "summary": "daily", "date": "1405-07-18", "start_time": "08:00", "end_time": "08:20"},
                {"title_id": 10102, "summary": "work", "task_id": 71708, "date": "1405-07-18", "start_time": "08:20", "duration_minutes": 60},
            ],
            date_calendar="jalali",
            dry_run=False,
        )

        self.assertTrue(result["valid"])
        self.assertEqual([item["index"] for item in result["created"]], [0, 1])
        self.assertEqual(client.posts[1]["end_time"], "09:20")
        self.assertEqual(client.posts[1]["order_id"], 71708)
        self.assertEqual(client.posts[0]["importance_id"], 2)
        self.assertNotIn("failed", result)

    def test_batch_stops_at_first_failure_without_retrying(self) -> None:
        client = FakeRecordClient()
        client.fail_post_at = 1
        result = self.make(client).create_performance_batch(
            [
                {"title_id": 1, "summary": "a", "date": "1405-07-18", "start_time": "08:00", "end_time": "08:20"},
                {"title_id": 1, "summary": "b", "date": "1405-07-18", "start_time": "08:20", "end_time": "09:00"},
                {"title_id": 1, "summary": "c", "date": "1405-07-18", "start_time": "09:00", "end_time": "10:00"},
            ],
            date_calendar="jalali",
            dry_run=False,
        )

        self.assertEqual(len(client.posts), 2)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["failed"]["index"], 1)
        self.assertTrue(result["failed"]["outcome_uncertain"])
        self.assertEqual(result["not_attempted"], 1)

    def test_batch_rejects_unknown_fields(self) -> None:
        result = self.make(FakeRecordClient()).create_performance_batch(
            [{"title_id": 1, "summary": "a", "date": "1405-07-18", "start_time": "08:00", "end_time": "08:20", "order": 5}],
            date_calendar="jalali",
        )
        self.assertIn("unknown fields: order", result["problems"][0])

    def test_update_sends_date_and_times_with_only_changed_fields(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-15", "08:00", "08:20")])
        result = self.make(client).update_performance(9, summary="  new text ")

        path, body = client.patches[0]
        self.assertEqual(path, "functionality/activity/9/")
        self.assertEqual(
            body,
            {"date": "1405-07-15", "start_time": "08:00", "end_time": "08:20", "summary": "new text"},
        )
        self.assertEqual(result["before"]["summary"], "daily")
        self.assertEqual(result["after"]["summary"], "new text")

    def test_update_can_link_or_clear_a_task(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-15", "08:00", "08:20")])
        tasks = FakeTasks()
        service = self.make(client, tasks)

        service.update_performance(9, task_id=71708)
        service.update_performance(9, clear_task=True)

        self.assertEqual(tasks.checked, [71708])
        self.assertEqual(client.patches[0][1]["order_id"], 71708)
        self.assertIsNone(client.patches[1][1]["order_id"])
        with self.assertRaises(RahtalValidationError):
            service.update_performance(9, task_id=1, clear_task=True)

    def test_update_rejects_an_end_before_the_existing_start(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-15", "08:00", "08:20")])
        with self.assertRaises(RahtalValidationError):
            self.make(client).update_performance(9, end_time="07:30")
        self.assertEqual(client.patches, [])

    def test_delete_returns_the_deleted_record(self) -> None:
        client = FakeRecordClient([record(9, "1405-07-15", "08:00", "08:20")])
        result = self.make(client).delete_performance(9)
        self.assertEqual(client.deletes, ["functionality/activities/9/"])
        self.assertEqual(result["record"]["id"], 9)


class FakeStatusClient:
    def __init__(self) -> None:
        self.patches: list[tuple[str, dict[str, Any]]] = []

    def patch(self, path: str, *, json_body):
        self.patches.append((path, dict(json_body)))

    def get(self, path: str, *, params=None):
        return {
            "id": 71708,
            "code": 1907,
            "order_title": "HR",
            "tags": [{"value": "انجام شد"}],
        }


class TaskStatusTests(unittest.TestCase):
    def test_update_status_sends_board_payload_and_returns_task(self) -> None:
        client = FakeStatusClient()
        task = TaskService(client).update_status(71708, "done")
        self.assertEqual(
            client.patches,
            [("leadership/order-partial-update/71708/", {"status": 3})],
        )
        self.assertEqual(task["status"]["name"], "done")

    def test_not_done_accepts_a_reason(self) -> None:
        client = FakeStatusClient()
        TaskService(client).update_status(71708, "not_done", non_completion_reason=" blocked ")
        self.assertEqual(
            client.patches[0][1], {"status": 4, "non_completion_reason": "blocked"}
        )

    def test_rejects_aggregate_status_and_misplaced_reason(self) -> None:
        service = TaskService(FakeStatusClient())
        with self.assertRaises(RahtalValidationError):
            service.update_status(71708, "active")
        with self.assertRaises(RahtalValidationError):
            service.update_status(71708, "done", non_completion_reason="x")


if __name__ == "__main__":
    unittest.main()
