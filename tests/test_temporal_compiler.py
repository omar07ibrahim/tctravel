from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from tctravel import (
    EdgeKind,
    NodeKind,
    canonical_graph_bytes,
    compile_temporal_graph,
    decode_itinerary_json,
    graph_digest,
)

from tests.test_itinerary_contract import encoded, valid_document


class TemporalCompilerTests(unittest.TestCase):
    def test_compiler_uses_deterministic_lexical_topological_order(self) -> None:
        document = valid_document()
        document["activities"].extend(
            [
                {
                    "id": "bag_drop",
                    "release_offset_seconds": 0,
                    "duration": {
                        "minimum_seconds": 60,
                        "maximum_seconds": 120,
                    },
                    "hard_deadline_offset_seconds": 1000,
                },
                {
                    "id": "check_in",
                    "release_offset_seconds": 0,
                    "duration": {
                        "minimum_seconds": 60,
                        "maximum_seconds": 120,
                    },
                    "hard_deadline_offset_seconds": 1000,
                },
            ]
        )
        graph = compile_temporal_graph(decode_itinerary_json(encoded(document)))

        self.assertEqual(
            graph.activity_order,
            (
                "bag_drop",
                "check_in",
                "local_shuttle",
                "security_check",
                "gate_walk",
            ),
        )
        self.assertEqual(graph.nodes[0].node_id, "anchor")
        self.assertEqual(graph.nodes[0].kind, NodeKind.ANCHOR)
        self.assertEqual(
            tuple(node.node_id for node in graph.nodes[1:5]),
            (
                "activity:bag_drop:start",
                "activity:bag_drop:finish",
                "activity:check_in:start",
                "activity:check_in:finish",
            ),
        )

    def test_graph_contains_exact_constraint_semantics(self) -> None:
        graph = compile_temporal_graph(
            decode_itinerary_json(encoded(valid_document()))
        )
        edge_tuples = {
            (
                edge.source_node_id,
                edge.target_node_id,
                edge.kind,
                edge.lower_bound_seconds,
                edge.upper_bound_seconds,
            )
            for edge in graph.edges
        }

        self.assertIn(
            (
                "anchor",
                "activity:local_shuttle:start",
                EdgeKind.RELEASE,
                0,
                None,
            ),
            edge_tuples,
        )
        self.assertIn(
            (
                "activity:local_shuttle:start",
                "activity:local_shuttle:finish",
                EdgeKind.DURATION,
                600,
                900,
            ),
            edge_tuples,
        )
        self.assertIn(
            (
                "anchor",
                "activity:local_shuttle:finish",
                EdgeKind.HARD_DEADLINE,
                None,
                1800,
            ),
            edge_tuples,
        )
        self.assertIn(
            (
                "activity:local_shuttle:finish",
                "activity:security_check:start",
                EdgeKind.TRANSFER,
                120,
                None,
            ),
            edge_tuples,
        )
        self.assertEqual(len(graph.nodes), 7)
        self.assertEqual(len(graph.edges), 11)
        node_position = {
            node.node_id: position
            for position, node in enumerate(graph.nodes)
        }
        for edge in graph.edges:
            self.assertLess(
                node_position[edge.source_node_id],
                node_position[edge.target_node_id],
            )

    def test_compilation_does_not_claim_worst_case_deadline_safety(self) -> None:
        document = valid_document()
        document["activities"] = [
            {
                "id": "bounded_activity",
                "release_offset_seconds": 0,
                "duration": {
                    "minimum_seconds": 10,
                    "maximum_seconds": 100,
                },
                "hard_deadline_offset_seconds": 50,
            }
        ]
        document["transfers"] = []

        graph = compile_temporal_graph(decode_itinerary_json(encoded(document)))
        duration = next(
            edge for edge in graph.edges if edge.kind is EdgeKind.DURATION
        )
        deadline = next(
            edge for edge in graph.edges if edge.kind is EdgeKind.HARD_DEADLINE
        )

        self.assertEqual(duration.upper_bound_seconds, 100)
        self.assertEqual(deadline.upper_bound_seconds, 50)

    def test_equivalent_inputs_compile_to_identical_bytes_and_digest(self) -> None:
        first = valid_document()
        second = valid_document()
        second["activities"] = list(reversed(second["activities"]))
        second["transfers"] = list(reversed(second["transfers"]))

        first_graph = compile_temporal_graph(
            decode_itinerary_json(json.dumps(first))
        )
        second_graph = compile_temporal_graph(
            decode_itinerary_json(
                json.dumps(second, sort_keys=True, separators=(",", ":"))
            )
        )

        self.assertEqual(
            canonical_graph_bytes(first_graph),
            canonical_graph_bytes(second_graph),
        )
        self.assertEqual(
            graph_digest(first_graph),
            "9b9cf2a2f8f2eb8f5776d7febff2ee724ef27fad9e59ccf85607349923f71f2d",
        )
        self.assertEqual(graph_digest(first_graph), graph_digest(second_graph))
        self.assertTrue(canonical_graph_bytes(first_graph).isascii())

    def test_graph_model_is_immutable(self) -> None:
        graph = compile_temporal_graph(
            decode_itinerary_json(encoded(valid_document()))
        )
        with self.assertRaises(FrozenInstanceError):
            graph.horizon_seconds = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            graph.nodes[0].node_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
