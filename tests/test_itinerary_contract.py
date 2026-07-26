from __future__ import annotations

import copy
import json
import traceback
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

from tctravel import (
    MAX_ACTIVITIES,
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    MAX_TRANSFERS,
    ContractError,
    ErrorCode,
    canonical_itinerary_bytes,
    decode_itinerary_json,
    itinerary_digest,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic_connection.v1.json"


def valid_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "itinerary_id": "synthetic_connection",
        "anchor_time_utc": "2026-08-01T08:00:00Z",
        "horizon_seconds": 7200,
        "activities": [
            {
                "id": "local_shuttle",
                "release_offset_seconds": 0,
                "duration": {
                    "minimum_seconds": 600,
                    "maximum_seconds": 900,
                },
                "hard_deadline_offset_seconds": 1800,
            },
            {
                "id": "security_check",
                "release_offset_seconds": 0,
                "duration": {
                    "minimum_seconds": 300,
                    "maximum_seconds": 600,
                },
                "hard_deadline_offset_seconds": 3000,
            },
            {
                "id": "gate_walk",
                "release_offset_seconds": 0,
                "duration": {
                    "minimum_seconds": 240,
                    "maximum_seconds": 360,
                },
                "hard_deadline_offset_seconds": 4200,
            },
        ],
        "transfers": [
            {
                "from_activity_id": "local_shuttle",
                "to_activity_id": "security_check",
                "minimum_seconds": 120,
            },
            {
                "from_activity_id": "security_check",
                "to_activity_id": "gate_walk",
                "minimum_seconds": 60,
            },
        ],
    }


def encoded(document: object) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


class ItineraryContractTests(unittest.TestCase):
    def assert_code(self, payload: bytes | str, code: ErrorCode) -> None:
        with self.assertRaises(ContractError) as raised:
            decode_itinerary_json(payload)
        self.assertEqual(raised.exception.code, code)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(
            raised.exception.as_dict(), {"error": {"code": code.value}}
        )

    def test_original_example_decodes_and_is_immutable(self) -> None:
        itinerary = decode_itinerary_json(EXAMPLE.read_bytes())
        self.assertEqual(itinerary.schema_version, 1)
        self.assertEqual(
            tuple(activity.activity_id for activity in itinerary.activities),
            ("gate_walk", "local_shuttle", "security_check"),
        )
        with self.assertRaises(FrozenInstanceError):
            itinerary.horizon_seconds = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            itinerary.activities[0].activity_id = "changed"  # type: ignore[misc]

    def test_example_and_equivalent_reordering_have_identical_bytes(self) -> None:
        document = valid_document()
        document["activities"] = list(reversed(document["activities"]))
        document["transfers"] = list(reversed(document["transfers"]))
        reordered = json.dumps(document, indent=3, sort_keys=True)

        from_example = decode_itinerary_json(EXAMPLE.read_bytes())
        from_reordered = decode_itinerary_json(reordered)

        self.assertEqual(
            canonical_itinerary_bytes(from_example),
            canonical_itinerary_bytes(from_reordered),
        )
        self.assertEqual(
            itinerary_digest(from_example),
            "61b76d3e8d6c5fb0e9b9ae65ab4a0043a3fd24e64182f1ed0a3a489bb2f30aa6",
        )
        self.assertEqual(
            itinerary_digest(from_example), itinerary_digest(from_reordered)
        )
        self.assertTrue(canonical_itinerary_bytes(from_example).isascii())

    def test_unknown_and_missing_fields_fail_closed(self) -> None:
        unknown = valid_document()
        unknown["untrusted_secret_field"] = "never-log-this"
        self.assert_code(encoded(unknown), ErrorCode.UNKNOWN_FIELD)

        missing = valid_document()
        del missing["anchor_time_utc"]
        self.assert_code(encoded(missing), ErrorCode.MISSING_FIELD)

        nested_unknown = valid_document()
        nested_unknown["activities"][0]["duration"]["extra"] = 1
        self.assert_code(encoded(nested_unknown), ErrorCode.UNKNOWN_FIELD)

        nested_missing = valid_document()
        del nested_missing["transfers"][0]["minimum_seconds"]
        self.assert_code(encoded(nested_missing), ErrorCode.MISSING_FIELD)

    def test_boolean_never_satisfies_an_integer_field(self) -> None:
        mutations = (
            ("schema_version",),
            ("horizon_seconds",),
            ("activities", 0, "release_offset_seconds"),
            ("activities", 0, "duration", "minimum_seconds"),
            ("activities", 0, "duration", "maximum_seconds"),
            ("activities", 0, "hard_deadline_offset_seconds"),
            ("transfers", 0, "minimum_seconds"),
        )
        for path in mutations:
            with self.subTest(path=path):
                document = valid_document()
                target: Any = document
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = True
                self.assert_code(encoded(document), ErrorCode.INVALID_TYPE)

    def test_non_integer_numbers_fail_closed(self) -> None:
        payload = encoded(valid_document()).replace(
            b'"horizon_seconds":7200', b'"horizon_seconds":7200.0'
        )
        self.assert_code(payload, ErrorCode.INVALID_TYPE)
        self.assert_code(
            payload.replace(b"7200.0", b"NaN"), ErrorCode.INVALID_TYPE
        )

    def test_invalid_identifier_timestamp_and_ranges_are_rejected(self) -> None:
        invalid_id = valid_document()
        invalid_id["itinerary_id"] = "PRIVATE/VALUE"
        self.assert_code(encoded(invalid_id), ErrorCode.INVALID_IDENTIFIER)

        invalid_timestamp = valid_document()
        invalid_timestamp["anchor_time_utc"] = "2026-02-30T08:00:00Z"
        self.assert_code(
            encoded(invalid_timestamp), ErrorCode.INVALID_TIMESTAMP
        )

        invalid_duration = valid_document()
        invalid_duration["activities"][0]["duration"]["minimum_seconds"] = 901
        self.assert_code(encoded(invalid_duration), ErrorCode.OUT_OF_RANGE)

        unsupported_version = valid_document()
        unsupported_version["schema_version"] = 2
        self.assert_code(encoded(unsupported_version), ErrorCode.OUT_OF_RANGE)

    def test_duplicate_json_key_is_rejected_before_schema_validation(self) -> None:
        self.assert_code(
            '{"schema_version":1,"schema_version":1}',
            ErrorCode.DUPLICATE_JSON_KEY,
        )
        self.assert_code(
            '{"schema_version":1,"schema_\\u0076ersion":1}',
            ErrorCode.DUPLICATE_JSON_KEY,
        )

    def test_duplicate_activities_and_transfers_are_rejected(self) -> None:
        duplicate_activity = valid_document()
        duplicate_activity["activities"].append(
            copy.deepcopy(duplicate_activity["activities"][0])
        )
        self.assert_code(
            encoded(duplicate_activity), ErrorCode.DUPLICATE_ACTIVITY_ID
        )

        duplicate_transfer = valid_document()
        repeated = copy.deepcopy(duplicate_transfer["transfers"][0])
        repeated["minimum_seconds"] = 121
        duplicate_transfer["transfers"].append(repeated)
        self.assert_code(
            encoded(duplicate_transfer), ErrorCode.DUPLICATE_TRANSFER
        )

    def test_transfer_references_self_edges_and_cycles_are_rejected(self) -> None:
        unknown = valid_document()
        unknown["transfers"][0]["from_activity_id"] = "unknown_activity"
        self.assert_code(
            encoded(unknown), ErrorCode.UNKNOWN_TRANSFER_REFERENCE
        )

        self_edge = valid_document()
        self_edge["transfers"][0]["to_activity_id"] = "local_shuttle"
        self.assert_code(encoded(self_edge), ErrorCode.SELF_TRANSFER)

        cycle = valid_document()
        cycle["transfers"].append(
            {
                "from_activity_id": "gate_walk",
                "to_activity_id": "local_shuttle",
                "minimum_seconds": 0,
            }
        )
        self.assert_code(encoded(cycle), ErrorCode.DEPENDENCY_CYCLE)

    def test_impossible_lower_bound_schedule_is_rejected(self) -> None:
        impossible = valid_document()
        impossible["activities"][1]["hard_deadline_offset_seconds"] = 900
        self.assert_code(
            encoded(impossible), ErrorCode.INFEASIBLE_DEADLINE
        )

    def test_size_depth_and_collection_limits_are_enforced(self) -> None:
        self.assert_code(
            b" " * (MAX_INPUT_BYTES + 1), ErrorCode.INPUT_TOO_LARGE
        )
        deeply_nested = ("[" * (MAX_JSON_DEPTH + 1)) + "0" + (
            "]" * (MAX_JSON_DEPTH + 1)
        )
        self.assert_code(deeply_nested, ErrorCode.INPUT_TOO_DEEP)

        too_many_activities = valid_document()
        prototype = too_many_activities["activities"][0]
        too_many_activities["activities"] = []
        for index in range(MAX_ACTIVITIES + 1):
            activity = copy.deepcopy(prototype)
            activity["id"] = f"a{index:03d}"
            too_many_activities["activities"].append(activity)
        too_many_activities["transfers"] = []
        self.assertLess(len(encoded(too_many_activities)), MAX_INPUT_BYTES)
        self.assert_code(encoded(too_many_activities), ErrorCode.OUT_OF_RANGE)

        too_many_transfers = valid_document()
        prototype_transfer = too_many_transfers["transfers"][0]
        too_many_transfers["transfers"] = [
            copy.deepcopy(prototype_transfer)
            for _ in range(MAX_TRANSFERS + 1)
        ]
        self.assertLess(len(encoded(too_many_transfers)), MAX_INPUT_BYTES)
        self.assert_code(encoded(too_many_transfers), ErrorCode.OUT_OF_RANGE)

    def test_declared_resource_boundaries_are_accepted(self) -> None:
        compact = encoded(valid_document())
        exactly_maximum_bytes = compact + (
            b" " * (MAX_INPUT_BYTES - len(compact))
        )
        self.assertEqual(len(exactly_maximum_bytes), MAX_INPUT_BYTES)
        decode_itinerary_json(exactly_maximum_bytes)

        activities = [
            {
                "id": f"a{index:03d}",
                "release_offset_seconds": 0,
                "duration": {
                    "minimum_seconds": 1,
                    "maximum_seconds": 2,
                },
                "hard_deadline_offset_seconds": 1000,
            }
            for index in range(MAX_ACTIVITIES)
        ]
        endpoint_pairs = [
            (source, target)
            for source in range(MAX_ACTIVITIES)
            for target in range(source + 1, MAX_ACTIVITIES)
        ][:MAX_TRANSFERS]
        boundary_document = {
            "schema_version": 1,
            "itinerary_id": "resource_boundary",
            "anchor_time_utc": "2026-08-01T00:00:00Z",
            "horizon_seconds": 1000,
            "activities": activities,
            "transfers": [
                {
                    "from_activity_id": f"a{source:03d}",
                    "to_activity_id": f"a{target:03d}",
                    "minimum_seconds": 0,
                }
                for source, target in endpoint_pairs
            ],
        }
        boundary_payload = encoded(boundary_document)
        self.assertLessEqual(len(boundary_payload), MAX_INPUT_BYTES)

        itinerary = decode_itinerary_json(boundary_payload)
        self.assertEqual(len(itinerary.activities), MAX_ACTIVITIES)
        self.assertEqual(len(itinerary.transfers), MAX_TRANSFERS)

    def test_invalid_encoding_and_large_integer_are_redacted(self) -> None:
        redaction_cases: tuple[tuple[bytes | str, str, ErrorCode], ...] = (
            (
                b'{"private-sentinel":\xff}',
                "private-sentinel",
                ErrorCode.INVALID_UTF8,
            ),
            (
                '{"private-sentinel":1 "missing-comma":2}',
                "private-sentinel",
                ErrorCode.INVALID_JSON,
            ),
            (
                '{"private-sentinel":"\ud800"}',
                "private-sentinel",
                ErrorCode.INVALID_UTF8,
            ),
        )
        for payload, sentinel, code in redaction_cases:
            with self.subTest(code=code):
                with self.assertRaises(ContractError) as raised:
                    decode_itinerary_json(payload)
                error = raised.exception
                self.assertEqual(error.code, code)
                self.assertIsNone(error.__context__)
                self.assertIsNone(error.__cause__)
                rendered_traceback = "".join(
                    traceback.format_exception(error)
                )
                self.assertNotIn(sentinel, rendered_traceback)

        huge_integer = encoded(valid_document()).replace(
            b'"horizon_seconds":7200',
            b'"horizon_seconds":99999999999',
        )
        self.assert_code(huge_integer, ErrorCode.OUT_OF_RANGE)

        private_value = "do-not-echo/private-value"
        document = valid_document()
        document["itinerary_id"] = private_value
        with self.assertRaises(ContractError) as raised:
            decode_itinerary_json(encoded(document))
        rendered = f"{raised.exception!s} {raised.exception!r}"
        self.assertNotIn(private_value, rendered)
        self.assertNotIn(str(EXAMPLE), rendered)


if __name__ == "__main__":
    unittest.main()
