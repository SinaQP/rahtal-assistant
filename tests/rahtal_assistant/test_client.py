from __future__ import annotations

import base64
import json
import time
import unittest

import httpx

from rahtal_assistant.client import RahtalClient
from rahtal_assistant.config import RahtalSettings
from rahtal_assistant.errors import (
    RahtalAuthenticationError,
    RahtalValidationError,
)


def make_jwt(expires_at: float) -> str:
    def encode(data: dict[str, object]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': expires_at})}.signature"


SETTINGS = RahtalSettings(
    base_url="https://rahtal.test/",
    username="employee",
    password="top-secret-password",
)


def build_client(handler) -> RahtalClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url=SETTINGS.base_url, transport=transport)
    return RahtalClient(SETTINGS, http_client=http)


class RahtalClientTests(unittest.TestCase):
    def test_successful_login_and_token_reuse(self) -> None:
        calls = {"login": 0, "whoami": 0}
        access = make_jwt(time.time() + 3600)
        refresh = make_jwt(time.time() + 7200)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/jwt/create/":
                calls["login"] += 1
                body = json.loads(request.content)
                self.assertEqual(body["username"], "employee")
                self.assertEqual(body["password"], SETTINGS.password)
                return httpx.Response(200, json={"access": access, "refresh": refresh})
            if request.url.path == "/user/auth/user-info/":
                calls["whoami"] += 1
                self.assertEqual(request.headers["Authorization"], f"JWT {access}")
                return httpx.Response(200, json={"id": 7, "full_name": "Test User"})
            raise AssertionError(request.url)

        client = build_client(handler)
        self.assertEqual(client.get("user/auth/user-info/")["id"], 7)
        self.assertEqual(client.get("user/auth/user-info/")["id"], 7)
        self.assertEqual(calls, {"login": 1, "whoami": 2})

    def test_invalid_credentials_are_sanitized(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"message": f"bad password {SETTINGS.password}"},
            )

        client = build_client(handler)
        with self.assertRaises(RahtalAuthenticationError) as caught:
            client.login()
        self.assertNotIn(SETTINGS.password, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_expired_access_token_is_refreshed(self) -> None:
        calls = {"refresh": 0, "login": 0}
        renewed_access = make_jwt(time.time() + 3600)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/jwt/refresh/":
                calls["refresh"] += 1
                return httpx.Response(200, json={"access": renewed_access})
            if request.url.path == "/auth/jwt/create/":
                calls["login"] += 1
                raise AssertionError("login should not be needed")
            if request.url.path == "/user/auth/user-info/":
                self.assertEqual(
                    request.headers["Authorization"], f"JWT {renewed_access}"
                )
                return httpx.Response(200, json={"id": 7})
            raise AssertionError(request.url)

        client = build_client(handler)
        client._access_token = make_jwt(time.time() - 60)
        client._refresh_token = make_jwt(time.time() + 3600)
        self.assertEqual(client.get("user/auth/user-info/")["id"], 7)
        self.assertEqual(calls, {"refresh": 1, "login": 0})

    def test_rejected_refresh_falls_back_to_login(self) -> None:
        calls = {"refresh": 0, "login": 0}
        access = make_jwt(time.time() + 3600)
        refresh = make_jwt(time.time() + 7200)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/jwt/refresh/":
                calls["refresh"] += 1
                return httpx.Response(401, json={"message": "expired"})
            if request.url.path == "/auth/jwt/create/":
                calls["login"] += 1
                return httpx.Response(200, json={"access": access, "refresh": refresh})
            if request.url.path == "/user/auth/user-info/":
                return httpx.Response(200, json={"id": 7})
            raise AssertionError(request.url)

        client = build_client(handler)
        client._access_token = make_jwt(time.time() - 60)
        client._refresh_token = make_jwt(time.time() + 3600)
        self.assertEqual(client.get("user/auth/user-info/")["id"], 7)
        self.assertEqual(calls, {"refresh": 1, "login": 1})

    def test_backend_validation_message_is_preserved(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/jwt/create/":
                return httpx.Response(
                    200,
                    json={
                        "access": make_jwt(time.time() + 3600),
                        "refresh": make_jwt(time.time() + 7200),
                    },
                )
            return httpx.Response(400, json={"message": "Duration is required."})

        client = build_client(handler)
        with self.assertRaisesRegex(RahtalValidationError, "Duration is required"):
            client.get("bad/")

    def test_write_is_not_retried_after_401(self) -> None:
        calls = {"write": 0, "refresh": 0}
        client: RahtalClient

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/functionality/activities/":
                calls["write"] += 1
                return httpx.Response(401, json={"message": "expired"})
            if request.url.path == "/auth/jwt/refresh/":
                calls["refresh"] += 1
                return httpx.Response(
                    200, json={"access": make_jwt(time.time() + 3600)}
                )
            raise AssertionError(request.url)

        client = build_client(handler)
        client._access_token = make_jwt(time.time() + 3600)
        client._refresh_token = make_jwt(time.time() + 7200)
        with self.assertRaisesRegex(RahtalAuthenticationError, "was not retried"):
            client.post("functionality/activities/", json_body={"summary": "x"})
        self.assertEqual(calls, {"write": 1, "refresh": 1})


if __name__ == "__main__":
    unittest.main()
