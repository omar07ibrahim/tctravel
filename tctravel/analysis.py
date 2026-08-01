"""Deterministic interval-feasibility analysis for validated itineraries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .compiler import compile_temporal_graph, graph_digest
from .model import Activity, Itinerary, Transfer

ANALYSIS_SCHEMA_VERSION: Final = 1


class FeasibilityStatus(StrEnum):
    """Deadline classification over all whole-second durations in the intervals."""

    ROBUST = "robust"
    DURATION_SENSITIVE = "duration_sensitive"


@dataclass(frozen=True, slots=True)
class ActivityTiming:
    """One activity's earliest timing under one duration scenario."""

    earliest_start_offset_seconds: int
    earliest_finish_offset_seconds: int
    deadline_slack_seconds: int
    release_is_binding: bool
    binding_predecessor_ids: tuple[str, ...]
    witness_predecessor_id: str | None


@dataclass(frozen=True, slots=True)
class ActivityFeasibility:
    """Best/worst timing envelope and deadline status for one activity."""

    activity_id: str
    release_offset_seconds: int
    minimum_duration_seconds: int
    maximum_duration_seconds: int
    hard_deadline_offset_seconds: int
    status: FeasibilityStatus
    best_case: ActivityTiming
    worst_case: ActivityTiming


@dataclass(frozen=True, slots=True)
class FeasibilityScenario:
    """The tightest deadline and one deterministic witness chain."""

    minimum_deadline_slack_seconds: int
    critical_activity_ids: tuple[str, ...]
    witness_activity_id: str
    witness_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntervalFeasibilityReport:
    """Result-only report produced by :func:`analyze_interval_feasibility`."""

    analysis_schema_version: int
    contract_schema_version: int
    graph_schema_version: int
    itinerary_digest: str
    graph_digest: str
    anchor_time_utc: str
    horizon_seconds: int
    status: FeasibilityStatus
    best_case: FeasibilityScenario
    worst_case: FeasibilityScenario
    activities: tuple[ActivityFeasibility, ...]


def _analyze_scenario(
    *,
    activity_order: tuple[str, ...],
    activity_by_id: dict[str, Activity],
    incoming: dict[str, tuple[Transfer, ...]],
    use_maximum_duration: bool,
) -> tuple[dict[str, ActivityTiming], FeasibilityScenario]:
    timings: dict[str, ActivityTiming] = {}

    for activity_id in activity_order:
        activity = activity_by_id[activity_id]
        earliest_start = activity.release_offset_seconds
        predecessor_candidates: list[tuple[str, int]] = []
        for transfer in incoming[activity_id]:
            predecessor_finish = timings[
                transfer.from_activity_id
            ].earliest_finish_offset_seconds
            candidate = predecessor_finish + transfer.minimum_seconds
            predecessor_candidates.append((transfer.from_activity_id, candidate))
            if candidate > earliest_start:
                earliest_start = candidate

        binding_predecessors = tuple(
            predecessor_id
            for predecessor_id, candidate in predecessor_candidates
            if candidate == earliest_start
        )
        duration = (
            activity.duration.maximum_seconds
            if use_maximum_duration
            else activity.duration.minimum_seconds
        )
        earliest_finish = earliest_start + duration
        timings[activity_id] = ActivityTiming(
            earliest_start_offset_seconds=earliest_start,
            earliest_finish_offset_seconds=earliest_finish,
            deadline_slack_seconds=(
                activity.hard_deadline_offset_seconds - earliest_finish
            ),
            release_is_binding=(
                activity.release_offset_seconds == earliest_start
            ),
            binding_predecessor_ids=binding_predecessors,
            witness_predecessor_id=(
                binding_predecessors[0] if binding_predecessors else None
            ),
        )

    minimum_slack = min(
        timing.deadline_slack_seconds for timing in timings.values()
    )
    critical_activity_ids = tuple(
        sorted(
            activity_id
            for activity_id, timing in timings.items()
            if timing.deadline_slack_seconds == minimum_slack
        )
    )
    witness_activity_id = critical_activity_ids[0]
    reverse_chain: list[str] = []
    visited: set[str] = set()
    cursor: str | None = witness_activity_id
    while cursor is not None:
        if cursor in visited:
            raise RuntimeError("validated witness chain contains a cycle")
        visited.add(cursor)
        reverse_chain.append(cursor)
        cursor = timings[cursor].witness_predecessor_id

    return timings, FeasibilityScenario(
        minimum_deadline_slack_seconds=minimum_slack,
        critical_activity_ids=critical_activity_ids,
        witness_activity_id=witness_activity_id,
        witness_chain=tuple(reversed(reverse_chain)),
    )


def analyze_interval_feasibility(
    itinerary: Itinerary,
) -> IntervalFeasibilityReport:
    """Compute deterministic min/max earliest-time deadline envelopes.

    Validation already rejects a schedule that misses a deadline at minimum
    duration. Because releases and transfer gaps are lower-bound constraints,
    the all-maximum-duration forward pass is the monotone worst case. Deadline
    equality is classified as robust.

    When several constraints bind the same start, all predecessor IDs are
    retained in lexical order. A witness chain chooses a transfer predecessor
    before a tied release, then the lexical predecessor ID.
    """

    graph = compile_temporal_graph(itinerary)
    activity_order = graph.activity_order
    activity_by_id = {
        activity.activity_id: activity for activity in itinerary.activities
    }
    incoming_lists: dict[str, list[Transfer]] = {
        activity_id: [] for activity_id in activity_order
    }
    for transfer in itinerary.transfers:
        incoming_lists[transfer.to_activity_id].append(transfer)
    incoming = {
        activity_id: tuple(
            sorted(
                incoming_lists[activity_id],
                key=lambda transfer: transfer.from_activity_id,
            )
        )
        for activity_id in activity_order
    }

    best_timings, best_summary = _analyze_scenario(
        activity_order=activity_order,
        activity_by_id=activity_by_id,
        incoming=incoming,
        use_maximum_duration=False,
    )
    worst_timings, worst_summary = _analyze_scenario(
        activity_order=activity_order,
        activity_by_id=activity_by_id,
        incoming=incoming,
        use_maximum_duration=True,
    )

    activities = tuple(
        ActivityFeasibility(
            activity_id=activity_id,
            release_offset_seconds=activity_by_id[
                activity_id
            ].release_offset_seconds,
            minimum_duration_seconds=activity_by_id[
                activity_id
            ].duration.minimum_seconds,
            maximum_duration_seconds=activity_by_id[
                activity_id
            ].duration.maximum_seconds,
            hard_deadline_offset_seconds=activity_by_id[
                activity_id
            ].hard_deadline_offset_seconds,
            status=(
                FeasibilityStatus.ROBUST
                if worst_timings[activity_id].deadline_slack_seconds >= 0
                else FeasibilityStatus.DURATION_SENSITIVE
            ),
            best_case=best_timings[activity_id],
            worst_case=worst_timings[activity_id],
        )
        for activity_id in activity_order
    )
    status = (
        FeasibilityStatus.ROBUST
        if worst_summary.minimum_deadline_slack_seconds >= 0
        else FeasibilityStatus.DURATION_SENSITIVE
    )
    return IntervalFeasibilityReport(
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        contract_schema_version=graph.contract_schema_version,
        graph_schema_version=graph.graph_schema_version,
        itinerary_digest=graph.itinerary_digest,
        graph_digest=graph_digest(graph),
        anchor_time_utc=graph.anchor_time_utc,
        horizon_seconds=graph.horizon_seconds,
        status=status,
        best_case=best_summary,
        worst_case=worst_summary,
        activities=activities,
    )


def _timing_document(timing: ActivityTiming) -> dict[str, object]:
    return {
        "binding_predecessor_ids": list(timing.binding_predecessor_ids),
        "deadline_slack_seconds": timing.deadline_slack_seconds,
        "earliest_finish_offset_seconds": (
            timing.earliest_finish_offset_seconds
        ),
        "earliest_start_offset_seconds": timing.earliest_start_offset_seconds,
        "release_is_binding": timing.release_is_binding,
        "witness_predecessor_id": timing.witness_predecessor_id,
    }


def _scenario_document(scenario: FeasibilityScenario) -> dict[str, object]:
    return {
        "critical_activity_ids": list(scenario.critical_activity_ids),
        "minimum_deadline_slack_seconds": (
            scenario.minimum_deadline_slack_seconds
        ),
        "witness_activity_id": scenario.witness_activity_id,
        "witness_chain": list(scenario.witness_chain),
    }


def _analysis_document(report: IntervalFeasibilityReport) -> dict[str, object]:
    return {
        "activities": [
            {
                "activity_id": activity.activity_id,
                "best_case": _timing_document(activity.best_case),
                "duration_interval_seconds": {
                    "maximum": activity.maximum_duration_seconds,
                    "minimum": activity.minimum_duration_seconds,
                },
                "hard_deadline_offset_seconds": (
                    activity.hard_deadline_offset_seconds
                ),
                "release_offset_seconds": activity.release_offset_seconds,
                "status": activity.status.value,
                "worst_case": _timing_document(activity.worst_case),
            }
            for activity in report.activities
        ],
        "analysis_schema_version": report.analysis_schema_version,
        "anchor_time_utc": report.anchor_time_utc,
        "best_case": _scenario_document(report.best_case),
        "contract_schema_version": report.contract_schema_version,
        "graph_digest": report.graph_digest,
        "graph_schema_version": report.graph_schema_version,
        "horizon_seconds": report.horizon_seconds,
        "itinerary_digest": report.itinerary_digest,
        "status": report.status.value,
        "worst_case": _scenario_document(report.worst_case),
    }


def canonical_analysis_bytes(report: IntervalFeasibilityReport) -> bytes:
    """Encode an analyzer-produced report as canonical ASCII JSON bytes."""

    return json.dumps(
        _analysis_document(report),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def analysis_digest(report: IntervalFeasibilityReport) -> str:
    """Return the lowercase SHA-256 digest of canonical analysis bytes."""

    return hashlib.sha256(canonical_analysis_bytes(report)).hexdigest()
