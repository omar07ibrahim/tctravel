from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast
from unittest import mock

from tctravel import (
    MAX_ACTIVITIES,
    MAX_HORIZON_SECONDS,
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    MAX_TRANSFERS,
    ErrorCode,
    compile_temporal_graph,
    decode_itinerary_json,
    graph_digest,
)
from tools import generate_visuals


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "synthetic_connection.v1.json"
MANIFEST = ROOT / generate_visuals.MANIFEST_OUTPUT
SVG_PATHS = (
    generate_visuals.BOUNDARY_OUTPUT,
    generate_visuals.GRAPH_OUTPUT,
)
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
FROZEN_SHA256 = {
    generate_visuals.BOUNDARY_OUTPUT: (
        "3285a8f4a1987b123f5fa35aef6d92c8254f84e8c80e841c7412fe849bd43d0c"
    ),
    generate_visuals.GRAPH_OUTPUT: (
        "04afa88063127426535ec4191f7a8dacd10f02ca04cf220a27492f678abe0314"
    ),
    generate_visuals.MANIFEST_OUTPUT: (
        "5c5c78ba655263fecdceea815ab02999a173c1e8d89b069dfe7caef80d0c1f4c"
    ),
}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class VisualEvidenceTests(unittest.TestCase):
    def test_reordered_probe_changes_arrays_and_every_object_key_order(
        self,
    ) -> None:
        def assert_nested_objects_reversed(
            original: dict[str, object],
            mutated: dict[str, object],
        ) -> None:
            self.assertEqual(
                list(mutated),
                list(reversed(tuple(original))),
            )
            for key, original_value in original.items():
                if type(original_value) is dict:
                    assert_nested_objects_reversed(
                        original_value,
                        cast(dict[str, object], mutated[key]),
                    )

        baseline = json.loads(FIXTURE.read_bytes())
        reordered_case = next(
            case
            for case in generate_visuals._boundary_cases(
                FIXTURE.read_bytes()
            )
            if case.case_id == "accepted_reordered"
        )
        reordered = json.loads(reordered_case.payload)

        self.assertEqual(
            list(reordered),
            list(reversed(tuple(baseline))),
        )
        for collection in ("activities", "transfers"):
            baseline_items = baseline[collection]
            reordered_items = reordered[collection]
            self.assertEqual(
                reordered_items,
                list(reversed(baseline_items)),
            )
            for original, mutated in zip(
                reversed(baseline_items),
                reordered_items,
                strict=True,
            ):
                assert_nested_objects_reversed(original, mutated)

    def test_generated_bundle_is_byte_reproducible(self) -> None:
        bundle = generate_visuals.build_bundle(ROOT)
        self.assertEqual(
            set(bundle),
            {
                generate_visuals.BOUNDARY_OUTPUT,
                generate_visuals.GRAPH_OUTPUT,
                generate_visuals.MANIFEST_OUTPUT,
            },
        )
        for relative_path, expected in bundle.items():
            with self.subTest(path=relative_path):
                self.assertEqual(
                    (ROOT / relative_path).read_bytes(),
                    expected,
                )

    def test_documented_check_command_is_read_only_and_succeeds(self) -> None:
        tracked_before = {
            relative_path: (ROOT / relative_path).read_bytes()
            for relative_path in (*SVG_PATHS, generate_visuals.MANIFEST_OUTPUT)
        }
        completed = subprocess.run(
            [sys.executable, "tools/generate_visuals.py", "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "TCTravel visual evidence: PASS "
            "(verified 2 SVGs + manifest)\n",
        )
        self.assertEqual(
            tracked_before,
            {
                relative_path: (ROOT / relative_path).read_bytes()
                for relative_path in (
                    *SVG_PATHS,
                    generate_visuals.MANIFEST_OUTPUT,
                )
            },
        )

    def test_readme_and_evidence_note_embed_real_artifacts(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        evidence_note = (ROOT / "docs" / "visual-evidence.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(generate_visuals.CHECK_COMMAND, readme)
        self.assertIn(generate_visuals.CHECK_COMMAND, evidence_note)
        for relative_path in SVG_PATHS:
            self.assertIn(relative_path, readme)
            self.assertIn(Path(relative_path).name, evidence_note)
        self.assertIn(generate_visuals.MANIFEST_OUTPUT, readme)
        self.assertIn("visuals/manifest.json", evidence_note)

    def test_manifest_binds_exact_sources_outputs_and_public_evidence(
        self,
    ) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="ascii"))
        self.assertEqual(
            set(manifest),
            {
                "check_command",
                "evidence",
                "outputs",
                "schema_version",
                "sources",
            },
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(
            manifest["check_command"],
            generate_visuals.CHECK_COMMAND,
        )

        sources = manifest["sources"]
        self.assertEqual(
            [record["path"] for record in sources],
            list(generate_visuals.SOURCE_PATHS),
        )
        for record in sources:
            relative_path = record["path"]
            payload = (ROOT / relative_path).read_bytes()
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], sha256(payload))
            self.assertNotIn("legacy", relative_path.casefold())

        outputs = manifest["outputs"]
        self.assertEqual(
            [record["path"] for record in outputs],
            sorted(SVG_PATHS),
        )
        for record in outputs:
            payload = (ROOT / record["path"]).read_bytes()
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], sha256(payload))

        evidence = manifest["evidence"]
        self.assertEqual(
            evidence["public_limits"],
            {
                "MAX_ACTIVITIES": MAX_ACTIVITIES,
                "MAX_HORIZON_SECONDS": MAX_HORIZON_SECONDS,
                "MAX_INPUT_BYTES": MAX_INPUT_BYTES,
                "MAX_JSON_DEPTH": MAX_JSON_DEPTH,
                "MAX_TRANSFERS": MAX_TRANSFERS,
            },
        )
        self.assertEqual(
            evidence["public_error_codes"],
            [code.value for code in ErrorCode],
        )

        graph = compile_temporal_graph(
            decode_itinerary_json(FIXTURE.read_bytes())
        )
        compiled = evidence["compiled_graph"]
        self.assertEqual(compiled["graph_sha256"], graph_digest(graph))
        self.assertEqual(compiled["itinerary_sha256"], graph.itinerary_digest)
        self.assertEqual(compiled["node_count"], len(graph.nodes))
        self.assertEqual(compiled["edge_count"], len(graph.edges))
        self.assertEqual(
            compiled["edges"],
            [
                {
                    "kind": edge.kind.value,
                    "lower_bound_seconds": edge.lower_bound_seconds,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "upper_bound_seconds": edge.upper_bound_seconds,
                }
                for edge in graph.edges
            ],
        )

    def test_generator_calls_public_package_api_without_private_imports(
        self,
    ) -> None:
        generator_source = (
            ROOT / "tools" / "generate_visuals.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(generator_source)
        tctravel_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("tctravel")
        ]
        self.assertEqual(tctravel_imports, ["tctravel"])

        with (
            mock.patch.object(
                generate_visuals,
                "decode_itinerary_json",
                wraps=decode_itinerary_json,
            ) as decoder,
            mock.patch.object(
                generate_visuals,
                "compile_temporal_graph",
                wraps=compile_temporal_graph,
            ) as compiler,
        ):
            generate_visuals.build_bundle(ROOT)
        self.assertGreaterEqual(decoder.call_count, 11)
        self.assertGreaterEqual(compiler.call_count, 4)

    def test_graph_svg_carries_every_observed_public_edge(self) -> None:
        graph = compile_temporal_graph(
            decode_itinerary_json(FIXTURE.read_bytes())
        )
        root = ET.fromstring(
            (ROOT / generate_visuals.GRAPH_OUTPUT).read_bytes()
        )
        self.assertEqual(
            root.attrib["data-itinerary-sha256"],
            graph.itinerary_digest,
        )
        self.assertEqual(
            root.attrib["data-graph-sha256"],
            graph_digest(graph),
        )

        observed_edges = []
        for element in root.iter():
            if "data-edge-kind" not in element.attrib:
                continue
            lower = element.attrib["data-lower-bound-seconds"]
            upper = element.attrib["data-upper-bound-seconds"]
            observed_edges.append(
                (
                    element.attrib["data-source-node-id"],
                    element.attrib["data-target-node-id"],
                    element.attrib["data-edge-kind"],
                    None if lower == "" else int(lower),
                    None if upper == "" else int(upper),
                )
            )
        self.assertEqual(
            observed_edges,
            [
                (
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.kind.value,
                    edge.lower_bound_seconds,
                    edge.upper_bound_seconds,
                )
                for edge in graph.edges
            ],
        )

        visible_text = "".join(root.itertext())
        self.assertIn(graph.itinerary_digest, visible_text)
        self.assertIn(graph_digest(graph), visible_text)
        for activity_id in graph.activity_order:
            self.assertIn(activity_id, visible_text)

    def test_boundary_svg_records_executed_cases_and_public_limits(
        self,
    ) -> None:
        observations = generate_visuals.collect_boundary_observations(
            FIXTURE.read_bytes()
        )
        root = ET.fromstring(
            (ROOT / generate_visuals.BOUNDARY_OUTPUT).read_bytes()
        )

        rendered_cases = {}
        rendered_limits = {}
        for element in root.iter():
            if "data-case-id" in element.attrib:
                rendered_cases[element.attrib["data-case-id"]] = element.attrib
            if "data-public-constant" in element.attrib:
                rendered_limits[
                    element.attrib["data-public-constant"]
                ] = int(element.attrib["data-value"])

        self.assertEqual(
            set(rendered_cases),
            {observation.case_id for observation in observations},
        )
        for observation in observations:
            record = rendered_cases[observation.case_id]
            self.assertEqual(record["data-status"], observation.status)
            self.assertEqual(record["data-code"], observation.code or "")
            self.assertEqual(
                record["data-itinerary-sha256"],
                observation.itinerary_sha256 or "",
            )
            self.assertEqual(
                record["data-graph-sha256"],
                observation.graph_sha256 or "",
            )

        self.assertEqual(
            rendered_limits,
            {
                "MAX_ACTIVITIES": MAX_ACTIVITIES,
                "MAX_HORIZON_SECONDS": MAX_HORIZON_SECONDS,
                "MAX_INPUT_BYTES": MAX_INPUT_BYTES,
                "MAX_JSON_DEPTH": MAX_JSON_DEPTH,
                "MAX_TRANSFERS": MAX_TRANSFERS,
            },
        )

    def test_svgs_are_accessible_fixed_canvas_and_self_contained(self) -> None:
        expected_viewboxes = {
            generate_visuals.BOUNDARY_OUTPUT: (
                generate_visuals.BOUNDARY_VIEWBOX
            ),
            generate_visuals.GRAPH_OUTPUT: generate_visuals.GRAPH_VIEWBOX,
        }
        forbidden_elements = {"a", "foreignObject", "image", "script"}

        for relative_path in SVG_PATHS:
            with self.subTest(path=relative_path):
                root = ET.fromstring((ROOT / relative_path).read_bytes())
                self.assertEqual(
                    root.tag,
                    f"{{{SVG_NAMESPACE}}}svg",
                )
                self.assertEqual(
                    root.attrib["viewBox"],
                    expected_viewboxes[relative_path],
                )
                self.assertEqual(root.attrib["role"], "img")
                self.assertGreater(int(root.attrib["width"]), 0)
                self.assertGreater(int(root.attrib["height"]), 0)

                labelled_by = root.attrib["aria-labelledby"].split()
                identifiers = {
                    element.attrib["id"]
                    for element in root.iter()
                    if "id" in element.attrib
                }
                self.assertEqual(len(labelled_by), 2)
                self.assertTrue(set(labelled_by).issubset(identifiers))
                self.assertIsNotNone(root.find(f"{{{SVG_NAMESPACE}}}title"))
                self.assertIsNotNone(root.find(f"{{{SVG_NAMESPACE}}}desc"))

                for element in root.iter():
                    self.assertNotIn(
                        local_name(element.tag),
                        forbidden_elements,
                    )
                    for name, value in element.attrib.items():
                        self.assertFalse(name.casefold().endswith("href"))
                        if "url(" in value:
                            self.assertTrue(
                                value.startswith("url(#"),
                                (relative_path, name, value),
                            )

    def test_generated_evidence_is_sanitized_and_claim_bounded(self) -> None:
        forbidden_tokens = (
            "/home/",
            "/Users/",
            "C:\\",
            "file://",
            "localhost",
            "mailto:",
            "tel:",
            "+994",
            "@",
            "api_key",
            "password",
            "private_key",
        )
        for relative_path in (*SVG_PATHS, generate_visuals.MANIFEST_OUTPUT):
            with self.subTest(path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                for token in forbidden_tokens:
                    self.assertNotIn(token, text)

        graph_text = (ROOT / generate_visuals.GRAPH_OUTPUT).read_text(
            encoding="utf-8"
        )
        boundary_text = (ROOT / generate_visuals.BOUNDARY_OUTPUT).read_text(
            encoding="utf-8"
        )
        for claim in ("no route choice", "reliability estimate", "prediction"):
            self.assertIn(claim, graph_text)
        for claim in ("does not test routing", "reliability", "prediction"):
            self.assertIn(claim, boundary_text)

    def test_generated_files_match_frozen_hashes(self) -> None:
        for relative_path, expected_sha256 in FROZEN_SHA256.items():
            with self.subTest(path=relative_path):
                self.assertEqual(
                    sha256((ROOT / relative_path).read_bytes()),
                    expected_sha256,
                )


if __name__ == "__main__":
    unittest.main()
