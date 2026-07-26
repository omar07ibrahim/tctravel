"""Immutable domain types for itinerary contract version 1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DurationInterval:
    """A closed, deterministic duration interval measured in whole seconds."""

    minimum_seconds: int
    maximum_seconds: int


@dataclass(frozen=True, slots=True)
class Activity:
    """One indivisible activity on the itinerary's relative timeline."""

    activity_id: str
    release_offset_seconds: int
    duration: DurationInterval
    hard_deadline_offset_seconds: int


@dataclass(frozen=True, slots=True)
class Transfer:
    """A finish-to-start dependency with a required minimum handoff gap."""

    from_activity_id: str
    to_activity_id: str
    minimum_seconds: int


@dataclass(frozen=True, slots=True)
class Itinerary:
    """A complete version-1 input, independent of source JSON ordering."""

    schema_version: int
    itinerary_id: str
    anchor_time_utc: str
    horizon_seconds: int
    activities: tuple[Activity, ...]
    transfers: tuple[Transfer, ...]
