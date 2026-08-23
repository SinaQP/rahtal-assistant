"""Authenticated-user operations."""

from __future__ import annotations

from typing import Any

from ..client import RahtalClient
from ..errors import RahtalMalformedResponseError


class AuthService:
    def __init__(self, client: RahtalClient) -> None:
        self.client = client
        self._cached_user: dict[str, Any] | None = None

    def whoami(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._cached_user is not None and not refresh:
            return dict(self._cached_user)

        data = self.client.get("user/auth/user-info/")
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise RahtalMalformedResponseError(
                "Rahtal returned an invalid current-user response."
            )

        user = {
            "id": data["id"],
            "full_name": data.get("full_name"),
            "is_active": data.get("is_active"),
            "is_admin": data.get("is_admin"),
            "role_id": data.get("role"),
            "actions": data.get("actions", []),
        }
        self._cached_user = user
        return dict(user)
