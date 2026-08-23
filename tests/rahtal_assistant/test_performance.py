from __future__ import annotations

import unittest
from datetime import date
from typing import Any

import jdatetime

from rahtal_assistant.errors import (
    RahtalAuthenticationError,
    RahtalNotFoundError,
    RahtalValidationError,
)
from rahtal_assistant.services.performance import PerformanceService


class FakeClient:
    def __init__(self, created: Any | None = None) -> None:
        self.created = created or {
            "id": 81,
            "date": "1405-05-20",
            "start_time": "09:00:00",
            "end_time": "10:30:00",
            "summary": "Implemented the report API",
            "title": {"id": 4, "name": "Development"},
            "order_code": 322,
        }
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.titles: Any = [{"id": 4, "name": "Development"}]
        self.post_error: Exception | None = None

    def get(self, path: str, *, params=None):
        if path == "core/title_activity/":
            return self.titles
        raise AssertionError(path)

    def post(self, path: str, *, json_body):
        self.posts.append((path, dict(json_body)))
        if self.post_error:
            raise self.post_error
        return self.created


class FakeAuth:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def whoami(self):
        if self.error:
            raise self.error
        return {"id": 7, "full_name": "Employee One"}


class FakeTasks:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get_task(self, task_id: int):
        if self.error:
            raise self.error
        return {"id": task_id, "code": 322, "title": "QuerySmith API"}


class PerformanceServiceTests(unittest.TestCase):
    def make_service(
        self,
        *,
        client: FakeClient | None = None,
        auth: FakeAuth | None = None,
        tasks: FakeTasks | None = None,
    ) -> PerformanceService:
        return PerformanceService(
            client or FakeClient(),
            auth or FakeAuth(),
            tasks or FakeTasks(),
        )

    def test_valid_registration_converts_date_and_duration(self) -> None:
        client = FakeClient()
        service = self.make_service(client=client)
        result = service.create_performance(
            task_id=12,
            title_id=4,
            summary="Implemented the report API",
            date="2026-08-11",
            date_calendar="gregorian",
            start_time="09:00",
            duration_minutes=90,
        )
        expected_date = jdatetime.date.fromgregorian(
            date=date(2026, 8, 11)
        ).strftime("%Y-%m-%d")
        path, payload = client.posts[0]
        self.assertEqual(path, "functionality/activities/")
        self.assertEqual(payload["user_id"], 7)
        self.assertEqual(payload["order_id"], 12)
        self.assertEqual(payload["date"], expected_date)
        self.assertEqual(payload["start_time"], "09:00")
        self.assertEqual(payload["end_time"], "10:30")
        self.assertEqual(result["duration_minutes"], 90)

    def test_missing_summary_is_rejected_before_write(self) -> None:
        client = FakeClient()
        service = self.make_service(client=client)
        with self.assertRaisesRegex(RahtalValidationError, "summary is required"):
            service.create_performance(
                task_id=12,
                title_id=4,
                summary=" ",
                start_time="09:00",
                end_time="10:00",
            )
        self.assertEqual(client.posts, [])

    def test_registration_without_task_sends_null_order_id(self) -> None:
        client = FakeClient()
        result = self.make_service(client=client).create_performance(
            title_id=4,
            summary="Daily team meeting",
            date="2026-08-17",
            date_calendar="gregorian",
            start_time="09:00",
            end_time="09:30",
        )

        _, payload = client.posts[0]
        self.assertIsNone(payload["order_id"])
        self.assertEqual(payload["importance_id"], 2)
        self.assertEqual(payload["urgency_id"], 2)
        self.assertIsNone(result["task"])

    def test_duration_without_time_anchor_is_rejected(self) -> None:
        service = self.make_service()
        with self.assertRaisesRegex(RahtalValidationError, "duration alone"):
            service.create_performance(
                task_id=12,
                title_id=4,
                summary="Work",
                duration_minutes=60,
            )

    def test_invalid_duration_or_interval_is_rejected(self) -> None:
        service = self.make_service()
        for duration in (0, -1, 1440):
            with self.subTest(duration=duration):
                with self.assertRaises(RahtalValidationError):
                    service.create_performance(
                        task_id=12,
                        title_id=4,
                        summary="Work",
                        start_time="09:00",
                        duration_minutes=duration,
                    )

    def test_invalid_task_prevents_write(self) -> None:
        client = FakeClient()
        service = self.make_service(
            client=client, tasks=FakeTasks(RahtalNotFoundError("Task not found."))
        )
        with self.assertRaises(RahtalNotFoundError):
            service.create_performance(
                task_id=999,
                title_id=4,
                summary="Work",
                start_time="09:00",
                end_time="10:00",
            )
        self.assertEqual(client.posts, [])

    def test_authentication_failure_prevents_write(self) -> None:
        client = FakeClient()
        service = self.make_service(
            client=client,
            auth=FakeAuth(RahtalAuthenticationError("Authentication failed.")),
        )
        with self.assertRaises(RahtalAuthenticationError):
            service.create_performance(
                task_id=12,
                title_id=4,
                summary="Work",
                start_time="09:00",
                end_time="10:00",
            )
        self.assertEqual(client.posts, [])

    def test_backend_validation_error_is_returned(self) -> None:
        client = FakeClient()
        client.post_error = RahtalValidationError("The time range overlaps.")
        service = self.make_service(client=client)
        with self.assertRaisesRegex(RahtalValidationError, "overlaps"):
            service.create_performance(
                task_id=12,
                title_id=4,
                summary="Work",
                start_time="09:00",
                end_time="10:00",
            )
        self.assertEqual(len(client.posts), 1)

    def test_performance_title_lookup_returns_real_ids(self) -> None:
        result = self.make_service().list_titles(search="Develop")
        self.assertEqual(result, {"items": [{"id": 4, "name": "Development"}], "count": 1})


if __name__ == "__main__":
    unittest.main()
