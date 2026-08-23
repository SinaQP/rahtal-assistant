"""Mocked vertical-slice verification from JWT login through performance write."""

from __future__ import annotations

import json
import time
import unittest

import httpx

from rahtal_assistant.client import RahtalClient
from rahtal_assistant.config import RahtalSettings
from rahtal_assistant.services import AuthService, PerformanceService, TaskService
from test_client import make_jwt


TASK = {
    "id": 12,
    "code": 322,
    "order_title": "QuerySmith report API",
    "details": "Implement tire-search reporting endpoints",
    "created_at": "1405-05-20",
    "days_until_deadline": "1405-05-25",
    "commander_full_name": "Commander One",
    "performer_full_name": "Employee One",
    "approval_status": "pending",
    "is_completed": False,
    "tags": [{"key": "وضعیت", "value": "در حال انجام", "color": "#af8a22"}],
    "has_comment": False,
    "comments": [],
    "files": [],
    "reminders": None,
}


class MockedEndToEndTests(unittest.TestCase):
    def test_login_tasks_search_detail_and_performance_creation(self) -> None:
        writes: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/auth/jwt/create/":
                return httpx.Response(
                    200,
                    json={
                        "access": make_jwt(time.time() + 3600),
                        "refresh": make_jwt(time.time() + 7200),
                    },
                )
            if path == "/user/auth/user-info/":
                return httpx.Response(
                    200,
                    json={
                        "id": 7,
                        "full_name": "Employee One",
                        "is_active": True,
                        "is_admin": False,
                        "role": 3,
                        "actions": ["GET_ORDERS", "CREATE_ACTIVITY"],
                    },
                )
            if path == "/leadership/get-orders/status/1/":
                return httpx.Response(
                    200,
                    json={
                        "total_pages": 0,
                        "total_items": 0,
                        "current_page": 1,
                        "next_page": None,
                        "previous_page": None,
                        "results": [],
                    },
                )
            if path == "/leadership/get-orders/status/2/":
                return httpx.Response(
                    200,
                    json={
                        "total_pages": 1,
                        "total_items": 1,
                        "current_page": 1,
                        "next_page": None,
                        "previous_page": None,
                        "results": [TASK],
                    },
                )
            if path == "/leadership/get-order-details/12/":
                return httpx.Response(200, json=TASK)
            if path == "/core/title_activity/":
                return httpx.Response(200, json=[{"id": 4, "name": "Development"}])
            if path == "/functionality/activities/":
                payload = json.loads(request.content)
                writes.append(payload)
                return httpx.Response(
                    201,
                    json={
                        "id": 81,
                        **payload,
                        "title": {"id": 4, "name": "Development"},
                        "order_code": 322,
                        "order_title": "QuerySmith report API",
                    },
                )
            raise AssertionError(f"Unexpected request: {request.method} {path}")

        settings = RahtalSettings(
            base_url="https://rahtal.test/",
            username="employee",
            password="password",
        )
        http = httpx.Client(
            base_url=settings.base_url,
            transport=httpx.MockTransport(handler),
        )
        client = RahtalClient(settings, http_client=http)
        auth = AuthService(client)
        tasks = TaskService(client)
        performance = PerformanceService(client, auth, tasks)

        self.assertEqual(auth.whoami()["id"], 7)
        self.assertEqual(tasks.list_my_tasks(status="active")["items"][0]["id"], 12)
        search = tasks.search_my_tasks("QuerySmith", status="in_progress")
        self.assertEqual(search["candidates"][0]["id"], 12)
        self.assertEqual(tasks.get_task(12)["code"], 322)
        self.assertEqual(performance.list_titles()["items"][0]["id"], 4)
        created = performance.create_performance(
            task_id=12,
            title_id=4,
            summary="Implemented the report API",
            date="2026-08-11",
            start_time="09:00",
            duration_minutes=60,
        )
        self.assertEqual(created["id"], 81)
        self.assertEqual(writes[0]["order_id"], 12)
        self.assertEqual(writes[0]["end_time"], "10:00")


if __name__ == "__main__":
    unittest.main()
