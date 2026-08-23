"""Validated, non-retrying performance creation."""

from __future__ import annotations

from typing import Any

from ..client import RahtalClient
from ..errors import RahtalMalformedResponseError, RahtalValidationError
from .auth import AuthService
from .tasks import TaskService
from .time_utils import resolve_time_interval, to_rahtal_jalali_date


class PerformanceService:
    def __init__(
        self,
        client: RahtalClient,
        auth_service: AuthService,
        task_service: TaskService,
    ) -> None:
        self.client = client
        self.auth = auth_service
        self.tasks = task_service

    def list_titles(self, *, search: str | None = None) -> dict[str, Any]:
        params = {"search": search.strip()} if search and search.strip() else None
        data = self.client.get("core/title_activity/", params=params)
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            data = data["results"]
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid performance-title response."
            )
        items = [
            {"id": item.get("id"), "name": item.get("name")}
            for item in data
            if isinstance(item.get("id"), int) and isinstance(item.get("name"), str)
        ]
        return {"items": items, "count": len(items)}

    def create_performance(
        self,
        *,
        title_id: int,
        summary: str,
        task_id: int | None = None,
        date: str | None = "today",
        date_calendar: str = "gregorian",
        start_time: str | None = None,
        end_time: str | None = None,
        duration_minutes: int | None = None,
        customer_id: int | None = None,
        product_id: int | None = None,
        importance_id: int | None = 2,
        urgency_id: int | None = 2,
    ) -> dict[str, Any]:
        if task_id is not None:
            self._positive_id(task_id, "task_id")
        self._positive_id(title_id, "title_id")
        for value, name in (
            (customer_id, "customer_id"),
            (product_id, "product_id"),
            (importance_id, "importance_id"),
            (urgency_id, "urgency_id"),
        ):
            if value is not None:
                self._positive_id(value, name)

        clean_summary = summary.strip() if isinstance(summary, str) else ""
        if not clean_summary:
            raise RahtalValidationError("summary is required.")

        task = self.tasks.get_task(task_id) if task_id is not None else None
        current_user = self.auth.whoami()
        jalali_date = to_rahtal_jalali_date(date, calendar=date_calendar)
        resolved_start, resolved_end, resolved_duration = resolve_time_interval(
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
        )

        payload: dict[str, Any] = {
            "user_id": current_user["id"],
            "date": jalali_date,
            "start_time": resolved_start,
            "end_time": resolved_end,
            "title_id": title_id,
            "summary": clean_summary,
            "order_id": task_id,
        }
        for name, value in (
            ("customer_id", customer_id),
            ("product_id", product_id),
            ("importance_id", importance_id),
            ("urgency_id", urgency_id),
        ):
            if value is not None:
                payload[name] = value

        created = self.client.post(
            "functionality/activities/", json_body=payload
        )
        if not isinstance(created, dict) or not isinstance(created.get("id"), int):
            raise RahtalMalformedResponseError(
                "Rahtal created the performance entry but returned an invalid result."
            )

        return {
            "id": created["id"],
            "task": (
                {
                    "id": task["id"],
                    "code": task.get("code"),
                    "title": task.get("title"),
                }
                if task is not None
                else None
            ),
            "date": created.get("date", jalali_date),
            "date_calendar": "jalali",
            "start_time": created.get("start_time", resolved_start),
            "end_time": created.get("end_time", resolved_end),
            "duration_minutes": resolved_duration,
            "summary": created.get("summary", clean_summary),
            "performance_title": created.get("title"),
            "order_code": created.get(
                "order_code", task.get("code") if task is not None else None
            ),
            "raw_created_record": created,
        }

    @staticmethod
    def _positive_id(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RahtalValidationError(f"{name} must be a positive integer.")
