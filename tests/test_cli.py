from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import patch

from tctravel import MAX_ACTIVITIES, MAX_TRANSFERS, decode_itinerary_json
from tctravel.analysis import (
    analyze_interval_feasibility,
    canonical_analysis_bytes,
)
from tctravel.cli import MAX_ANALYSIS_OUTPUT_BYTES, CliExitCode, main
from tctravel.codec import MAX_INPUT_BYTES

ROOT = Path(__file__).resolve().parents[1]
ROBUST_EXAMPLE = ROOT / "examples" / "synthetic_connection.v1.json"
TIGHT_EXAMPLE = ROOT / "examples" / "synthetic_tight_connection.v1.json"


def _invoke(
    args: list[str], payload: bytes = b""
) -> tuple[int, bytes, bytes]:
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    status = main(
        args,
        stdin=io.BytesIO(payload),
        stdout=stdout,
        stderr=stderr,
    )
    return status, stdout.getvalue(), stderr.getvalue()


class CliTests(unittest.TestCase):
    def test_robust_stdin_is_canonical_and_returns_zero(self) -> None:
        payload = ROBUST_EXAMPLE.read_bytes()
        status, stdout, stderr = _invoke([], payload)
        expected = canonical_analysis_bytes(
            analyze_interval_feasibility(decode_itinerary_json(payload))
        ) + b"\n"

        self.assertEqual(status, CliExitCode.ROBUST)
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, b"")
        self.assertLessEqual(len(stdout), MAX_ANALYSIS_OUTPUT_BYTES)
        self.assertEqual(json.loads(stdout)["status"], "robust")

    def test_duration_sensitive_stdin_returns_one_with_full_report(self) -> None:
        payload = TIGHT_EXAMPLE.read_bytes()
        status, stdout, stderr = _invoke(["-"], payload)
        expected = canonical_analysis_bytes(
            analyze_interval_feasibility(decode_itinerary_json(payload))
        ) + b"\n"

        self.assertEqual(status, CliExitCode.DURATION_SENSITIVE)
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, b"")
        document = json.loads(stdout)
        self.assertEqual(document["status"], "duration_sensitive")
        self.assertEqual(
            document["worst_case"]["minimum_deadline_slack_seconds"],
            -120,
        )

    def test_regular_file_matches_stdin_and_module_contract(self) -> None:
        payload = ROBUST_EXAMPLE.read_bytes()
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "fixture.json"
            path.write_bytes(payload)
            from_file = _invoke([str(path)])
        from_stdin = _invoke(["-"], payload)

        self.assertEqual(from_file, from_stdin)

    def test_help_is_static_and_invalid_arguments_are_redacted(self) -> None:
        status, stdout, stderr = _invoke(["--help"])
        self.assertEqual(status, CliExitCode.ROBUST)
        self.assertEqual(
            stdout,
            b"usage: tctravel-analyze [INPUT|-]\n"
            b"Analyze a contract-v1 itinerary; omitted INPUT and '-' read stdin.\n",
        )
        self.assertEqual(stderr, b"")

        sentinel = "private-token-or-path"
        status, stdout, stderr = _invoke(["--unknown", sentinel])
        self.assertEqual(status, CliExitCode.INVALID_INVOCATION)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            json.loads(stderr), {"error": {"code": "invalid_invocation"}}
        )
        self.assertNotIn(sentinel.encode(), stderr)

    def test_contract_errors_are_canonical_and_do_not_echo_input(self) -> None:
        sentinel = b"private-sentinel"
        status, stdout, stderr = _invoke(
            ["-"], b'{"unknown":"' + sentinel + b'"}'
        )

        self.assertEqual(status, CliExitCode.CONTRACT_REJECTED)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            stderr, b'{"error":{"code":"unknown_field"}}\n'
        )
        self.assertNotIn(sentinel, stderr)

    def test_oversized_stdin_uses_the_public_contract_error(self) -> None:
        status, stdout, stderr = _invoke(
            ["-"], b" " * (MAX_INPUT_BYTES + 1)
        )

        self.assertEqual(status, CliExitCode.CONTRACT_REJECTED)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            stderr, b'{"error":{"code":"input_too_large"}}\n'
        )

    def test_maximum_shaped_contract_stays_within_output_cap(self) -> None:
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
        payload = json.dumps(
            {
                "schema_version": 1,
                "itinerary_id": "cli_output_boundary",
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
            },
            separators=(",", ":"),
        ).encode()
        self.assertLessEqual(len(payload), MAX_INPUT_BYTES)

        status, stdout, stderr = _invoke(["-"], payload)
        self.assertEqual(status, CliExitCode.ROBUST)
        self.assertEqual(stderr, b"")
        self.assertLessEqual(len(stdout), MAX_ANALYSIS_OUTPUT_BYTES)
        self.assertEqual(len(json.loads(stdout)["activities"]), MAX_ACTIVITIES)

    def test_missing_symlink_and_fifo_paths_fail_without_path_echo(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            missing = directory / "private-missing-name"
            target = directory / "target.json"
            target.write_bytes(ROBUST_EXAMPLE.read_bytes())
            symlink = directory / "private-symlink-name"
            symlink.symlink_to(target)
            paths = [missing, symlink]
            if hasattr(os, "mkfifo"):
                fifo = directory / "private-fifo-name"
                os.mkfifo(fifo)
                paths.append(fifo)

            for path in paths:
                with self.subTest(kind=path.name):
                    status, stdout, stderr = _invoke([str(path)])
                    self.assertEqual(status, CliExitCode.INPUT_UNAVAILABLE)
                    self.assertEqual(stdout, b"")
                    self.assertEqual(
                        stderr,
                        b'{"error":{"code":"input_unavailable"}}\n',
                    )
                    self.assertNotIn(str(path).encode(), stderr)

    def test_oversized_regular_file_is_contract_rejected_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "oversized.json"
            path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
            status, stdout, stderr = _invoke([str(path)])

        self.assertEqual(status, CliExitCode.CONTRACT_REJECTED)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            stderr, b'{"error":{"code":"input_too_large"}}\n'
        )

    def test_growth_between_lstat_and_fstat_is_contract_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "growing.json"
            path.write_bytes(b"{}")
            real_open = os.open

            def grow_then_open(name: str, flags: int) -> int:
                path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
                return real_open(name, flags)

            with patch("tctravel.cli.os.open", side_effect=grow_then_open):
                status, stdout, stderr = _invoke([str(path)])

        self.assertEqual(status, CliExitCode.CONTRACT_REJECTED)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            stderr, b'{"error":{"code":"input_too_large"}}\n'
        )

    def test_in_place_mutation_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "mutating.json"
            path.write_bytes(ROBUST_EXAMPLE.read_bytes())

            from tctravel.cli import _read_stream_bounded

            def mutate_after_read(stream: io.BufferedReader) -> bytes:
                payload = _read_stream_bounded(stream)
                path.write_bytes(b" " * len(payload))
                return payload

            with patch(
                "tctravel.cli._read_stream_bounded",
                side_effect=mutate_after_read,
            ):
                status, stdout, stderr = _invoke([str(path)])

        self.assertEqual(status, CliExitCode.INPUT_UNAVAILABLE)
        self.assertEqual(stdout, b"")
        self.assertEqual(
            stderr, b'{"error":{"code":"input_unavailable"}}\n'
        )

    def test_stdin_read_failure_is_reported_as_input_unavailable(self) -> None:
        class BrokenInput(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                raise OSError("private stream detail")

        stdout = io.BytesIO()
        stderr = io.BytesIO()
        status = main(
            ["-"],
            stdin=BrokenInput(),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(status, CliExitCode.INPUT_UNAVAILABLE)
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(
            stderr.getvalue(),
            b'{"error":{"code":"input_unavailable"}}\n',
        )
        self.assertNotIn(b"private stream detail", stderr.getvalue())

    def test_internal_failure_is_redacted(self) -> None:
        sentinel = "private-internal-detail"
        with patch(
            "tctravel.cli.analyze_interval_feasibility",
            side_effect=RuntimeError(sentinel),
        ):
            status, stdout, stderr = _invoke(
                ["-"], ROBUST_EXAMPLE.read_bytes()
            )

        self.assertEqual(status, CliExitCode.INTERNAL_ERROR)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b'{"error":{"code":"internal_error"}}\n')
        self.assertNotIn(sentinel.encode(), stderr)

    def test_broken_output_streams_never_escape_private_exceptions(self) -> None:
        class BrokenOutput(io.BytesIO):
            def write(self, data: bytes) -> int:
                raise OSError("private/output/path")

        class NoneOutput:
            def write(self, data: bytes) -> None:
                return None

            def flush(self) -> None:
                return None

        class PartialOutput:
            def write(self, data: bytes) -> int:
                return max(0, len(data) - 1)

            def flush(self) -> None:
                return None

        stderr = io.BytesIO()
        status = main(
            ["--help"],
            stdin=io.BytesIO(),
            stdout=BrokenOutput(),
            stderr=stderr,
        )
        self.assertEqual(status, CliExitCode.INTERNAL_ERROR)
        self.assertEqual(
            stderr.getvalue(),
            b'{"error":{"code":"internal_error"}}\n',
        )
        self.assertNotIn(b"private/output/path", stderr.getvalue())

        for output in (NoneOutput(), PartialOutput()):
            with self.subTest(writer=type(output).__name__):
                stderr = io.BytesIO()
                status = main(
                    ["--help"],
                    stdin=io.BytesIO(),
                    stdout=cast(BinaryIO, output),
                    stderr=stderr,
                )
                self.assertEqual(status, CliExitCode.INTERNAL_ERROR)
                self.assertEqual(
                    stderr.getvalue(),
                    b'{"error":{"code":"internal_error"}}\n',
                )

        status = main(
            ["--invalid"],
            stdin=io.BytesIO(),
            stdout=io.BytesIO(),
            stderr=BrokenOutput(),
        )
        self.assertEqual(status, CliExitCode.INTERNAL_ERROR)

        stderr = io.BytesIO()
        status = main(
            ["-"],
            stdin=io.BytesIO(ROBUST_EXAMPLE.read_bytes()),
            stdout=BrokenOutput(),
            stderr=stderr,
        )
        self.assertEqual(status, CliExitCode.INTERNAL_ERROR)
        self.assertEqual(
            stderr.getvalue(),
            b'{"error":{"code":"internal_error"}}\n',
        )


if __name__ == "__main__":
    unittest.main()
