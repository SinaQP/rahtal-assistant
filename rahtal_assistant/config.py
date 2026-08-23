"""Environment-backed configuration for the local Rahtal assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import RahtalConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RahtalConfigurationError(f"{name} must be true or false.")


@dataclass(frozen=True, slots=True)
class RahtalSettings:
    base_url: str
    username: str
    password: str
    timeout_seconds: float = 15.0
    verify_ssl: bool = True

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "RahtalSettings":
        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

        missing = [
            name
            for name in ("RAHTAL_BASE_URL", "RAHTAL_USERNAME", "RAHTAL_PASSWORD")
            if not os.getenv(name, "").strip()
        ]
        if missing:
            raise RahtalConfigurationError(
                "Missing Rahtal configuration: " + ", ".join(missing)
            )

        raw_timeout = os.getenv("RAHTAL_TIMEOUT_SECONDS", "15")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise RahtalConfigurationError(
                "RAHTAL_TIMEOUT_SECONDS must be a positive number."
            ) from exc
        if timeout <= 0:
            raise RahtalConfigurationError(
                "RAHTAL_TIMEOUT_SECONDS must be a positive number."
            )

        base_url = os.environ["RAHTAL_BASE_URL"].strip().rstrip("/") + "/"
        if not base_url.startswith(("http://", "https://")):
            raise RahtalConfigurationError(
                "RAHTAL_BASE_URL must start with http:// or https://."
            )

        return cls(
            base_url=base_url,
            username=os.environ["RAHTAL_USERNAME"].strip(),
            password=os.environ["RAHTAL_PASSWORD"],
            timeout_seconds=timeout,
            verify_ssl=_parse_bool(
                os.getenv("RAHTAL_VERIFY_SSL", "true"), name="RAHTAL_VERIFY_SSL"
            ),
        )
