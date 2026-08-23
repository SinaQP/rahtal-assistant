"""Assigned-order listing, details, and forgiving local search."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from ..client import RahtalClient
from ..errors import RahtalMalformedResponseError, RahtalValidationError


STATUS_NAMES = {
    1: "todo",
    2: "in_progress",
    3: "done",
    4: "not_done",
}
STATUS_LABELS = {
    1: "برای انجام",
    2: "در حال انجام",
    3: "انجام شد",
    4: "انجام نشد",
}
STATUS_ALIASES = {
    "todo": [1],
    "open": [1],
    "in_progress": [2],
    "in-progress": [2],
    "doing": [2],
    "active": [1, 2],
    "current": [1, 2],
    "done": [3],
    "completed": [3],
    "not_done": [4],
    "not-done": [4],
    "failed": [4],
    "all": [1, 2, 3, 4],
    "برای انجام": [1],
    "در حال انجام": [2],
    "انجام شد": [3],
    "انجام نشد": [4],
}


class TaskService:
    def __init__(self, client: RahtalClient) -> None:
        self.client = client

    def list_my_tasks(
        self,
        *,
        status: str | int | None = "active",
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        commander_id: int | None = None,
        approval_status: str | None = None,
        is_completed: bool | None = None,
    ) -> dict[str, Any]:
        self._validate_pagination(page, page_size)
        status_ids = self.resolve_status_ids(status)
        requested_count = page * page_size
        all_items: list[dict[str, Any]] = []
        total_items = 0
        per_status_totals: dict[str, int] = {}

        for status_id in status_ids:
            response = self._fetch_status(
                status_id,
                page=1 if len(status_ids) > 1 else page,
                count=requested_count if len(status_ids) > 1 else page_size,
                search=search,
                commander_id=commander_id,
                approval_status=approval_status,
                is_completed=is_completed,
            )
            raw_items, status_total, pagination = self._normalize_list_response(response)
            compact = [self._compact_task(item, status_id=status_id) for item in raw_items]
            all_items.extend(compact)
            total_items += status_total
            per_status_totals[STATUS_NAMES[status_id]] = status_total

            if len(status_ids) == 1:
                return {
                    "items": compact,
                    "page": pagination.get("current_page", page),
                    "page_size": page_size,
                    "total_items": status_total,
                    "total_pages": pagination.get("total_pages"),
                    "statuses_queried": [STATUS_NAMES[status_id]],
                    "truncated": False,
                }

        all_items.sort(
            key=lambda item: (item.get("created_at") or "", item.get("code") or 0),
            reverse=True,
        )
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "items": all_items[start:end],
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": (total_items + page_size - 1) // page_size,
            "statuses_queried": [STATUS_NAMES[value] for value in status_ids],
            "per_status_totals": per_status_totals,
            "truncated": len(all_items) < min(total_items, requested_count),
        }

    def search_my_tasks(
        self,
        query: str,
        *,
        status: str | int | None = "all",
        limit: int = 8,
    ) -> dict[str, Any]:
        normalized_query = _normalize_text(query)
        if not normalized_query:
            raise RahtalValidationError("query must not be empty.")
        if limit < 1 or limit > 20:
            raise RahtalValidationError("limit must be between 1 and 20.")

        status_ids = self.resolve_status_ids(status)
        candidates: dict[int, tuple[dict[str, Any], bool]] = {}
        incomplete = False

        for status_id in status_ids:
            direct_response = self._fetch_status(
                status_id, page=1, count=50, search=query
            )
            direct_items, _, _ = self._normalize_list_response(direct_response)
            for item in direct_items:
                compact = self._compact_task(item, status_id=status_id)
                candidates[compact["id"]] = (compact, True)

            pool_response = self._fetch_status(status_id, page=1, count=200)
            pool_items, pool_total, _ = self._normalize_list_response(pool_response)
            incomplete = incomplete or pool_total > len(pool_items)
            for item in pool_items:
                compact = self._compact_task(item, status_id=status_id)
                candidates.setdefault(compact["id"], (compact, False))

        scored: list[dict[str, Any]] = []
        for task, backend_match in candidates.values():
            score = self._score_task(normalized_query, task)
            if backend_match:
                score = max(score, 0.9)
            if score >= 0.34:
                scored.append({**task, "match_score": round(score, 3)})

        scored.sort(key=lambda item: (-item["match_score"], -(item.get("code") or 0)))
        results = scored[:limit]
        ambiguous = (
            len(results) > 1
            and results[0]["match_score"] >= 0.72
            and results[1]["match_score"] >= 0.72
            and results[0]["match_score"] - results[1]["match_score"] <= 0.08
        )
        return {
            "query": query,
            "candidates": results,
            "ambiguous": ambiguous,
            "no_match": not results,
            "candidate_pool_incomplete": incomplete,
            "instruction": (
                "Ask the user to choose a task ID from these candidates."
                if ambiguous
                else "Use only an ID present in candidates."
            ),
        }

    def get_task(self, task_id: int) -> dict[str, Any]:
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
            raise RahtalValidationError("task_id must be a positive integer.")
        data = self.client.get(f"leadership/get-order-details/{task_id}/")
        if not isinstance(data, dict) or data.get("id") != task_id:
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid task-detail response."
            )
        status_id = self._status_id_from_tags(data.get("tags"))
        task = self._compact_task(data, status_id=status_id)
        task["comments"] = data.get("comments", [])
        task["files"] = data.get("files", [])
        task["reminder"] = data.get("reminders")
        return task

    def resolve_status_ids(self, status: str | int | None) -> list[int]:
        if status is None:
            return [1, 2]
        if isinstance(status, bool):
            raise RahtalValidationError("status is not valid.")
        if isinstance(status, int):
            if status in STATUS_NAMES:
                return [status]
            raise RahtalValidationError("status ID must be between 1 and 4.")
        normalized = status.strip().lower().replace(" ", "_")
        resolved = STATUS_ALIASES.get(normalized) or STATUS_ALIASES.get(status.strip())
        if not resolved:
            raise RahtalValidationError(
                "status must be todo, in_progress, active, done, not_done, all, or ID 1-4."
            )
        return resolved

    def _fetch_status(
        self,
        status_id: int,
        *,
        page: int,
        count: int,
        search: str | None = None,
        commander_id: int | None = None,
        approval_status: str | None = None,
        is_completed: bool | None = None,
    ) -> Any:
        params: dict[str, Any] = {"page": page, "count": count}
        if search:
            params["search"] = search
        if commander_id is not None:
            params["commander_id"] = commander_id
        if approval_status is not None:
            if approval_status not in {"pending", "approved", "rejected"}:
                raise RahtalValidationError(
                    "approval_status must be pending, approved, or rejected."
                )
            params["approval_status"] = approval_status
        if is_completed is not None:
            params["is_completed"] = str(is_completed).lower()
        return self.client.get(
            f"leadership/get-orders/status/{status_id}/", params=params
        )

    def _normalize_list_response(
        self, response: Any
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        if isinstance(response, list):
            if not all(isinstance(item, dict) for item in response):
                raise RahtalMalformedResponseError(
                    "Rahtal returned an invalid task-list response."
                )
            return response, len(response), {}
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid paginated task-list response."
            )
        items = response["results"]
        if not all(isinstance(item, dict) for item in items):
            raise RahtalMalformedResponseError(
                "Rahtal returned invalid task-list items."
            )
        total = response.get("total_items", len(items))
        return items, total if isinstance(total, int) else len(items), response

    def _compact_task(
        self, data: dict[str, Any], *, status_id: int | None
    ) -> dict[str, Any]:
        task_id = data.get("id")
        if not isinstance(task_id, int):
            raise RahtalMalformedResponseError("A Rahtal task has no valid ID.")
        return {
            "id": task_id,
            "code": data.get("code"),
            "title": data.get("order_title"),
            "status": (
                {
                    "id": status_id,
                    "name": STATUS_NAMES.get(status_id),
                    "label": STATUS_LABELS.get(status_id),
                }
                if status_id
                else None
            ),
            "details": data.get("details"),
            "created_at": data.get("created_at"),
            "deadline": data.get("days_until_deadline"),
            "commander": data.get("commander_full_name"),
            "performer": data.get("performer_full_name"),
            "approval_status": data.get("approval_status"),
            "is_completed": data.get("is_completed"),
            "tags": data.get("tags", []),
            "has_comment": data.get("has_comment"),
        }

    def _score_task(self, query: str, task: dict[str, Any]) -> float:
        code = _normalize_text(str(task.get("code") or ""))
        if query == code:
            return 1.0

        weighted_fields = (
            (task.get("title"), 1.0),
            (task.get("details"), 0.9),
            (task.get("commander"), 0.65),
            (task.get("performer"), 0.55),
        )
        best = 0.0
        query_tokens = set(query.split())
        for raw_value, weight in weighted_fields:
            value = _normalize_text(str(raw_value or ""))
            if not value:
                continue
            if query == value:
                field_score = 1.0
            elif query in value:
                field_score = 0.92
            elif value in query:
                field_score = 0.86
            else:
                ratio = SequenceMatcher(None, query, value).ratio()
                tokens = set(value.split())
                overlap = len(query_tokens & tokens) / max(len(query_tokens), 1)
                field_score = max(ratio, overlap * 0.88)
            best = max(best, field_score * weight)
        return best

    def _status_id_from_tags(self, tags: Any) -> int | None:
        if not isinstance(tags, list):
            return None
        label_to_id = {label: status_id for status_id, label in STATUS_LABELS.items()}
        for tag in tags:
            if isinstance(tag, dict) and tag.get("value") in label_to_id:
                return label_to_id[tag["value"]]
        return None

    def _validate_pagination(self, page: int, page_size: int) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > 20:
            raise RahtalValidationError("page must be an integer between 1 and 20.")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size < 1
            or page_size > 50
        ):
            raise RahtalValidationError(
                "page_size must be an integer between 1 and 50."
            )


def _normalize_text(value: str) -> str:
    normalized = (
        value.casefold()
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
    )
    normalized = re.sub(r"[^\w\s]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())
