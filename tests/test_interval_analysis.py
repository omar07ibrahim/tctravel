from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

from tctravel import (
    MAX_ACTIVITIES,
    MAX_TRANSFERS,
    ContractError,
    ErrorCode,
    Itinerary,
    decode_itinerary_json,
)
from tctravel.analysis import (
    ActivityFeasibility,
    FeasibilityStatus,
    IntervalFeasibilityReport,
    analysis_digest,
    analyze_interval_feasibility,
    canonical_analysis_bytes,
)

from tests.test_itinerary_contract import encoded, valid_document


ROOT = Path(__file__).resolve().parents[1]
ROBUST_EXAMPLE = ROOT / "examples" / "synthetic_connection.v1.json"
TIGHT_EXAMPLE = ROOT / "examples" / "synthetic_tight_connection.v1.json"


def _activity(
    report: IntervalFeasibilityReport, activity_id: str
) -> ActivityFeasibility:
    return next(
        activity
        for activity in report.activities
        if activity.activity_id == activity_id
    )


def _oracle_schedule(
    document: dict[str, Any], durations: dict[str, int]
) -> tuple[dict[str, int], dict[str, int]]:
    """Independent max-plus relaxation without production ordering helpers."""

    activities = {
        activity["id"]: activity for activity in document["activities"]
    }
    starts = {
        activity_id: activity["release_offset_seconds"]
        for activity_id, activity in activities.items()
    }
    finishes = {
        activity_id: starts[activity_id] + durations[activity_id]
        for activity_id in activities
    }
    for _ in range(len(activities)):
        changed = False
        for transfer in document["transfers"]:
            target = transfer["to_activity_id"]
            candidate = (
                finishes[transfer["from_activity_id"]]
                + transfer["minimum_seconds"]
            )
            if candidate > starts[target]:
                starts[target] = candidate
                finishes[target] = candidate + durations[target]
                changed = True
        if not changed:
            break
    else:
        raise AssertionError("oracle did not converge")
    return starts, finishes


def _oracle_witness_chain(
    document: dict[str, Any], durations: dict[str, int], endpoint: str
) -> tuple[str, ...]:
    starts, finishes = _oracle_schedule(document, durations)
    incoming: dict[str, list[tuple[str, int]]] = {
        activity["id"]: [] for activity in document["activities"]
    }
    for transfer in document["transfers"]:
        incoming[transfer["to_activity_id"]].append(
            (
                transfer["from_activity_id"],
                finishes[transfer["from_activity_id"]]
                + transfer["minimum_seconds"],
            )
        )
    witness = {
        activity_id: min(
            (
                predecessor_id
                for predecessor_id, candidate in candidates
                if candidate == starts[activity_id]
            ),
            default=None,
        )
        for activity_id, candidates in incoming.items()
    }
    reverse_chain: list[str] = []
    cursor: str | None = endpoint
    while cursor is not None:
        reverse_chain.append(cursor)
        cursor = witness[cursor]
    return tuple(reversed(reverse_chain))


class IntervalAnalysisTests(unittest.TestCase):
    def test_robust_example_has_exact_minimum_and_maximum_envelopes(self) -> None:
        report = analyze_interval_feasibility(
            decode_itinerary_json(ROBUST_EXAMPLE.read_bytes())
        )

        self.assertEqual(report.status, FeasibilityStatus.ROBUST)
        self.assertEqual(report.best_case.minimum_deadline_slack_seconds, 1200)
        self.assertEqual(report.best_case.critical_activity_ids, ("local_shuttle",))
        self.assertEqual(report.best_case.witness_chain, ("local_shuttle",))
        self.assertEqual(report.worst_case.minimum_deadline_slack_seconds, 900)
        self.assertEqual(report.worst_case.critical_activity_ids, ("local_shuttle",))
        self.assertEqual(
            analysis_digest(report),
            "7ddafbf24ec6300e127eb15db691d13676bbf9c83e69efc34e75695841b840a2",
        )

        expected = {
            "local_shuttle": ((0, 600, 1200), (0, 900, 900)),
            "security_check": ((720, 1020, 1980), (1020, 1620, 1380)),
            "gate_walk": ((1080, 1320, 2880), (1680, 2040, 2160)),
        }
        for activity_id, (best, worst) in expected.items():
            result = _activity(report, activity_id)
            self.assertEqual(
                (
                    result.best_case.earliest_start_offset_seconds,
                    result.best_case.earliest_finish_offset_seconds,
                    result.best_case.deadline_slack_seconds,
                ),
                best,
            )
            self.assertEqual(
                (
                    result.worst_case.earliest_start_offset_seconds,
                    result.worst_case.earliest_finish_offset_seconds,
                    result.worst_case.deadline_slack_seconds,
                ),
                worst,
            )
            self.assertEqual(result.status, FeasibilityStatus.ROBUST)

    def test_tight_example_is_duration_sensitive_with_equality_robust(self) -> None:
        report = analyze_interval_feasibility(
            decode_itinerary_json(TIGHT_EXAMPLE.read_bytes())
        )

        self.assertEqual(report.status, FeasibilityStatus.DURATION_SENSITIVE)
        self.assertEqual(report.best_case.minimum_deadline_slack_seconds, 300)
        self.assertEqual(report.worst_case.minimum_deadline_slack_seconds, -120)
        self.assertEqual(report.worst_case.critical_activity_ids, ("security_check",))
        self.assertEqual(
            report.worst_case.witness_chain,
            ("local_shuttle", "security_check"),
        )
        self.assertEqual(
            _activity(report, "local_shuttle").worst_case.deadline_slack_seconds,
            0,
        )
        self.assertEqual(
            _activity(report, "local_shuttle").status,
            FeasibilityStatus.ROBUST,
        )
        self.assertEqual(
            _activity(report, "security_check").status,
            FeasibilityStatus.DURATION_SENSITIVE,
        )
        self.assertEqual(
            analysis_digest(report),
            "35dfcc56368c12f3a8d4e12a4e715dd1f4fc9cef30a4613c736cdb412bfc0c2e",
        )

    def test_all_binding_predecessors_and_lexical_witness_are_preserved(self) -> None:
        document = {
            "schema_version": 1,
            "itinerary_id": "binding_tie",
            "anchor_time_utc": "2026-08-01T00:00:00Z",
            "horizon_seconds": 100,
            "activities": [
                {
                    "id": "c",
                    "release_offset_seconds": 5,
                    "duration": {"minimum_seconds": 1, "maximum_seconds": 1},
                    "hard_deadline_offset_seconds": 10,
                },
                {
                    "id": "b",
                    "release_offset_seconds": 0,
                    "duration": {"minimum_seconds": 5, "maximum_seconds": 5},
                    "hard_deadline_offset_seconds": 10,
                },
                {
                    "id": "a",
                    "release_offset_seconds": 0,
                    "duration": {"minimum_seconds": 5, "maximum_seconds": 5},
                    "hard_deadline_offset_seconds": 10,
                },
            ],
            "transfers": [
                {"from_activity_id": "b", "to_activity_id": "c", "minimum_seconds": 0},
                {"from_activity_id": "a", "to_activity_id": "c", "minimum_seconds": 0},
            ],
        }
        report = analyze_interval_feasibility(
            decode_itinerary_json(encoded(document))
        )
        timing = _activity(report, "c").worst_case

        self.assertTrue(timing.release_is_binding)
        self.assertEqual(timing.binding_predecessor_ids, ("a", "b"))
        self.assertEqual(timing.witness_predecessor_id, "a")
        self.assertEqual(report.worst_case.witness_activity_id, "c")
        self.assertEqual(report.worst_case.witness_chain, ("a", "c"))

    def test_endpoint_enumeration_oracle_matches_report(self) -> None:
        document = {
            "schema_version": 1,
            "itinerary_id": "oracle_fan_in",
            "anchor_time_utc": "2026-08-01T00:00:00Z",
            "horizon_seconds": 100,
            "activities": [
                {
                    "id": "a",
                    "release_offset_seconds": 0,
                    "duration": {"minimum_seconds": 1, "maximum_seconds": 3},
                    "hard_deadline_offset_seconds": 4,
                },
                {
                    "id": "b",
                    "release_offset_seconds": 1,
                    "duration": {"minimum_seconds": 2, "maximum_seconds": 4},
                    "hard_deadline_offset_seconds": 7,
                },
                {
                    "id": "c",
                    "release_offset_seconds": 0,
                    "duration": {"minimum_seconds": 1, "maximum_seconds": 2},
                    "hard_deadline_offset_seconds": 6,
                },
            ],
            "transfers": [
                {"from_activity_id": "a", "to_activity_id": "c", "minimum_seconds": 1},
                {"from_activity_id": "b", "to_activity_id": "c", "minimum_seconds": 0},
            ],
        }
        report = analyze_interval_feasibility(
            decode_itinerary_json(encoded(document))
        )
        duration_options = {
            activity["id"]: (
                activity["duration"]["minimum_seconds"],
                activity["duration"]["maximum_seconds"],
            )
            for activity in document["activities"]
        }
        ids = tuple(sorted(duration_options))
        schedules = []
        all_meet_deadlines = True
        deadlines = {
            activity["id"]: activity["hard_deadline_offset_seconds"]
            for activity in document["activities"]
        }
        for values in itertools.product(*(duration_options[item] for item in ids)):
            durations = dict(zip(ids, values, strict=True))
            starts, finishes = _oracle_schedule(document, durations)
            schedules.append((starts, finishes))
            all_meet_deadlines &= all(
                finishes[item] <= deadlines[item] for item in ids
            )

        self.assertFalse(all_meet_deadlines)
        self.assertEqual(report.status, FeasibilityStatus.DURATION_SENSITIVE)
        minimum_durations = {
            activity_id: duration_options[activity_id][0]
            for activity_id in ids
        }
        maximum_durations = {
            activity_id: duration_options[activity_id][1]
            for activity_id in ids
        }
        best_starts, best_finishes = _oracle_schedule(
            document, minimum_durations
        )
        worst_starts, worst_finishes = _oracle_schedule(
            document, maximum_durations
        )
        for activity_id in ids:
            result = _activity(report, activity_id)
            self.assertEqual(
                result.best_case.earliest_start_offset_seconds,
                min(starts[activity_id] for starts, _ in schedules),
            )
            self.assertEqual(
                result.best_case.earliest_finish_offset_seconds,
                min(finishes[activity_id] for _, finishes in schedules),
            )
            self.assertEqual(
                result.worst_case.earliest_start_offset_seconds,
                max(starts[activity_id] for starts, _ in schedules),
            )
            self.assertEqual(
                result.worst_case.earliest_finish_offset_seconds,
                max(finishes[activity_id] for _, finishes in schedules),
            )
            self.assertEqual(
                result.best_case.deadline_slack_seconds,
                deadlines[activity_id] - best_finishes[activity_id],
            )
            self.assertEqual(
                result.worst_case.deadline_slack_seconds,
                deadlines[activity_id] - worst_finishes[activity_id],
            )
            expected_status = (
                FeasibilityStatus.ROBUST
                if all(
                    finishes[activity_id] <= deadlines[activity_id]
                    for _, finishes in schedules
                )
                else FeasibilityStatus.DURATION_SENSITIVE
            )
            self.assertEqual(result.status, expected_status)

        for scenario, durations, finishes in (
            (report.best_case, minimum_durations, best_finishes),
            (report.worst_case, maximum_durations, worst_finishes),
        ):
            slacks = {
                activity_id: deadlines[activity_id] - finishes[activity_id]
                for activity_id in ids
            }
            minimum_slack = min(slacks.values())
            critical_ids = tuple(
                sorted(
                    activity_id
                    for activity_id, slack in slacks.items()
                    if slack == minimum_slack
                )
            )
            self.assertEqual(scenario.minimum_deadline_slack_seconds, minimum_slack)
            self.assertEqual(scenario.critical_activity_ids, critical_ids)
            self.assertEqual(scenario.witness_activity_id, critical_ids[0])
            self.assertEqual(
                scenario.witness_chain,
                _oracle_witness_chain(document, durations, critical_ids[0]),
            )

    def test_deadline_equality_is_robust_and_minimum_failure_is_rejected(self) -> None:
        exact = valid_document()
        exact["activities"] = [
            {
                "id": "exact",
                "release_offset_seconds": 2,
                "duration": {"minimum_seconds": 3, "maximum_seconds": 5},
                "hard_deadline_offset_seconds": 7,
            }
        ]
        exact["transfers"] = []
        report = analyze_interval_feasibility(
            decode_itinerary_json(encoded(exact))
        )
        self.assertEqual(report.status, FeasibilityStatus.ROBUST)
        self.assertEqual(report.worst_case.minimum_deadline_slack_seconds, 0)

        exact["activities"][0]["hard_deadline_offset_seconds"] = 4
        with self.assertRaises(ContractError) as raised:
            decode_itinerary_json(encoded(exact))
        self.assertEqual(raised.exception.code, ErrorCode.INFEASIBLE_DEADLINE)

    def test_input_permutations_have_identical_canonical_analysis(self) -> None:
        first = valid_document()
        second = json.loads(json.dumps(first))
        second["activities"].reverse()
        second["transfers"].reverse()

        first_report = analyze_interval_feasibility(
            decode_itinerary_json(encoded(first))
        )
        second_report = analyze_interval_feasibility(
            decode_itinerary_json(
                json.dumps(second, sort_keys=True, indent=2)
            )
        )
        self.assertEqual(
            canonical_analysis_bytes(first_report),
            canonical_analysis_bytes(second_report),
        )
        self.assertEqual(analysis_digest(first_report), analysis_digest(second_report))
        self.assertTrue(canonical_analysis_bytes(first_report).isascii())

        decoded = decode_itinerary_json(encoded(first))
        manually_permuted = Itinerary(
            schema_version=decoded.schema_version,
            itinerary_id=decoded.itinerary_id,
            anchor_time_utc=decoded.anchor_time_utc,
            horizon_seconds=decoded.horizon_seconds,
            activities=tuple(reversed(decoded.activities)),
            transfers=tuple(reversed(decoded.transfers)),
        )
        self.assertEqual(
            canonical_analysis_bytes(first_report),
            canonical_analysis_bytes(
                analyze_interval_feasibility(manually_permuted)
            ),
        )

    def test_declared_activity_and_transfer_bounds_are_analyzable(self) -> None:
        activities = [
            {
                "id": f"a{index:03d}",
                "release_offset_seconds": 0,
                "duration": {"minimum_seconds": 1, "maximum_seconds": 2},
                "hard_deadline_offset_seconds": 1000,
            }
            for index in range(MAX_ACTIVITIES)
        ]
        endpoint_pairs = [
            (source, target)
            for source in range(MAX_ACTIVITIES)
            for target in range(source + 1, MAX_ACTIVITIES)
        ][:MAX_TRANSFERS]
        document = {
            "schema_version": 1,
            "itinerary_id": "analysis_resource_boundary",
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
        report = analyze_interval_feasibility(
            decode_itinerary_json(encoded(document))
        )
        self.assertEqual(len(report.activities), MAX_ACTIVITIES)
        self.assertEqual(report.status, FeasibilityStatus.ROBUST)

    def test_analysis_digest_is_stable_across_hash_seeds(self) -> None:
        program = (
            "from pathlib import Path;"
            "from tctravel import decode_itinerary_json;"
            "from tctravel.analysis import analysis_digest,analyze_interval_feasibility;"
            "p=Path('examples/synthetic_tight_connection.v1.json');"
            "i=decode_itinerary_json(p.read_bytes());"
            "print(analysis_digest(analyze_interval_feasibility(i)))"
        )
        outputs = []
        for seed in ("0", "1", "7", "123", "4294967295"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [sys.executable, "-c", program],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            outputs.append(completed.stdout)
        self.assertEqual(len(set(outputs)), 1)

    def test_report_models_are_immutable(self) -> None:
        report = analyze_interval_feasibility(
            decode_itinerary_json(ROBUST_EXAMPLE.read_bytes())
        )
        with self.assertRaises(FrozenInstanceError):
            report.status = FeasibilityStatus.DURATION_SENSITIVE  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            report.activities[0].activity_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
