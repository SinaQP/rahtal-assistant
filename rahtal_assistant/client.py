"""Authenticated HTTP client for the existing Rahtal backend."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .config import RahtalSettings
from .errors import (
    RahtalAuthenticationError,
    RahtalAuthorizationError,
    RahtalMalformedResponseError,
    RahtalNotFoundError,
    RahtalUnavailableError,
    RahtalValidationError,
)


logger = logging.getLogger("rahtal_assistant.http")


class RahtalClient:
    """Small synchronous client with in-memory JWT reuse and refresh."""

    def __init__(
        self,
        settings: RahtalSettings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            verify=settings.verify_ssl,
            headers={"Accept": "application/json"},
        )
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> "RahtalClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def login(self) -> None:
        response = self._send(
            "POST",
            "auth/jwt/create/",
            json_body={
                "username": self.settings.username,
                "password": self.settings.password,
            },
        )
        if response.status_code in {400, 401, 403}:
            raise RahtalAuthenticationError(
                self._error_message(response, "Rahtal rejected the configured credentials.")
            )
        self._raise_for_status(response)
        data = self._json_object(response)
        access = data.get("access")
        refresh = data.get("refresh")
        if not isinstance(access, str) or not access:
            raise RahtalMalformedResponseError(
                "Rahtal login succeeded but returned no access token."
            )
        if not isinstance(refresh, str) or not refresh:
            raise RahtalMalformedResponseError(
                "Rahtal login succeeded but returned no refresh token."
            )
        self._access_token = access
        self._refresh_token = refresh

    def refresh(self) -> None:
        if not self._refresh_token or self._token_expired(self._refresh_token):
            self.login()
            return

        response = self._send(
            "POST",
            "auth/jwt/refresh/",
            json_body={"refresh": self._refresh_token},
        )
        if response.status_code in {400, 401, 403}:
            self.login()
            return
        self._raise_for_status(response)
        data = self._json_object(response)
        access = data.get("access")
        if not isinstance(access, str) or not access:
            raise RahtalMalformedResponseError(
                "Rahtal refresh succeeded but returned no access token."
            )
        self._access_token = access
        rotated_refresh = data.get("refresh")
        if isinstance(rotated_refresh, str) and rotated_refresh:
            self._refresh_token = rotated_refresh

    def ensure_authenticated(self) -> None:
        if self._access_token and not self._token_expired(self._access_token):
            return
        if self._refresh_token:
            self.refresh()
        else:
            self.login()

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, *, json_body: Mapping[str, Any]) -> Any:
        return self.request("POST", path, json_body=json_body, write_operation=True)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        write_operation: bool = False,
    ) -> Any:
        self.ensure_authenticated()
        response = self._send_authenticated(
            method, path, params=params, json_body=json_body
        )

        if response.status_code == 401:
            self.refresh()
            if write_operation:
                raise RahtalAuthenticationError(
                    "Rahtal authentication expired during the write. Authentication was "
                    "renewed, but the write was not retried to avoid a duplicate record."
                )
            response = self._send_authenticated(
                method, path, params=params, json_body=json_body
            )

        self._raise_for_status(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RahtalMalformedResponseError(
                "Rahtal returned a non-JSON response."
            ) from exc

    def _send_authenticated(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
    ) -> httpx.Response:
        return self._send(
            method,
            path,
            params=params,
            json_body=json_body,
            headers={"Authorization": f"JWT {self._access_token}"},
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        started = time.monotonic()
        normalized_path = path.lstrip("/")
        try:
            response = self._http.request(
                method,
                normalized_path,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise RahtalUnavailableError("Rahtal timed out while processing the request.") from exc
        except httpx.RequestError as exc:
            raise RahtalUnavailableError("Rahtal is unavailable or unreachable.") from exc
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            logger.info(
                "rahtal_request method=%s endpoint=/%s duration_ms=%.1f",
                method.upper(),
                normalized_path,
                duration_ms,
            )

        logger.info(
            "rahtal_response method=%s endpoint=/%s status=%s",
            method.upper(),
            normalized_path,
            response.status_code,
        )
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        message = self._error_message(
            response, f"Rahtal request failed with status {response.status_code}."
        )
        if response.status_code == 400:
            raise RahtalValidationError(message)
        if response.status_code == 401:
            raise RahtalAuthenticationError(message)
        if response.status_code == 403:
            raise RahtalAuthorizationError(message)
        if response.status_code == 404:
            raise RahtalNotFoundError(message)
        if response.status_code >= 500:
            raise RahtalUnavailableError(message)
        raise RahtalValidationError(message)

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise RahtalMalformedResponseError(
                "Rahtal returned a malformed JSON response."
            ) from exc
        if not isinstance(data, dict):
            raise RahtalMalformedResponseError(
                "Rahtal returned an unexpected response shape."
            )
        return data

    def _error_message(self, response: httpx.Response, fallback: str) -> str:
        try:
            data = response.json()
        except ValueError:
            return fallback
        message = self._first_message(data)
        return self._redact(message or fallback)

    def _first_message(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            for item in value:
                message = self._first_message(item)
                if message:
                    return message
        if isinstance(value, dict):
            for key in ("message", "detail", "error"):
                if key in value:
                    message = self._first_message(value[key])
                    if message:
                        return message
            for item in value.values():
                message = self._first_message(item)
                if message:
                    return message
        return None

    def _redact(self, message: str) -> str:
        redacted = message
        secrets = (
            self.settings.password,
            self._access_token,
            self._refresh_token,
        )
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    @staticmethod
    def _token_expired(token: str, *, leeway_seconds: int = 30) -> bool:
        try:
            payload_segment = token.split(".")[1]
            padding = "=" * (-len(payload_segment) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(payload_segment + padding).decode("utf-8")
            )
            expires_at = payload.get("exp")
            if not isinstance(expires_at, (int, float)):
                return False
            return expires_at <= time.time() + leeway_seconds
        except (IndexError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return False
