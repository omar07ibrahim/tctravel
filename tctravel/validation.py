"""Semantic validation shared by decoding and graph compilation."""

from __future__ import annotations

import heapq
import re
from datetime import datetime
from typing import Final, NoReturn

from .errors import ContractError, ErrorCode
from .model import Activity, DurationInterval, Itinerary, Transfer

SCHEMA_VERSION: Final = 1
MAX_IDENTIFIER_LENGTH: Final = 64
MAX_ACTIVITIES: Final = 128
MAX_TRANSFERS: Final = 512
MAX_HORIZON_SECONDS: Final = 31 * 24 * 60 * 60

_IDENTIFIER_RE: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}", re.ASCII)
_UTC_TIMESTAMP_RE: Final = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z",
    re.ASCII,
)


def _fail(code: ErrorCode) -> NoReturn:
    raise ContractError(code)


def _is_int(value: object) -> bool:
    return type(value) is int


def _valid_identifier(value: object) -> bool:
    return (
        type(value) is str
        and len(value) <= MAX_IDENTIFIER_LENGTH
        and _IDENTIFIER_RE.fullmatch(value) is not None
    )


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def validate_itinerary(itinerary: Itinerary) -> tuple[str, ...]:
    """Validate a model and return its deterministic activity topological order.

    This function validates manually constructed models as strictly as decoded
    models. The returned order uses lexical activity identifiers to break ties.
    """

    if type(itinerary) is not Itinerary:
        _fail(ErrorCode.INVALID_TYPE)
    if not _is_int(itinerary.schema_version):
        _fail(ErrorCode.INVALID_TYPE)
    if itinerary.schema_version != SCHEMA_VERSION:
        _fail(ErrorCode.OUT_OF_RANGE)
    if not _valid_identifier(itinerary.itinerary_id):
        _fail(ErrorCode.INVALID_IDENTIFIER)
    if not _valid_timestamp(itinerary.anchor_time_utc):
        _fail(ErrorCode.INVALID_TIMESTAMP)
    if not _is_int(itinerary.horizon_seconds):
        _fail(ErrorCode.INVALID_TYPE)
    if not 1 <= itinerary.horizon_seconds <= MAX_HORIZON_SECONDS:
        _fail(ErrorCode.OUT_OF_RANGE)
    if type(itinerary.activities) is not tuple:
        _fail(ErrorCode.INVALID_TYPE)
    if not 1 <= len(itinerary.activities) <= MAX_ACTIVITIES:
        _fail(ErrorCode.OUT_OF_RANGE)
    if type(itinerary.transfers) is not tuple:
        _fail(ErrorCode.INVALID_TYPE)
    if len(itinerary.transfers) > MAX_TRANSFERS:
        _fail(ErrorCode.OUT_OF_RANGE)

    activity_by_id: dict[str, Activity] = {}
    for activity in itinerary.activities:
        _validate_activity(activity, itinerary.horizon_seconds)
        if activity.activity_id in activity_by_id:
            _fail(ErrorCode.DUPLICATE_ACTIVITY_ID)
        activity_by_id[activity.activity_id] = activity

    transfer_pairs: set[tuple[str, str]] = set()
    successors = {activity_id: set[str]() for activity_id in activity_by_id}
    incoming: dict[str, list[Transfer]] = {
        activity_id: [] for activity_id in activity_by_id
    }
    indegree = {activity_id: 0 for activity_id in activity_by_id}

    for transfer in itinerary.transfers:
        _validate_transfer(transfer, itinerary.horizon_seconds)
        pair = (transfer.from_activity_id, transfer.to_activity_id)
        if pair in transfer_pairs:
            _fail(ErrorCode.DUPLICATE_TRANSFER)
        transfer_pairs.add(pair)
        if transfer.from_activity_id == transfer.to_activity_id:
            _fail(ErrorCode.SELF_TRANSFER)
        if (
            transfer.from_activity_id not in activity_by_id
            or transfer.to_activity_id not in activity_by_id
        ):
            _fail(ErrorCode.UNKNOWN_TRANSFER_REFERENCE)
        successors[transfer.from_activity_id].add(transfer.to_activity_id)
        incoming[transfer.to_activity_id].append(transfer)
        indegree[transfer.to_activity_id] += 1

    ready = [activity_id for activity_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        activity_id = heapq.heappop(ready)
        order.append(activity_id)
        for successor in sorted(successors[activity_id]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(ready, successor)

    if len(order) != len(activity_by_id):
        _fail(ErrorCode.DEPENDENCY_CYCLE)

    earliest_finish: dict[str, int] = {}
    for activity_id in order:
        activity = activity_by_id[activity_id]
        earliest_start = activity.release_offset_seconds
        for transfer in incoming[activity_id]:
            predecessor_finish = earliest_finish[transfer.from_activity_id]
            earliest_start = max(
                earliest_start, predecessor_finish + transfer.minimum_seconds
            )
        earliest_finish[activity_id] = (
            earliest_start + activity.duration.minimum_seconds
        )
        if earliest_finish[activity_id] > activity.hard_deadline_offset_seconds:
            _fail(ErrorCode.INFEASIBLE_DEADLINE)

    return tuple(order)


def _validate_activity(activity: Activity, horizon_seconds: int) -> None:
    if type(activity) is not Activity:
        _fail(ErrorCode.INVALID_TYPE)
    if not _valid_identifier(activity.activity_id):
        _fail(ErrorCode.INVALID_IDENTIFIER)
    if not _is_int(activity.release_offset_seconds):
        _fail(ErrorCode.INVALID_TYPE)
    if not 0 <= activity.release_offset_seconds <= horizon_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
    if type(activity.duration) is not DurationInterval:
        _fail(ErrorCode.INVALID_TYPE)
    if not _is_int(activity.duration.minimum_seconds) or not _is_int(
        activity.duration.maximum_seconds
    ):
        _fail(ErrorCode.INVALID_TYPE)
    if not 1 <= activity.duration.minimum_seconds <= horizon_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
    if not 1 <= activity.duration.maximum_seconds <= horizon_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
    if activity.duration.minimum_seconds > activity.duration.maximum_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
    if not _is_int(activity.hard_deadline_offset_seconds):
        _fail(ErrorCode.INVALID_TYPE)
    if not 1 <= activity.hard_deadline_offset_seconds <= horizon_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
    if (
        activity.release_offset_seconds + activity.duration.minimum_seconds
        > activity.hard_deadline_offset_seconds
    ):
        _fail(ErrorCode.INFEASIBLE_DEADLINE)


def _validate_transfer(transfer: Transfer, horizon_seconds: int) -> None:
    if type(transfer) is not Transfer:
        _fail(ErrorCode.INVALID_TYPE)
    if not _valid_identifier(transfer.from_activity_id) or not _valid_identifier(
        transfer.to_activity_id
    ):
        _fail(ErrorCode.INVALID_IDENTIFIER)
    if not _is_int(transfer.minimum_seconds):
        _fail(ErrorCode.INVALID_TYPE)
    if not 0 <= transfer.minimum_seconds <= horizon_seconds:
        _fail(ErrorCode.OUT_OF_RANGE)
