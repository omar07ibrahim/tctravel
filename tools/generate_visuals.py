#!/usr/bin/env python3
"""Generate source-bound visual evidence for the TCTravel contract core.

The generator is deliberately standard-library only. It loads the repository's
synthetic fixture, exercises the public ``tctravel`` API, and emits deterministic
SVG plus a manifest. It never reads the quarantined legacy snapshot.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Mapping, Sequence, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tctravel import (
    MAX_ACTIVITIES,
    MAX_HORIZON_SECONDS,
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    MAX_TRANSFERS,
    ContractError,
    EdgeKind,
    ErrorCode,
    TemporalEdge,
    TemporalGraph,
    compile_temporal_graph,
    decode_itinerary_json,
    graph_digest,
    itinerary_digest,
)


GRAPH_OUTPUT: Final = "docs/visuals/generated/temporal-dag.svg"
BOUNDARY_OUTPUT: Final = (
    "docs/visuals/generated/contract-boundary-matrix.svg"
)
MANIFEST_OUTPUT: Final = "docs/visuals/manifest.json"
FIXTURE_PATH: Final = "examples/synthetic_connection.v1.json"
CHECK_COMMAND: Final = "python3 tools/generate_visuals.py --check"

SOURCE_PATHS: Final = (
    "examples/synthetic_connection.v1.json",
    "tctravel/__init__.py",
    "tctravel/codec.py",
    "tctravel/compiler.py",
    "tctravel/errors.py",
    "tctravel/model.py",
    "tctravel/validation.py",
    "tools/generate_visuals.py",
)

GRAPH_VIEWBOX: Final = "0 0 1800 1240"
BOUNDARY_VIEWBOX: Final = "0 0 1800 1200"

COLORS: Final = {
    EdgeKind.RELEASE.value: "#0369a1",
    EdgeKind.DURATION.value: "#15803d",
    EdgeKind.HARD_DEADLINE.value: "#b91c1c",
    EdgeKind.TRANSFER.value: "#6d28d9",
}


class GenerationFailure(RuntimeError):
    """Raised when observed API behavior cannot support a visual claim."""


@dataclass(frozen=True, slots=True)
class BoundaryCase:
    case_id: str
    label: str
    probe: str
    payload: bytes
    expected_code: ErrorCode | None


@dataclass(frozen=True, slots=True)
class BoundaryObservation:
    case_id: str
    label: str
    probe: str
    status: str
    code: str | None
    itinerary_sha256: str | None
    graph_sha256: str | None
    detail: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _json_bytes(document: object, *, sort_keys: bool = True) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    ).encode("ascii")


def _reverse_object_key_order(value: object) -> object:
    """Recursively reverse JSON object keys while preserving array order."""

    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        return {
            key: _reverse_object_key_order(mapping[key])
            for key in reversed(tuple(mapping))
        }
    if type(value) is list:
        items = cast(list[object], value)
        return [_reverse_object_key_order(item) for item in items]
    return value


def _load_fixture_document(fixture_payload: bytes) -> dict[str, object]:
    document = json.loads(fixture_payload)
    if type(document) is not dict:
        raise GenerationFailure("synthetic_fixture_not_object")
    return document


def _boundary_cases(fixture_payload: bytes) -> tuple[BoundaryCase, ...]:
    baseline = _load_fixture_document(fixture_payload)

    reordered = copy.deepcopy(baseline)
    reordered["activities"] = list(reversed(reordered["activities"]))
    reordered["transfers"] = list(reversed(reordered["transfers"]))
    reordered = cast(
        dict[str, object],
        _reverse_object_key_order(reordered),
    )

    unknown_field = copy.deepcopy(baseline)
    unknown_field["unreviewed_extension"] = "synthetic"

    boolean_integer = copy.deepcopy(baseline)
    boolean_integer["horizon_seconds"] = True

    cyclic = copy.deepcopy(baseline)
    cyclic["transfers"].append(
        {
            "from_activity_id": "gate_walk",
            "minimum_seconds": 0,
            "to_activity_id": "local_shuttle",
        }
    )

    infeasible = copy.deepcopy(baseline)
    infeasible["activities"][0]["hard_deadline_offset_seconds"] = 599

    duplicate_marker = b'"schema_version": 1,'
    if fixture_payload.count(duplicate_marker) != 1:
        raise GenerationFailure("synthetic_fixture_schema_marker_drift")
    duplicate_key = fixture_payload.replace(
        duplicate_marker,
        duplicate_marker + b'\n  "schema_version": 1,',
        1,
    )

    too_deep = (
        b"[" * (MAX_JSON_DEPTH + 1)
        + b"0"
        + b"]" * (MAX_JSON_DEPTH + 1)
    )
    too_large = b" " * (MAX_INPUT_BYTES + 1)

    return (
        BoundaryCase(
            case_id="accepted_baseline",
            label="Committed synthetic fixture",
            probe="Exact version-1 object",
            payload=fixture_payload,
            expected_code=None,
        ),
        BoundaryCase(
            case_id="accepted_reordered",
            label="Equivalent reordered input",
            probe="Objects, activities, transfers reordered",
            payload=_json_bytes(reordered, sort_keys=False),
            expected_code=None,
        ),
        BoundaryCase(
            case_id="unknown_field",
            label="Unknown root field",
            probe="One undeclared key",
            payload=_json_bytes(unknown_field),
            expected_code=ErrorCode.UNKNOWN_FIELD,
        ),
        BoundaryCase(
            case_id="duplicate_json_key",
            label="Duplicate JSON key",
            probe="schema_version appears twice",
            payload=duplicate_key,
            expected_code=ErrorCode.DUPLICATE_JSON_KEY,
        ),
        BoundaryCase(
            case_id="boolean_integer",
            label="Boolean in integer field",
            probe="horizon_seconds = true",
            payload=_json_bytes(boolean_integer),
            expected_code=ErrorCode.INVALID_TYPE,
        ),
        BoundaryCase(
            case_id="dependency_cycle",
            label="Cyclic transfers",
            probe="gate_walk loops to local_shuttle",
            payload=_json_bytes(cyclic),
            expected_code=ErrorCode.DEPENDENCY_CYCLE,
        ),
        BoundaryCase(
            case_id="infeasible_deadline",
            label="Impossible minimum finish",
            probe="600 s minimum; 599 s deadline",
            payload=_json_bytes(infeasible),
            expected_code=ErrorCode.INFEASIBLE_DEADLINE,
        ),
        BoundaryCase(
            case_id="input_too_deep",
            label="Nesting beyond public limit",
            probe=f"{MAX_JSON_DEPTH + 1} nested containers",
            payload=too_deep,
            expected_code=ErrorCode.INPUT_TOO_DEEP,
        ),
        BoundaryCase(
            case_id="input_too_large",
            label="Payload beyond public limit",
            probe=f"{MAX_INPUT_BYTES + 1:,} bytes",
            payload=too_large,
            expected_code=ErrorCode.INPUT_TOO_LARGE,
        ),
    )


def collect_boundary_observations(
    fixture_payload: bytes,
) -> tuple[BoundaryObservation, ...]:
    """Execute representative contract probes through the public API."""

    baseline = decode_itinerary_json(fixture_payload)
    baseline_graph = compile_temporal_graph(baseline)
    baseline_itinerary_sha256 = itinerary_digest(baseline)
    baseline_graph_sha256 = graph_digest(baseline_graph)
    observations: list[BoundaryObservation] = []

    for case in _boundary_cases(fixture_payload):
        try:
            itinerary = decode_itinerary_json(case.payload)
            graph = compile_temporal_graph(itinerary)
        except ContractError as error:
            if case.expected_code is None or error.code is not case.expected_code:
                raise GenerationFailure(
                    f"unexpected_contract_result:{case.case_id}"
                ) from None
            if error.code not in ErrorCode:
                raise GenerationFailure(
                    f"non_public_error_code:{case.case_id}"
                )
            observations.append(
                BoundaryObservation(
                    case_id=case.case_id,
                    label=case.label,
                    probe=case.probe,
                    status="rejected",
                    code=error.code.value,
                    itinerary_sha256=None,
                    graph_sha256=None,
                    detail=f"ContractError.code = {error.code.value}",
                )
            )
            continue

        if case.expected_code is not None:
            raise GenerationFailure(
                f"unexpected_contract_acceptance:{case.case_id}"
            )

        observed_itinerary_sha256 = itinerary_digest(itinerary)
        observed_graph_sha256 = graph_digest(graph)
        if case.case_id == "accepted_reordered":
            digests_match = (
                observed_itinerary_sha256 == baseline_itinerary_sha256
                and observed_graph_sha256 == baseline_graph_sha256
            )
            if not digests_match:
                raise GenerationFailure("equivalent_input_digest_drift")
            detail = "Canonical itinerary + graph digests match baseline"
        else:
            detail = (
                f"{len(itinerary.activities)} activities; "
                f"{len(itinerary.transfers)} transfers"
            )
        observations.append(
            BoundaryObservation(
                case_id=case.case_id,
                label=case.label,
                probe=case.probe,
                status="accepted",
                code=None,
                itinerary_sha256=observed_itinerary_sha256,
                graph_sha256=observed_graph_sha256,
                detail=detail,
            )
        )

    return tuple(observations)


def _bound_text(edge: TemporalEdge) -> str:
    lower = edge.lower_bound_seconds
    upper = edge.upper_bound_seconds
    if lower is not None and upper is not None:
        return f"lower {lower:,} s / upper {upper:,} s"
    if lower is not None:
        return f"lower >= {lower:,} s"
    if upper is not None:
        return f"upper <= {upper:,} s"
    raise GenerationFailure("unbounded_edge")


def _compact_bound_text(edge: TemporalEdge) -> str:
    lower = edge.lower_bound_seconds
    upper = edge.upper_bound_seconds
    if lower is not None and upper is not None:
        return f"{lower:,}-{upper:,} s"
    if lower is not None:
        return f">= {lower:,} s"
    if upper is not None:
        return f"<= {upper:,} s"
    raise GenerationFailure("unbounded_edge")


def _edge_by_kind(
    graph: TemporalGraph, kind: EdgeKind
) -> tuple[TemporalEdge, ...]:
    return tuple(edge for edge in graph.edges if edge.kind is kind)


def _graph_metadata(graph: TemporalGraph) -> str:
    metadata = {
        "artifact": "compiled_temporal_dag",
        "contract_schema_version": graph.contract_schema_version,
        "edge_count": len(graph.edges),
        "graph_schema_version": graph.graph_schema_version,
        "graph_sha256": graph_digest(graph),
        "itinerary_sha256": graph.itinerary_digest,
        "node_count": len(graph.nodes),
    }
    return _escape(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    )


def _marker_definitions() -> str:
    lines: list[str] = ["  <defs>"]
    for kind, color in COLORS.items():
        lines.extend(
            (
                (
                    f'    <marker id="arrow-{_escape(kind)}" '
                    'viewBox="0 0 10 10" refX="9" refY="5" '
                    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                ),
                (
                    f'      <path d="M 0 0 L 10 5 L 0 10 z" '
                    f'fill="{color}"/>'
                ),
                "    </marker>",
            )
        )
    lines.append("  </defs>")
    return "\n".join(lines)


def _graph_svg(graph: TemporalGraph) -> bytes:
    itinerary_sha256 = graph.itinerary_digest
    graph_sha256 = graph_digest(graph)
    kind_counts = Counter(edge.kind.value for edge in graph.edges)
    expected_kinds = {kind.value for kind in EdgeKind}
    if set(kind_counts) != expected_kinds:
        raise GenerationFailure("edge_kind_inventory_drift")

    positions: dict[str, tuple[float, float]] = {"anchor": (150.0, 490.0)}
    x = 390.0
    for activity_id in graph.activity_order:
        positions[f"activity:{activity_id}:start"] = (x, 490.0)
        positions[f"activity:{activity_id}:finish"] = (x + 210.0, 490.0)
        x += 460.0

    if len(positions) != len(graph.nodes):
        raise GenerationFailure("graph_layout_node_count_drift")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{GRAPH_VIEWBOX}" width="1800" height="1240" '
            'role="img" aria-labelledby="dag-title dag-desc" '
            f'data-itinerary-sha256="{itinerary_sha256}" '
            f'data-graph-sha256="{graph_sha256}">'
        ),
        "  <title id=\"dag-title\">Compiled temporal dependency DAG</title>",
        (
            "  <desc id=\"dag-desc\">The exact seven-node, eleven-edge graph "
            "compiled from the committed synthetic itinerary. Colored arrows "
            "show release, bounded duration, hard deadline, and transfer "
            "constraints. Full canonical SHA-256 digests are visible.</desc>"
        ),
        f"  <metadata>{_graph_metadata(graph)}</metadata>",
        _marker_definitions(),
        '  <rect width="1800" height="1240" fill="#f8fafc"/>',
        (
            '  <rect x="32" y="28" width="1736" height="156" rx="26" '
            'fill="#0f172a"/>'
        ),
        (
            '  <text x="76" y="83" fill="#f8fafc" font-size="34" '
            'font-weight="700" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "Compiled temporal dependency DAG</text>"
        ),
        (
            '  <text x="76" y="122" fill="#cbd5e1" font-size="19" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            f"contract v{graph.contract_schema_version} → graph "
            f"v{graph.graph_schema_version} • {len(graph.nodes)} nodes • "
            f"{len(graph.edges)} constraints • synthetic fixture</text>"
        ),
        (
            '  <text x="800" y="72" fill="#94a3b8" font-size="14" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            "canonical itinerary SHA-256</text>"
        ),
        (
            '  <text x="800" y="98" fill="#f8fafc" font-size="15" '
            'font-family="SFMono-Regular, Consolas, Liberation Mono, monospace">'
            f"{itinerary_sha256}</text>"
        ),
        (
            '  <text x="800" y="130" fill="#94a3b8" font-size="14" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            "canonical graph SHA-256</text>"
        ),
        (
            '  <text x="800" y="156" fill="#f8fafc" font-size="15" '
            'font-family="SFMono-Regular, Consolas, Liberation Mono, monospace">'
            f"{graph_sha256}</text>"
        ),
        (
            '  <text x="54" y="230" fill="#334155" font-size="17" '
            'font-weight="700" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "Compiled structure</text>"
        ),
    ]

    legend_x = 300
    legend_labels = {
        EdgeKind.RELEASE.value: "release lower bound",
        EdgeKind.DURATION.value: "duration interval",
        EdgeKind.HARD_DEADLINE.value: "hard deadline upper bound",
        EdgeKind.TRANSFER.value: "minimum transfer gap",
    }
    for kind in (
        EdgeKind.RELEASE.value,
        EdgeKind.DURATION.value,
        EdgeKind.HARD_DEADLINE.value,
        EdgeKind.TRANSFER.value,
    ):
        color = COLORS[kind]
        count = kind_counts[kind]
        lines.extend(
            (
                (
                    f'  <line x1="{legend_x}" y1="224" x2="{legend_x + 42}" '
                    f'y2="224" stroke="{color}" stroke-width="5" '
                    f'marker-end="url(#arrow-{kind})"/>'
                ),
                (
                    f'  <text x="{legend_x + 55}" y="230" fill="#475569" '
                    'font-size="15" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{_escape(legend_labels[kind])} ({count})</text>"
                ),
            )
        )
        legend_x += 360

    lines.append(
        '  <rect x="42" y="256" width="1716" height="470" rx="24" '
        'fill="#ffffff" stroke="#dbe3ee" stroke-width="2"/>'
    )

    releases = _edge_by_kind(graph, EdgeKind.RELEASE)
    deadlines = _edge_by_kind(graph, EdgeKind.HARD_DEADLINE)
    durations = _edge_by_kind(graph, EdgeKind.DURATION)
    transfers = _edge_by_kind(graph, EdgeKind.TRANSFER)

    for index, edge in enumerate(releases):
        source_x, source_y = positions[edge.source_node_id]
        target_x, target_y = positions[edge.target_node_id]
        source_x += 78
        target_x -= 80
        control_x = (source_x + target_x) / 2
        control_y = 320 - index * 52
        label_y = (source_y + control_y) / 2 - 13
        lines.extend(
            (
                (
                    f'  <path d="M {source_x:.0f} {source_y:.0f} '
                    f'Q {control_x:.0f} {control_y:.0f} '
                    f'{target_x:.0f} {target_y:.0f}" fill="none" '
                    f'stroke="{COLORS[EdgeKind.RELEASE.value]}" '
                    'stroke-width="4" '
                    f'marker-end="url(#arrow-{EdgeKind.RELEASE.value})"/>'
                ),
                (
                    f'  <text x="{control_x:.0f}" y="{label_y:.0f}" '
                    'text-anchor="middle" fill="#0369a1" font-size="14" '
                    'font-weight="700" '
                    'style="paint-order:stroke;stroke:#ffffff;stroke-width:7px;'
                    'stroke-linejoin:round" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"release {_escape(_compact_bound_text(edge))}</text>"
                ),
            )
        )

    for index, edge in enumerate(deadlines):
        source_x, source_y = positions[edge.source_node_id]
        target_x, target_y = positions[edge.target_node_id]
        source_x += 78
        target_x -= 80
        control_x = (source_x + target_x) / 2
        control_y = 650 + index * 40
        label_y = (source_y + control_y) / 2 + 28
        lines.extend(
            (
                (
                    f'  <path d="M {source_x:.0f} {source_y:.0f} '
                    f'Q {control_x:.0f} {control_y:.0f} '
                    f'{target_x:.0f} {target_y:.0f}" fill="none" '
                    f'stroke="{COLORS[EdgeKind.HARD_DEADLINE.value]}" '
                    'stroke-width="4" stroke-dasharray="10 7" '
                    f'marker-end="url(#arrow-{EdgeKind.HARD_DEADLINE.value})"/>'
                ),
                (
                    f'  <text x="{control_x:.0f}" y="{label_y:.0f}" '
                    'text-anchor="middle" fill="#b91c1c" font-size="14" '
                    'font-weight="700" '
                    'style="paint-order:stroke;stroke:#ffffff;stroke-width:7px;'
                    'stroke-linejoin:round" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"deadline {_escape(_compact_bound_text(edge))}</text>"
                ),
            )
        )

    for edge in durations:
        source_x, source_y = positions[edge.source_node_id]
        target_x, target_y = positions[edge.target_node_id]
        source_x += 80
        target_x -= 80
        midpoint = (source_x + target_x) / 2
        lines.extend(
            (
                (
                    f'  <line x1="{source_x:.0f}" y1="{source_y:.0f}" '
                    f'x2="{target_x:.0f}" y2="{target_y:.0f}" '
                    f'stroke="{COLORS[EdgeKind.DURATION.value]}" '
                    'stroke-width="5" '
                    f'marker-end="url(#arrow-{EdgeKind.DURATION.value})"/>'
                ),
                (
                    f'  <text x="{midpoint:.0f}" y="{source_y - 70:.0f}" '
                    'text-anchor="middle" fill="#15803d" font-size="14" '
                    'font-weight="700" '
                    'style="paint-order:stroke;stroke:#ffffff;stroke-width:7px;'
                    'stroke-linejoin:round" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"duration {_escape(_compact_bound_text(edge))}</text>"
                ),
            )
        )

    for edge in transfers:
        source_x, source_y = positions[edge.source_node_id]
        target_x, target_y = positions[edge.target_node_id]
        source_x += 80
        target_x -= 80
        midpoint = (source_x + target_x) / 2
        lines.extend(
            (
                (
                    f'  <line x1="{source_x:.0f}" y1="{source_y:.0f}" '
                    f'x2="{target_x:.0f}" y2="{target_y:.0f}" '
                    f'stroke="{COLORS[EdgeKind.TRANSFER.value]}" '
                    'stroke-width="5" stroke-dasharray="4 5" '
                    f'marker-end="url(#arrow-{EdgeKind.TRANSFER.value})"/>'
                ),
                (
                    f'  <text x="{midpoint:.0f}" y="{source_y + 82:.0f}" '
                    'text-anchor="middle" fill="#6d28d9" font-size="14" '
                    'font-weight="700" '
                    'style="paint-order:stroke;stroke:#ffffff;stroke-width:7px;'
                    'stroke-linejoin:round" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"transfer {_escape(_compact_bound_text(edge))}</text>"
                ),
            )
        )

    for node in graph.nodes:
        center_x, center_y = positions[node.node_id]
        if node.node_id == "anchor":
            lines.extend(
                (
                    (
                        f'  <circle cx="{center_x:.0f}" cy="{center_y:.0f}" '
                        'r="78" fill="#1e293b" stroke="#0f172a" '
                        'stroke-width="3"/>'
                    ),
                    (
                        f'  <text x="{center_x:.0f}" y="{center_y - 7:.0f}" '
                        'text-anchor="middle" fill="#f8fafc" font-size="19" '
                        'font-weight="700" '
                        'font-family="Inter, Segoe UI, Arial, sans-serif">'
                        "ANCHOR</text>"
                    ),
                    (
                        f'  <text x="{center_x:.0f}" y="{center_y + 23:.0f}" '
                        'text-anchor="middle" fill="#cbd5e1" font-size="13" '
                        'font-family="Inter, Segoe UI, Arial, sans-serif">'
                        "t = 0 s</text>"
                    ),
                )
            )
            continue

        activity_id = node.activity_id
        if activity_id is None:
            raise GenerationFailure("activity_node_without_activity")
        node_phase = (
            "START" if node.node_id.endswith(":start") else "FINISH"
        )
        node_fill = "#e0f2fe" if node_phase == "START" else "#dcfce7"
        node_stroke = (
            COLORS[EdgeKind.RELEASE.value]
            if node_phase == "START"
            else COLORS[EdgeKind.DURATION.value]
        )
        lines.extend(
            (
                (
                    f'  <rect x="{center_x - 80:.0f}" '
                    f'y="{center_y - 58:.0f}" width="160" height="116" '
                    f'rx="18" fill="{node_fill}" stroke="{node_stroke}" '
                    'stroke-width="3"/>'
                ),
                (
                    f'  <text x="{center_x:.0f}" y="{center_y - 12:.0f}" '
                    'text-anchor="middle" fill="#0f172a" font-size="14" '
                    'font-weight="700" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    'monospace">'
                    f"{_escape(activity_id)}</text>"
                ),
                (
                    f'  <text x="{center_x:.0f}" y="{center_y + 22:.0f}" '
                    f'text-anchor="middle" fill="{node_stroke}" font-size="16" '
                    'font-weight="800" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{node_phase}</text>"
                ),
            )
        )

    lines.extend(
        (
            (
                '  <text x="58" y="774" fill="#334155" font-size="18" '
                'font-weight="700" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "Exact compiled constraint ledger</text>"
            ),
            (
                '  <text x="430" y="774" fill="#64748b" font-size="14" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "Every card below is emitted from one TemporalEdge.</text>"
            ),
        )
    )

    card_width = 410
    card_height = 116
    for index, edge in enumerate(graph.edges):
        column = index % 4
        row = index // 4
        card_x = 42 + column * 432
        card_y = 794 + row * 126
        color = COLORS[edge.kind.value]
        lower_attribute = (
            ""
            if edge.lower_bound_seconds is None
            else str(edge.lower_bound_seconds)
        )
        upper_attribute = (
            ""
            if edge.upper_bound_seconds is None
            else str(edge.upper_bound_seconds)
        )
        lines.extend(
            (
                (
                    f'  <g data-edge-kind="{_escape(edge.kind.value)}" '
                    f'data-source-node-id="{_escape(edge.source_node_id)}" '
                    f'data-target-node-id="{_escape(edge.target_node_id)}" '
                    f'data-lower-bound-seconds="{lower_attribute}" '
                    f'data-upper-bound-seconds="{upper_attribute}">'
                ),
                (
                    f'    <rect x="{card_x}" y="{card_y}" width="{card_width}" '
                    f'height="{card_height}" rx="16" fill="#ffffff" '
                    'stroke="#dbe3ee" stroke-width="2"/>'
                ),
                (
                    f'    <rect x="{card_x}" y="{card_y}" width="8" '
                    f'height="{card_height}" rx="4" fill="{color}"/>'
                ),
                (
                    f'    <text x="{card_x + 24}" y="{card_y + 27}" '
                    f'fill="{color}" font-size="14" font-weight="800" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{_escape(edge.kind.value.upper())} • "
                    f"{_escape(_bound_text(edge))}</text>"
                ),
                (
                    f'    <text x="{card_x + 24}" y="{card_y + 56}" '
                    'fill="#475569" font-size="12" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    'monospace">'
                    f"from {_escape(edge.source_node_id)}</text>"
                ),
                (
                    f'    <text x="{card_x + 24}" y="{card_y + 82}" '
                    'fill="#475569" font-size="12" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    'monospace">'
                    f"to   {_escape(edge.target_node_id)}</text>"
                ),
                "  </g>",
            )
        )

    lines.extend(
        (
            (
                '  <rect x="42" y="1178" width="1716" height="38" rx="14" '
                'fill="#e2e8f0"/>'
            ),
            (
                '  <text x="900" y="1203" text-anchor="middle" fill="#334155" '
                'font-size="15" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "Constraint compilation only — no route choice, live travel "
                "data, reliability estimate, or prediction.</text>"
            ),
            "</svg>",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def _observation_record(
    observation: BoundaryObservation,
) -> dict[str, object]:
    return {
        "case_id": observation.case_id,
        "code": observation.code,
        "detail": observation.detail,
        "graph_sha256": observation.graph_sha256,
        "itinerary_sha256": observation.itinerary_sha256,
        "label": observation.label,
        "probe": observation.probe,
        "status": observation.status,
    }


def _boundary_metadata(
    observations: Sequence[BoundaryObservation],
) -> str:
    metadata = {
        "artifact": "contract_boundary_matrix",
        "case_count": len(observations),
        "observed_error_codes": sorted(
            {
                observation.code
                for observation in observations
                if observation.code is not None
            }
        ),
        "public_error_code_count": len(ErrorCode),
    }
    return _escape(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    )


def _boundary_svg(
    observations: Sequence[BoundaryObservation],
) -> bytes:
    if not observations:
        raise GenerationFailure("empty_boundary_observations")
    accepted_count = sum(
        observation.status == "accepted" for observation in observations
    )
    rejected_count = sum(
        observation.status == "rejected" for observation in observations
    )
    observed_codes = {
        observation.code
        for observation in observations
        if observation.code is not None
    }

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{BOUNDARY_VIEWBOX}" width="1800" height="1200" '
            'role="img" aria-labelledby="boundary-title boundary-desc" '
            f'data-case-count="{len(observations)}" '
            f'data-public-error-code-count="{len(ErrorCode)}">'
        ),
        (
            "  <title id=\"boundary-title\">Executed contract boundary "
            "matrix</title>"
        ),
        (
            "  <desc id=\"boundary-desc\">Nine synthetic probes executed "
            "against the public itinerary decoder and temporal compiler. "
            "Accepted rows show observed canonical evidence; rejected rows "
            "show the exact stable public error code.</desc>"
        ),
        f"  <metadata>{_boundary_metadata(observations)}</metadata>",
        '  <rect width="1800" height="1200" fill="#f8fafc"/>',
        (
            '  <rect x="32" y="28" width="1736" height="156" rx="26" '
            'fill="#0f172a"/>'
        ),
        (
            '  <text x="76" y="84" fill="#f8fafc" font-size="34" '
            'font-weight="700" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "Executed contract-boundary matrix</text>"
        ),
        (
            '  <text x="76" y="125" fill="#cbd5e1" font-size="19" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            "Observed through the public decoder + compiler • synthetic inputs "
            "only • fail-closed codes are redacted</text>"
        ),
        (
            '  <rect x="1320" y="61" width="180" height="80" rx="18" '
            'fill="#14532d"/>'
        ),
        (
            '  <text x="1410" y="94" text-anchor="middle" fill="#bbf7d0" '
            'font-size="14" font-weight="700" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">ACCEPTED</text>'
        ),
        (
            '  <text x="1410" y="124" text-anchor="middle" fill="#ffffff" '
            'font-size="25" font-weight="800" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            f"{accepted_count}</text>"
        ),
        (
            '  <rect x="1520" y="61" width="180" height="80" rx="18" '
            'fill="#7f1d1d"/>'
        ),
        (
            '  <text x="1610" y="94" text-anchor="middle" fill="#fecaca" '
            'font-size="14" font-weight="700" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">REJECTED</text>'
        ),
        (
            '  <text x="1610" y="124" text-anchor="middle" fill="#ffffff" '
            'font-size="25" font-weight="800" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">'
            f"{rejected_count}</text>"
        ),
        (
            '  <text x="48" y="224" fill="#334155" font-size="17" '
            'font-weight="700" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "Public resource bounds read at generation time</text>"
        ),
    ]

    limits = (
        ("MAX_INPUT_BYTES", f"{MAX_INPUT_BYTES:,}", "UTF-8 bytes"),
        ("MAX_JSON_DEPTH", f"{MAX_JSON_DEPTH:,}", "containers"),
        ("MAX_ACTIVITIES", f"{MAX_ACTIVITIES:,}", "activities"),
        ("MAX_TRANSFERS", f"{MAX_TRANSFERS:,}", "transfers"),
        (
            "MAX_HORIZON_SECONDS",
            f"{MAX_HORIZON_SECONDS:,}",
            f"seconds ({MAX_HORIZON_SECONDS // 86_400} days)",
        ),
    )
    card_width = 328
    for index, (name, value, unit) in enumerate(limits):
        x = 48 + index * 344
        lines.extend(
            (
                (
                    f'  <g data-public-constant="{name}" '
                    f'data-value="{value.replace(",", "")}">'
                ),
                (
                    f'    <rect x="{x}" y="245" width="{card_width}" '
                    'height="106" rx="18" fill="#ffffff" stroke="#cbd5e1" '
                    'stroke-width="2"/>'
                ),
                (
                    f'    <text x="{x + 20}" y="276" fill="#64748b" '
                    'font-size="13" font-weight="700" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    f'monospace">{name}</text>'
                ),
                (
                    f'    <text x="{x + 20}" y="313" fill="#0f172a" '
                    'font-size="25" font-weight="800" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{value}</text>"
                ),
                (
                    f'    <text x="{x + 20}" y="336" fill="#64748b" '
                    'font-size="13" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{unit}</text>"
                ),
                "  </g>",
            )
        )

    lines.extend(
        (
            (
                '  <rect x="48" y="382" width="1704" height="58" rx="14" '
                'fill="#e2e8f0"/>'
            ),
            (
                '  <text x="72" y="417" fill="#334155" font-size="14" '
                'font-weight="800" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">CASE</text>'
            ),
            (
                '  <text x="430" y="417" fill="#334155" font-size="14" '
                'font-weight="800" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">PROBE</text>'
            ),
            (
                '  <text x="1000" y="417" fill="#334155" font-size="14" '
                'font-weight="800" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "OBSERVED RESULT</text>"
            ),
            (
                '  <text x="1380" y="417" fill="#334155" font-size="14" '
                'font-weight="800" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "PUBLIC EVIDENCE</text>"
            ),
        )
    )

    for index, observation in enumerate(observations):
        y = 448 + index * 66
        fill = "#ffffff" if index % 2 == 0 else "#f1f5f9"
        status_fill = (
            "#166534" if observation.status == "accepted" else "#991b1b"
        )
        status_text = observation.status.upper()
        evidence_primary: str
        evidence_secondary: str
        if observation.status == "accepted":
            if (
                observation.itinerary_sha256 is None
                or observation.graph_sha256 is None
            ):
                raise GenerationFailure("accepted_observation_without_digest")
            evidence_primary = (
                "itinerary "
                f"{observation.itinerary_sha256[:20]}…"
            )
            evidence_secondary = (
                "graph     "
                f"{observation.graph_sha256[:20]}…"
            )
            visual_detail = (
                "canonical digests match: yes"
                if observation.case_id == "accepted_reordered"
                else observation.detail
            )
        else:
            if observation.code is None:
                raise GenerationFailure("rejected_observation_without_code")
            evidence_primary = f"ContractError.code"
            evidence_secondary = observation.code
            visual_detail = "fail-closed ContractError"

        lines.extend(
            (
                (
                    f'  <g data-case-id="{_escape(observation.case_id)}" '
                    f'data-status="{_escape(observation.status)}" '
                    f'data-code="{_escape(observation.code or "")}" '
                    f'data-itinerary-sha256="'
                    f'{_escape(observation.itinerary_sha256 or "")}" '
                    f'data-graph-sha256="'
                    f'{_escape(observation.graph_sha256 or "")}">'
                ),
                (
                    f'    <rect x="48" y="{y}" width="1704" height="62" '
                    f'rx="10" fill="{fill}"/>'
                ),
                (
                    f'    <text x="72" y="{y + 25}" fill="#0f172a" '
                    'font-size="15" font-weight="700" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{_escape(observation.label)}</text>"
                ),
                (
                    f'    <text x="72" y="{y + 47}" fill="#64748b" '
                    'font-size="12" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    f'monospace">{_escape(observation.case_id)}</text>'
                ),
                (
                    f'    <text x="430" y="{y + 36}" fill="#334155" '
                    'font-size="14" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{_escape(observation.probe)}</text>"
                ),
                (
                    f'    <rect x="1000" y="{y + 14}" width="126" height="34" '
                    f'rx="17" fill="{status_fill}"/>'
                ),
                (
                    f'    <text x="1063" y="{y + 36}" text-anchor="middle" '
                    'fill="#ffffff" font-size="12" font-weight="800" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{status_text}</text>"
                ),
                (
                    f'    <text x="1145" y="{y + 36}" fill="#475569" '
                    'font-size="12" '
                    'font-family="Inter, Segoe UI, Arial, sans-serif">'
                    f"{_escape(visual_detail)}</text>"
                ),
                (
                    f'    <text x="1380" y="{y + 25}" fill="#475569" '
                    'font-size="12" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    f'monospace">{_escape(evidence_primary)}</text>'
                ),
                (
                    f'    <text x="1380" y="{y + 47}" fill="#0f172a" '
                    'font-size="12" font-weight="700" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    f'monospace">{_escape(evidence_secondary)}</text>'
                ),
                "  </g>",
            )
        )

    footer_y = 448 + len(observations) * 66 + 22
    lines.extend(
        (
            (
                f'  <rect x="48" y="{footer_y}" width="1704" height="95" '
                'rx="18" fill="#e0f2fe" stroke="#7dd3fc" stroke-width="2"/>'
            ),
            (
                f'  <text x="74" y="{footer_y + 32}" fill="#075985" '
                'font-size="15" font-weight="800" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                f"Observed {len(observed_codes)} of {len(ErrorCode)} public "
                "ErrorCode values in this representative matrix.</text>"
            ),
            (
                f'  <text x="74" y="{footer_y + 62}" fill="#0c4a6e" '
                'font-size="14" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "Full observed records and exact SHA-256 values are bound in "
                "docs/visuals/manifest.json.</text>"
            ),
            (
                f'  <text x="74" y="{footer_y + 84}" fill="#0c4a6e" '
                'font-size="13" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                "This matrix tests contract behavior; it does not test routing, "
                "live data, reliability, or prediction.</text>"
            ),
            "</svg>",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def _source_records(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative_path in SOURCE_PATHS:
        payload = (root / relative_path).read_bytes()
        records.append(
            {
                "bytes": len(payload),
                "path": relative_path,
                "sha256": _sha256(payload),
            }
        )
    return records


def _edge_record(edge: TemporalEdge) -> dict[str, object]:
    return {
        "kind": edge.kind.value,
        "lower_bound_seconds": edge.lower_bound_seconds,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "upper_bound_seconds": edge.upper_bound_seconds,
    }


def _manifest_bytes(
    root: Path,
    graph: TemporalGraph,
    observations: Sequence[BoundaryObservation],
    outputs: Mapping[str, bytes],
) -> bytes:
    source_records = _source_records(root)
    public_limits = {
        "MAX_ACTIVITIES": MAX_ACTIVITIES,
        "MAX_HORIZON_SECONDS": MAX_HORIZON_SECONDS,
        "MAX_INPUT_BYTES": MAX_INPUT_BYTES,
        "MAX_JSON_DEPTH": MAX_JSON_DEPTH,
        "MAX_TRANSFERS": MAX_TRANSFERS,
    }
    edge_kind_counts = Counter(edge.kind.value for edge in graph.edges)
    manifest = {
        "check_command": CHECK_COMMAND,
        "evidence": {
            "boundary_matrix": [
                _observation_record(observation)
                for observation in observations
            ],
            "compiled_graph": {
                "activity_order": list(graph.activity_order),
                "contract_schema_version": graph.contract_schema_version,
                "edge_count": len(graph.edges),
                "edge_kind_counts": dict(sorted(edge_kind_counts.items())),
                "edges": [_edge_record(edge) for edge in graph.edges],
                "graph_schema_version": graph.graph_schema_version,
                "graph_sha256": graph_digest(graph),
                "itinerary_sha256": graph.itinerary_digest,
                "node_count": len(graph.nodes),
            },
            "public_error_codes": [code.value for code in ErrorCode],
            "public_limits": public_limits,
        },
        "outputs": [
            {
                "bytes": len(outputs[path]),
                "path": path,
                "sha256": _sha256(outputs[path]),
            }
            for path in sorted(outputs)
        ],
        "schema_version": 1,
        "sources": source_records,
    }
    return (
        json.dumps(
            manifest,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def build_bundle(root: Path) -> dict[str, bytes]:
    """Build all generated files in memory without mutating the repository."""

    fixture_payload = (root / FIXTURE_PATH).read_bytes()
    itinerary = decode_itinerary_json(fixture_payload)
    graph = compile_temporal_graph(itinerary)
    observations = collect_boundary_observations(fixture_payload)
    outputs = {
        GRAPH_OUTPUT: _graph_svg(graph),
        BOUNDARY_OUTPUT: _boundary_svg(observations),
    }
    manifest = _manifest_bytes(root, graph, observations, outputs)
    return {**outputs, MANIFEST_OUTPUT: manifest}


def _write_bundle(root: Path, bundle: Mapping[str, bytes]) -> None:
    for relative_path, payload in sorted(bundle.items()):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _check_bundle(root: Path, bundle: Mapping[str, bytes]) -> None:
    mismatches: list[str] = []
    for relative_path, expected in sorted(bundle.items()):
        target = root / relative_path
        if not target.is_file() or target.read_bytes() != expected:
            mismatches.append(relative_path)
    if mismatches:
        rendered = ",".join(mismatches)
        raise GenerationFailure(f"generated_artifact_drift:{rendered}")


def _parse_args(argv: Iterable[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic TCTravel visual evidence."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed artifacts without writing",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        bundle = build_bundle(REPOSITORY_ROOT)
        if arguments.check:
            _check_bundle(REPOSITORY_ROOT, bundle)
            action = "verified"
        else:
            _write_bundle(REPOSITORY_ROOT, bundle)
            action = "generated"
    except (GenerationFailure, OSError) as error:
        print(f"TCTravel visual evidence: FAIL ({error})", file=sys.stderr)
        return 1

    print(
        f"TCTravel visual evidence: PASS ({action} "
        f"{len(bundle) - 1} SVGs + manifest)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
