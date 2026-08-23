"""Sanitized exception types exposed by the Rahtal assistant."""

from __future__ import annotations

from typing import Any


class RahtalError(Exception):
    """Base class for errors safe to show through MCP."""

    code = "rahtal_error"

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class RahtalConfigurationError(RahtalError):
    code = "configuration_error"


class RahtalAuthenticationError(RahtalError):
    code = "authentication_error"


class RahtalAuthorizationError(RahtalError):
    code = "authorization_error"


class RahtalValidationError(RahtalError):
    code = "validation_error"


class RahtalNotFoundError(RahtalError):
    code = "not_found"


class RahtalUnavailableError(RahtalError):
    code = "api_unavailable"


class RahtalMalformedResponseError(RahtalError):
    code = "malformed_response"


class RahtalDuplicateWriteError(RahtalError):
    code = "duplicate_write"
