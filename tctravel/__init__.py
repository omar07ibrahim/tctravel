"""Deterministic contracts for the original TCTravel reliability core."""

from .codec import (
    MAX_ACTIVITIES,
    MAX_HORIZON_SECONDS,
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    MAX_TRANSFERS,
    canonical_itinerary_bytes,
    decode_itinerary_json,
    itinerary_digest,
)
from .compiler import (
    EdgeKind,
    NodeKind,
    TemporalEdge,
    TemporalGraph,
    TemporalNode,
    canonical_graph_bytes,
    compile_temporal_graph,
    graph_digest,
)
from .errors import ContractError, ErrorCode
from .model import Activity, DurationInterval, Itinerary, Transfer

__all__ = [
    "Activity",
    "ContractError",
    "DurationInterval",
    "EdgeKind",
    "ErrorCode",
    "Itinerary",
    "MAX_ACTIVITIES",
    "MAX_HORIZON_SECONDS",
    "MAX_INPUT_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_TRANSFERS",
    "NodeKind",
    "TemporalEdge",
    "TemporalGraph",
    "TemporalNode",
    "Transfer",
    "canonical_graph_bytes",
    "canonical_itinerary_bytes",
    "compile_temporal_graph",
    "decode_itinerary_json",
    "graph_digest",
    "itinerary_digest",
]
