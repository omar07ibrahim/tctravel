"""Strict JSON decoding and canonical encoding for itinerary contract v1."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Final, NoReturn, TypeVar, cast

from .errors import ContractError, ErrorCode
from .model import Activity, DurationInterval, Itinerary, Transfer
from .validation import (
    MAX_ACTIVITIES,
    MAX_HORIZON_SECONDS,
    MAX_TRANSFERS,
    SCHEMA_VERSION,
    validate_itinerary,
)

MAX_INPUT_BYTES: Final = 64 * 1024
MAX_JSON_DEPTH: Final = 24
MAX_INTEGER_DIGITS: Final = 10

_ROOT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "itinerary_id",
        "anchor_time_utc",
        "horizon_seconds",
        "activities",
        "transfers",
    }
)
_ACTIVITY_FIELDS: Final = frozenset(
    {
        "id",
        "release_offset_seconds",
        "duration",
        "hard_deadline_offset_seconds",
    }
)
_DURATION_FIELDS: Final = frozenset({"minimum_seconds", "maximum_seconds"})
_TRANSFER_FIELDS: Final = frozenset(
    {"from_activity_id", "to_activity_id", "minimum_seconds"}
)

T = TypeVar("T")


class _DuplicateKey(ValueError):
    pass


class _NonIntegerNumber(ValueError):
    pass


class _IntegerTooLong(ValueError):
    pass


def _fail(code: ErrorCode) -> NoReturn:
    raise ContractError(code)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _parse_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_INTEGER_DIGITS:
        raise _IntegerTooLong
    return int(token)


def _reject_non_integer(_: str) -> NoReturn:
    raise _NonIntegerNumber


def _decode_text(data: bytes | str) -> str:
    if type(data) is bytes:
        if len(data) > MAX_INPUT_BYTES:
            _fail(ErrorCode.INPUT_TOO_LARGE)
        decoded: str | None = None
        try:
            decoded = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if decoded is None:
            _fail(ErrorCode.INVALID_UTF8)
        return decoded
    if type(data) is str:
        encoded: bytes | None = None
        try:
            encoded = data.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            pass
        if encoded is None:
            _fail(ErrorCode.INVALID_UTF8)
        if len(encoded) > MAX_INPUT_BYTES:
            _fail(ErrorCode.INPUT_TOO_LARGE)
        return data
    _fail(ErrorCode.INVALID_TYPE)


def _check_json_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _fail(ErrorCode.INPUT_TOO_DEEP)
        elif character in "]}":
            depth -= 1
            if depth < 0:
                _fail(ErrorCode.INVALID_JSON)
    if in_string or depth != 0:
        _fail(ErrorCode.INVALID_JSON)


def _decode_json(text: str) -> object:
    _check_json_depth(text)
    failure_code: ErrorCode
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_parse_integer,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
        )
    except _DuplicateKey:
        failure_code = ErrorCode.DUPLICATE_JSON_KEY
    except _IntegerTooLong:
        failure_code = ErrorCode.OUT_OF_RANGE
    except _NonIntegerNumber:
        failure_code = ErrorCode.INVALID_TYPE
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        failure_code = ErrorCode.INVALID_JSON
    _fail(failure_code)


def _require_object(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        _fail(ErrorCode.INVALID_TYPE)
    value = cast(dict[str, object], value)
    actual = frozenset(value)
    if actual - fields:
        _fail(ErrorCode.UNKNOWN_FIELD)
    if fields - actual:
        _fail(ErrorCode.MISSING_FIELD)
    return value


def _require_list(
    value: object, *, minimum: int, maximum: int
) -> list[object]:
    if type(value) is not list:
        _fail(ErrorCode.INVALID_TYPE)
    result = cast(list[object], value)
    if not minimum <= len(result) <= maximum:
        _fail(ErrorCode.OUT_OF_RANGE)
    return result


def _require_string(value: object) -> str:
    if type(value) is not str:
        _fail(ErrorCode.INVALID_TYPE)
    return value


def _require_integer(value: object) -> int:
    if type(value) is not int:
        _fail(ErrorCode.INVALID_TYPE)
    return value


def _decode_each(
    values: list[object], decoder: Callable[[object], T]
) -> tuple[T, ...]:
    return tuple(decoder(value) for value in values)


def _decode_activity(value: object) -> Activity:
    item = _require_object(value, _ACTIVITY_FIELDS)
    duration_item = _require_object(item["duration"], _DURATION_FIELDS)
    return Activity(
        activity_id=_require_string(item["id"]),
        release_offset_seconds=_require_integer(
            item["release_offset_seconds"]
        ),
        duration=DurationInterval(
            minimum_seconds=_require_integer(duration_item["minimum_seconds"]),
            maximum_seconds=_require_integer(duration_item["maximum_seconds"]),
        ),
        hard_deadline_offset_seconds=_require_integer(
            item["hard_deadline_offset_seconds"]
        ),
    )


def _decode_transfer(value: object) -> Transfer:
    item = _require_object(value, _TRANSFER_FIELDS)
    return Transfer(
        from_activity_id=_require_string(item["from_activity_id"]),
        to_activity_id=_require_string(item["to_activity_id"]),
        minimum_seconds=_require_integer(item["minimum_seconds"]),
    )


def decode_itinerary_json(data: bytes | str) -> Itinerary:
    """Decode untrusted JSON into a validated, order-normalized model."""

    root = _require_object(_decode_json(_decode_text(data)), _ROOT_FIELDS)
    activities = _decode_each(
        _require_list(
            root["activities"], minimum=1, maximum=MAX_ACTIVITIES
        ),
        _decode_activity,
    )
    transfers = _decode_each(
        _require_list(
            root["transfers"], minimum=0, maximum=MAX_TRANSFERS
        ),
        _decode_transfer,
    )
    itinerary = Itinerary(
        schema_version=_require_integer(root["schema_version"]),
        itinerary_id=_require_string(root["itinerary_id"]),
        anchor_time_utc=_require_string(root["anchor_time_utc"]),
        horizon_seconds=_require_integer(root["horizon_seconds"]),
        activities=tuple(sorted(activities, key=lambda item: item.activity_id)),
        transfers=tuple(
            sorted(
                transfers,
                key=lambda item: (
                    item.from_activity_id,
                    item.to_activity_id,
                    item.minimum_seconds,
                ),
            )
        ),
    )
    validate_itinerary(itinerary)
    return itinerary


def _itinerary_document(itinerary: Itinerary) -> dict[str, object]:
    validate_itinerary(itinerary)
    return {
        "activities": [
            {
                "duration": {
                    "maximum_seconds": activity.duration.maximum_seconds,
                    "minimum_seconds": activity.duration.minimum_seconds,
                },
                "hard_deadline_offset_seconds": (
                    activity.hard_deadline_offset_seconds
                ),
                "id": activity.activity_id,
                "release_offset_seconds": activity.release_offset_seconds,
            }
            for activity in sorted(
                itinerary.activities, key=lambda item: item.activity_id
            )
        ],
        "anchor_time_utc": itinerary.anchor_time_utc,
        "horizon_seconds": itinerary.horizon_seconds,
        "itinerary_id": itinerary.itinerary_id,
        "schema_version": itinerary.schema_version,
        "transfers": [
            {
                "from_activity_id": transfer.from_activity_id,
                "minimum_seconds": transfer.minimum_seconds,
                "to_activity_id": transfer.to_activity_id,
            }
            for transfer in sorted(
                itinerary.transfers,
                key=lambda item: (
                    item.from_activity_id,
                    item.to_activity_id,
                    item.minimum_seconds,
                ),
            )
        ],
    }


def canonical_itinerary_bytes(itinerary: Itinerary) -> bytes:
    """Encode semantic content into one byte-stable JSON representation."""

    return json.dumps(
        _itinerary_document(itinerary),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def itinerary_digest(itinerary: Itinerary) -> str:
    """Return the lowercase SHA-256 digest of canonical itinerary bytes."""

    return hashlib.sha256(canonical_itinerary_bytes(itinerary)).hexdigest()
