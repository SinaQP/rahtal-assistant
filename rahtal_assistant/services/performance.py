"""Validated, non-retrying performance creation."""

from __future__ import annotations

from typing import Any

import jdatetime

from ..client import RahtalClient
from ..errors import RahtalError, RahtalMalformedResponseError, RahtalValidationError
from .auth import AuthService
from .tasks import TaskService
from .time_utils import resolve_time_interval, to_rahtal_jalali_date


MAX_BATCH_SIZE = 100
DEFAULT_MAX_DAYS_ALLOWED = 3


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

    def max_days_allowed(self) -> int:
        data = self.client.get("functionality/activity-settings/max-day")
        value = data.get("max_days_allowed") if isinstance(data, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return DEFAULT_MAX_DAYS_ALLOWED
        return value

    def list_performance(
        self,
        *,
        start_date: str | None = "today",
        end_date: str | None = None,
        date_calendar: str = "gregorian",
    ) -> dict[str, Any]:
        start = to_rahtal_jalali_date(start_date, calendar=date_calendar)
        end = (
            to_rahtal_jalali_date(end_date, calendar=date_calendar)
            if end_date is not None
            else start
        )
        if _jalali(end) < _jalali(start):
            raise RahtalValidationError("end_date must not be before start_date.")
        if (_jalali(end) - _jalali(start)).days > 62:
            raise RahtalValidationError("The date range may span at most 62 days.")

        records = [
            _compact_record(item) for item in self._fetch_range(start, end)
        ]
        records.sort(key=lambda item: (item["date"], item["start_time"] or ""))

        days: dict[str, dict[str, Any]] = {}
        for record in records:
            day = days.setdefault(
                record["date"], {"date": record["date"], "count": 0, "minutes": 0}
            )
            day["count"] += 1
            day["minutes"] += record["duration_minutes"] or 0
        return {
            "start_date": start,
            "end_date": end,
            "date_calendar": "jalali",
            "items": records,
            "days": list(days.values()),
            "count": len(records),
            "total_minutes": sum(day["minutes"] for day in days.values()),
        }

    def update_performance(
        self,
        record_id: int,
        *,
        summary: str | None = None,
        title_id: int | None = None,
        task_id: int | None = None,
        clear_task: bool = False,
        date: str | None = None,
        date_calendar: str = "gregorian",
        start_time: str | None = None,
        end_time: str | None = None,
        customer_id: int | None = None,
        product_id: int | None = None,
        importance_id: int | None = None,
        urgency_id: int | None = None,
    ) -> dict[str, Any]:
        self._positive_id(record_id, "record_id")
        if task_id is not None and clear_task:
            raise RahtalValidationError("Pass either task_id or clear_task, not both.")
        for value, name in (
            (title_id, "title_id"),
            (task_id, "task_id"),
            (customer_id, "customer_id"),
            (product_id, "product_id"),
            (importance_id, "importance_id"),
            (urgency_id, "urgency_id"),
        ):
            if value is not None:
                self._positive_id(value, name)

        current = self._get_record(record_id)
        jalali_date = (
            to_rahtal_jalali_date(date, calendar=date_calendar)
            if date is not None
            else current["date"]
        )
        resolved_start, resolved_end, _ = resolve_time_interval(
            start_time=start_time or current["start_time"],
            end_time=end_time or current["end_time"],
            duration_minutes=None,
        )

        # The backend's overlap check reads date and times even on PATCH, so
        # they are always sent; every other field is sent only when changing.
        payload: dict[str, Any] = {
            "date": jalali_date,
            "start_time": resolved_start,
            "end_time": resolved_end,
        }
        if summary is not None:
            clean_summary = summary.strip()
            if not clean_summary:
                raise RahtalValidationError("summary must not be empty.")
            payload["summary"] = clean_summary
        if task_id is not None:
            self.tasks.get_task(task_id)
            payload["order_id"] = task_id
        elif clear_task:
            payload["order_id"] = None
        for name, value in (
            ("title_id", title_id),
            ("customer_id", customer_id),
            ("product_id", product_id),
            ("importance_id", importance_id),
            ("urgency_id", urgency_id),
        ):
            if value is not None:
                payload[name] = value

        updated = self.client.patch(
            f"functionality/activity/{record_id}/", json_body=payload
        )
        if not isinstance(updated, dict) or updated.get("id") != record_id:
            raise RahtalMalformedResponseError(
                "Rahtal updated the performance entry but returned an invalid result."
            )
        return {"before": current, "after": _compact_record(updated)}

    def delete_performance(self, record_id: int) -> dict[str, Any]:
        self._positive_id(record_id, "record_id")
        current = self._get_record(record_id)
        self.client.delete(f"functionality/activities/{record_id}/")
        return {"deleted": True, "record": current}

    def create_performance_batch(
        self,
        entries: list[dict[str, Any]],
        *,
        date_calendar: str = "gregorian",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(entries, list) or not entries:
            raise RahtalValidationError("entries must be a non-empty list.")
        if len(entries) > MAX_BATCH_SIZE:
            raise RahtalValidationError(
                f"A batch may contain at most {MAX_BATCH_SIZE} entries."
            )

        prepared, problems = self._prepare_batch(entries, date_calendar)
        result: dict[str, Any] = {
            "dry_run": dry_run,
            "entry_count": len(entries),
            "total_minutes": sum(item["duration_minutes"] for item in prepared),
            "problems": problems,
            "valid": not problems,
        }
        if dry_run or problems:
            result["entries"] = prepared
            result["created"] = []
            return result

        created: list[dict[str, Any]] = []
        for index, entry in enumerate(prepared):
            try:
                record = self.create_performance(
                    title_id=entry["title_id"],
                    summary=entry["summary"],
                    task_id=entry["task_id"],
                    date=entry["date"],
                    date_calendar="jalali",
                    start_time=entry["start_time"],
                    end_time=entry["end_time"],
                    customer_id=entry["customer_id"],
                    product_id=entry["product_id"],
                    importance_id=entry["importance_id"],
                    urgency_id=entry["urgency_id"],
                )
            except RahtalError as exc:
                # Stop at the first failure and never retry: the failed write
                # may have landed, and later entries may depend on this one.
                result["created"] = created
                result["failed"] = {
                    "index": index,
                    "entry": entry,
                    "error": f"{exc.code}: {exc.message}",
                    "outcome_uncertain": not isinstance(exc, RahtalValidationError),
                }
                result["not_attempted"] = len(prepared) - index - 1
                return result
            created.append(
                {
                    "index": index,
                    "id": record["id"],
                    "date": entry["date"],
                    "start_time": entry["start_time"],
                    "end_time": entry["end_time"],
                }
            )
        result["created"] = created
        return result

    def _prepare_batch(
        self, entries: list[dict[str, Any]], date_calendar: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        prepared: list[dict[str, Any]] = []
        problems: list[str] = []
        task_ids: set[int] = set()

        for index, raw in enumerate(entries):
            if not isinstance(raw, dict):
                problems.append(f"entry {index}: must be an object.")
                continue
            try:
                entry = self._prepare_entry(raw, date_calendar)
            except RahtalError as exc:
                problems.append(f"entry {index}: {exc.message}")
                continue
            entry["index"] = index
            prepared.append(entry)
            if entry["task_id"] is not None:
                task_ids.add(entry["task_id"])

        if not prepared:
            return prepared, problems

        today = _jalali(to_rahtal_jalali_date("today"))
        max_days = self.max_days_allowed()
        for entry in prepared:
            age = (today - _jalali(entry["date"])).days
            if age > max_days:
                problems.append(
                    f"entry {entry['index']}: {entry['date']} is {age} days ago; "
                    f"Rahtal accepts at most {max_days}."
                )

        for task_id in sorted(task_ids):
            try:
                self.tasks.get_task(task_id)
            except RahtalError as exc:
                problems.append(f"task {task_id}: {exc.message}")

        dates = sorted({entry["date"] for entry in prepared})
        existing = [
            _compact_record(item) for item in self._fetch_range(dates[0], dates[-1])
        ]
        for entry in prepared:
            for other in prepared:
                if other["index"] < entry["index"] and _overlaps(entry, other):
                    problems.append(
                        f"entry {entry['index']}: overlaps entry {other['index']} "
                        f"on {entry['date']}."
                    )
            for record in existing:
                if _overlaps(entry, record):
                    problems.append(
                        f"entry {entry['index']}: overlaps existing record "
                        f"{record['id']} ({record['start_time']}-{record['end_time']}) "
                        f"on {entry['date']}."
                    )
        return prepared, problems

    def _prepare_entry(
        self, raw: dict[str, Any], date_calendar: str
    ) -> dict[str, Any]:
        unknown = set(raw) - {
            "title_id", "summary", "task_id", "date", "start_time", "end_time",
            "duration_minutes", "customer_id", "product_id", "importance_id",
            "urgency_id",
        }
        if unknown:
            raise RahtalValidationError(f"unknown fields: {', '.join(sorted(unknown))}.")
        title_id = raw.get("title_id")
        self._positive_id(title_id, "title_id")
        for name in ("task_id", "customer_id", "product_id", "importance_id", "urgency_id"):
            if raw.get(name) is not None:
                self._positive_id(raw[name], name)
        summary = raw.get("summary")
        clean_summary = summary.strip() if isinstance(summary, str) else ""
        if not clean_summary:
            raise RahtalValidationError("summary is required.")
        if raw.get("date") is None:
            raise RahtalValidationError("date is required in a batch entry.")
        start, end, minutes = resolve_time_interval(
            start_time=raw.get("start_time"),
            end_time=raw.get("end_time"),
            duration_minutes=raw.get("duration_minutes"),
        )
        return {
            "date": to_rahtal_jalali_date(raw["date"], calendar=date_calendar),
            "start_time": start,
            "end_time": end,
            "duration_minutes": minutes,
            "title_id": title_id,
            "task_id": raw.get("task_id"),
            "summary": clean_summary,
            "customer_id": raw.get("customer_id"),
            "product_id": raw.get("product_id"),
            "importance_id": raw.get("importance_id", 2),
            "urgency_id": raw.get("urgency_id", 2),
        }

    def _fetch_range(self, start: str, end: str) -> list[dict[str, Any]]:
        data = self.client.get(
            "functionality/get_activities/",
            params={"start_date": start, "end_date": end},
        )
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            data = data["results"]
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid performance-list response."
            )
        return data

    def _get_record(self, record_id: int) -> dict[str, Any]:
        data = self.client.get(f"functionality/activity/{record_id}/")
        if not isinstance(data, dict) or data.get("id") != record_id:
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid performance-detail response."
            )
        return _compact_record(data)

    @staticmethod
    def _positive_id(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RahtalValidationError(f"{name} must be a positive integer.")


def _jalali(value: str) -> jdatetime.date:
    year, month, day = (int(part) for part in value.split("-"))
    return jdatetime.date(year, month, day)


def _hhmm(value: Any) -> str | None:
    return value[:5] if isinstance(value, str) and len(value) >= 5 else None


def _minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:5])


def _overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if first["date"] != second["date"]:
        return False
    if not all((first["start_time"], first["end_time"], second["start_time"], second["end_time"])):
        return False
    return (
        _minutes(first["start_time"]) < _minutes(second["end_time"])
        and _minutes(second["start_time"]) < _minutes(first["end_time"])
    )


def _compact_record(data: dict[str, Any]) -> dict[str, Any]:
    start = _hhmm(data.get("start_time"))
    end = _hhmm(data.get("end_time"))
    title = data.get("title") if isinstance(data.get("title"), dict) else {}
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else None
    product = data.get("product") if isinstance(data.get("product"), dict) else None
    return {
        "id": data.get("id"),
        "date": data.get("date"),
        "start_time": start,
        "end_time": end,
        "duration_minutes": _minutes(end) - _minutes(start) if start and end else None,
        "title": {"id": title.get("id"), "name": title.get("name")} if title else None,
        "summary": data.get("summary"),
        "task_code": data.get("order_code"),
        "task_title": data.get("order_title"),
        "customer": {"id": customer.get("id"), "name": customer.get("name")} if customer else None,
        "product": {"id": product.get("id"), "name": product.get("name")} if product else None,
    }
