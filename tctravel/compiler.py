"""Deterministic temporal dependency graph compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .codec import itinerary_digest
from .model import Itinerary
from .validation import SCHEMA_VERSION, validate_itinerary

GRAPH_SCHEMA_VERSION: Final = 1


class NodeKind(StrEnum):
    ANCHOR = "anchor"
    ACTIVITY_START = "activity_start"
    ACTIVITY_FINISH = "activity_finish"


class EdgeKind(StrEnum):
    RELEASE = "release"
    DURATION = "duration"
    HARD_DEADLINE = "hard_deadline"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class TemporalNode:
    node_id: str
    kind: NodeKind
    activity_id: str | None


@dataclass(frozen=True, slots=True)
class TemporalEdge:
    source_node_id: str
    target_node_id: str
    kind: EdgeKind
    lower_bound_seconds: int | None
    upper_bound_seconds: int | None


@dataclass(frozen=True, slots=True)
class TemporalGraph:
    """Result-only graph produced and validated by ``compile_temporal_graph``."""

    graph_schema_version: int
    contract_schema_version: int
    itinerary_digest: str
    anchor_time_utc: str
    horizon_seconds: int
    activity_order: tuple[str, ...]
    nodes: tuple[TemporalNode, ...]
    edges: tuple[TemporalEdge, ...]


def _start_node_id(activity_id: str) -> str:
    return f"activity:{activity_id}:start"


def _finish_node_id(activity_id: str) -> str:
    return f"activity:{activity_id}:finish"


def compile_temporal_graph(itinerary: Itinerary) -> TemporalGraph:
    """Compile a validated itinerary into a byte-deterministic constraint DAG."""

    activity_order = validate_itinerary(itinerary)
    activity_by_id = {
        activity.activity_id: activity for activity in itinerary.activities
    }

    nodes: list[TemporalNode] = [
        TemporalNode(node_id="anchor", kind=NodeKind.ANCHOR, activity_id=None)
    ]
    for activity_id in activity_order:
        nodes.extend(
            (
                TemporalNode(
                    node_id=_start_node_id(activity_id),
                    kind=NodeKind.ACTIVITY_START,
                    activity_id=activity_id,
                ),
                TemporalNode(
                    node_id=_finish_node_id(activity_id),
                    kind=NodeKind.ACTIVITY_FINISH,
                    activity_id=activity_id,
                ),
            )
        )

    edges: list[TemporalEdge] = []
    for activity_id in activity_order:
        activity = activity_by_id[activity_id]
        edges.extend(
            (
                TemporalEdge(
                    source_node_id="anchor",
                    target_node_id=_start_node_id(activity_id),
                    kind=EdgeKind.RELEASE,
                    lower_bound_seconds=activity.release_offset_seconds,
                    upper_bound_seconds=None,
                ),
                TemporalEdge(
                    source_node_id=_start_node_id(activity_id),
                    target_node_id=_finish_node_id(activity_id),
                    kind=EdgeKind.DURATION,
                    lower_bound_seconds=activity.duration.minimum_seconds,
                    upper_bound_seconds=activity.duration.maximum_seconds,
                ),
                TemporalEdge(
                    source_node_id="anchor",
                    target_node_id=_finish_node_id(activity_id),
                    kind=EdgeKind.HARD_DEADLINE,
                    lower_bound_seconds=None,
                    upper_bound_seconds=activity.hard_deadline_offset_seconds,
                ),
            )
        )
    for transfer in itinerary.transfers:
        edges.append(
            TemporalEdge(
                source_node_id=_finish_node_id(transfer.from_activity_id),
                target_node_id=_start_node_id(transfer.to_activity_id),
                kind=EdgeKind.TRANSFER,
                lower_bound_seconds=transfer.minimum_seconds,
                upper_bound_seconds=None,
            )
        )

    node_position = {node.node_id: index for index, node in enumerate(nodes)}
    edges.sort(
        key=lambda edge: (
            node_position[edge.source_node_id],
            node_position[edge.target_node_id],
            edge.kind.value,
            -1
            if edge.lower_bound_seconds is None
            else edge.lower_bound_seconds,
            -1
            if edge.upper_bound_seconds is None
            else edge.upper_bound_seconds,
        )
    )

    return TemporalGraph(
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        contract_schema_version=SCHEMA_VERSION,
        itinerary_digest=itinerary_digest(itinerary),
        anchor_time_utc=itinerary.anchor_time_utc,
        horizon_seconds=itinerary.horizon_seconds,
        activity_order=activity_order,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _graph_document(graph: TemporalGraph) -> dict[str, object]:
    return {
        "activity_order": list(graph.activity_order),
        "anchor_time_utc": graph.anchor_time_utc,
        "contract_schema_version": graph.contract_schema_version,
        "edges": [
            {
                "kind": edge.kind.value,
                "lower_bound_seconds": edge.lower_bound_seconds,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "upper_bound_seconds": edge.upper_bound_seconds,
            }
            for edge in graph.edges
        ],
        "graph_schema_version": graph.graph_schema_version,
        "horizon_seconds": graph.horizon_seconds,
        "itinerary_digest": graph.itinerary_digest,
        "nodes": [
            {
                "activity_id": node.activity_id,
                "kind": node.kind.value,
                "node_id": node.node_id,
            }
            for node in graph.nodes
        ],
    }


def canonical_graph_bytes(graph: TemporalGraph) -> bytes:
    """Encode a compiler-produced graph into canonical ASCII JSON bytes."""

    return json.dumps(
        _graph_document(graph),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def graph_digest(graph: TemporalGraph) -> str:
    """Return the lowercase SHA-256 digest of canonical graph bytes."""

    return hashlib.sha256(canonical_graph_bytes(graph)).hexdigest()
