"""Stable, redacted errors for untrusted itinerary inputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ErrorCode(StrEnum):
    """Public error taxonomy.

    Codes are deliberately contextual-detail free. Callers can branch on
    ``code`` without leaking a submitted value, a JSON location, or a local
    filesystem path into logs.
    """

    INPUT_TOO_LARGE = "input_too_large"
    INPUT_TOO_DEEP = "input_too_deep"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    OUT_OF_RANGE = "out_of_range"
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_TIMESTAMP = "invalid_timestamp"
    DUPLICATE_ACTIVITY_ID = "duplicate_activity_id"
    DUPLICATE_TRANSFER = "duplicate_transfer"
    SELF_TRANSFER = "self_transfer"
    UNKNOWN_TRANSFER_REFERENCE = "unknown_transfer_reference"
    DEPENDENCY_CYCLE = "dependency_cycle"
    INFEASIBLE_DEADLINE = "infeasible_deadline"


_ERROR_PREFIX: Final = "tctravel_contract_error"


class ContractError(ValueError):
    """A machine-readable contract failure with a redacted string form."""

    __slots__ = ("code",)

    code: ErrorCode

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(f"{_ERROR_PREFIX}:{code.value}")

    def as_dict(self) -> dict[str, dict[str, str]]:
        """Return a stable JSON-ready payload without untrusted context."""

        return {"error": {"code": self.code.value}}
