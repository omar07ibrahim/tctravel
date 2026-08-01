from __future__ import annotations

# pyright: reportPrivateUsage=false
import ast
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, cast
from unittest import mock

from PIL import Image

from tools import generate_analysis_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / evidence.MANIFEST_OUTPUT
EXPECTED_BUNDLE_PATHS = {
    evidence.RAW_ROBUST_STDOUT,
    evidence.RAW_ROBUST_STDERR,
    evidence.RAW_TIGHT_STDOUT,
    evidence.RAW_TIGHT_STDERR,
    evidence.RUNS_OUTPUT,
    evidence.TERMINAL_SVG_OUTPUT,
    evidence.TERMINAL_PNG_OUTPUT,
    evidence.ENVELOPE_OUTPUT,
    evidence.SLACK_OUTPUT,
    evidence.CHAIN_OUTPUT,
    evidence.WORKFLOW_OUTPUT,
    evidence.DECISION_OUTPUT,
    evidence.DEMO_OUTPUT,
    evidence.MANIFEST_OUTPUT,
}
SVG_PATHS = {
    evidence.TERMINAL_SVG_OUTPUT,
    evidence.ENVELOPE_OUTPUT,
    evidence.SLACK_OUTPUT,
    evidence.CHAIN_OUTPUT,
    evidence.WORKFLOW_OUTPUT,
    evidence.DECISION_OUTPUT,
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode(errors="replace"))
    return completed.stdout


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _load_report(path: str) -> dict[str, Any]:
    payload = (ROOT / path).read_bytes()
    document = json.loads(payload)
    canonical = (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    if payload != canonical:
        raise AssertionError(f"non-canonical report: {path}")
    return cast(dict[str, Any], document)


def _requirements_lock() -> dict[str, tuple[str, str]]:
    logical_lines: list[str] = []
    pending: list[str] = []
    for raw_line in (
        (ROOT / "requirements-evidence.txt").read_text(encoding="ascii").splitlines()
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        pending.append(line[:-1].rstrip() if continued else line)
        if continued:
            continue
        logical_lines.append(" ".join(pending))
        pending.clear()
    if pending:
        raise AssertionError("unterminated requirements continuation")

    records: dict[str, tuple[str, str]] = {}
    for line in logical_lines:
        tokens = line.split()
        name, version = tokens[0].split("==", 1)
        hashes = [
            token.removeprefix("--hash=sha256:")
            for token in tokens[1:]
            if token.startswith("--hash=sha256:")
        ]
        if len(hashes) != 1 or len(hashes[0]) != 64:
            raise AssertionError(f"invalid locked hash for {name}")
        records[name] = (version, hashes[0])
    return records


def _write_contract_wheel(
    path: Path,
    *,
    source_replacement: tuple[str, bytes] | None = None,
    metadata: bytes = evidence.EXPECTED_WHEEL_METADATA,
    entry_points: bytes = evidence.EXPECTED_WHEEL_ENTRY_POINTS,
    extra_members: tuple[tuple[str, bytes], ...] = (),
    record_replacement: bytes | None = None,
) -> None:
    package_paths = sorted(
        item for item in evidence.SUBJECT_SOURCE_PATHS if item.startswith("tctravel/")
    )
    payloads: dict[str, bytes] = {}
    for package_path in package_paths:
        payload = _git(
            "show",
            f"{evidence.DEFAULT_SUBJECT_REVISION}:{package_path}",
        )
        if source_replacement is not None and package_path == source_replacement[0]:
            payload = source_replacement[1]
        payloads[package_path] = payload
    dist_info = evidence.EXPECTED_DIST_INFO_ROOT
    payloads[f"{dist_info}/METADATA"] = metadata
    payloads[f"{dist_info}/WHEEL"] = evidence.EXPECTED_WHEEL_DESCRIPTOR
    payloads[f"{dist_info}/entry_points.txt"] = entry_points
    payloads[f"{dist_info}/top_level.txt"] = evidence.EXPECTED_WHEEL_TOP_LEVEL
    payloads.update(extra_members)
    record_member = f"{dist_info}/RECORD"
    record = (
        "".join(
            f"{member},{evidence._wheel_record_hash(payload)},{len(payload)}\n"
            for member, payload in payloads.items()
        )
        + f"{record_member},,\n"
    )
    payloads[record_member] = (
        record.encode("ascii") if record_replacement is None else record_replacement
    )
    with zipfile.ZipFile(path, "w") as archive:
        for member, payload in payloads.items():
            archive.writestr(member, payload)


def _integer_expression(node: ast.expr) -> int:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _integer_expression(node.left) * _integer_expression(node.right)
    raise AssertionError("pinned runtime limit is not a bounded integer expression")


def _pinned_integer_constants(path: str) -> dict[str, int]:
    payload = _git("show", f"{evidence.DEFAULT_SUBJECT_REVISION}:{path}").decode(
        "utf-8"
    )
    result: dict[str, int] = {}
    for statement in ast.parse(payload).body:
        if not isinstance(statement, ast.AnnAssign):
            continue
        if isinstance(statement.target, ast.Name) and statement.value is not None:
            try:
                result[statement.target.id] = _integer_expression(statement.value)
            except AssertionError:
                continue
    return result


@unittest.skipUnless(MANIFEST.is_file(), "published in the generated-only slice")
class PublishedAnalysisEvidenceTests(unittest.TestCase):
    def test_check_command_is_read_only_and_leaves_no_residue(self) -> None:
        before_status = _git("status", "--porcelain=v1", "--untracked-files=all")
        before = {
            path: _sha256((ROOT / path).read_bytes()) for path in EXPECTED_BUNDLE_PATHS
        }
        completed = subprocess.run(
            [sys.executable, "tools/generate_analysis_evidence.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "TCTravel analysis evidence: PASS "
            "(verified 8 visuals + raw runs + manifest)\n",
        )
        self.assertEqual(
            before,
            {
                path: _sha256((ROOT / path).read_bytes())
                for path in EXPECTED_BUNDLE_PATHS
            },
        )
        self.assertEqual(
            before_status,
            _git("status", "--porcelain=v1", "--untracked-files=all"),
        )
        work = ROOT / ".evidence-work"
        self.assertFalse(work.exists() and any(work.iterdir()))

    def test_manifest_binds_exact_subject_pipeline_runs_and_artifacts(self) -> None:
        manifest = json.loads(MANIFEST.read_bytes())
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["check_command"], evidence.CHECK_COMMAND)
        self.assertEqual(manifest["write_command"], evidence.WRITE_COMMAND)
        subject = manifest["subject"]
        self.assertEqual(subject["revision"], evidence.DEFAULT_SUBJECT_REVISION)
        self.assertEqual(subject["tree"], evidence.DEFAULT_SUBJECT_TREE)
        self.assertEqual(
            subject["tree"],
            _git("rev-parse", f"{subject['revision']}^{{tree}}").decode().strip(),
        )
        self.assertEqual(
            [record["path"] for record in subject["sources"]],
            list(evidence.SUBJECT_SOURCE_PATHS),
        )
        for record in subject["sources"]:
            payload = _git("show", f"{subject['revision']}:{record['path']}")
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], _sha256(payload))
            self.assertEqual(
                record["blob_oid"],
                _git(
                    "rev-parse",
                    f"{subject['revision']}:{record['path']}",
                )
                .decode()
                .strip(),
            )

        pipeline = manifest["pipeline"]
        self.assertEqual(
            pipeline["dependency_versions"],
            evidence.EXPECTED_PIPELINE_DISTRIBUTIONS,
        )
        self.assertEqual(
            manifest["renderer_contract"], evidence.EXPECTED_RENDERER_CONTRACT
        )
        self.assertEqual(
            [record["path"] for record in pipeline["sources"]],
            list(evidence.PIPELINE_SOURCE_PATHS),
        )
        for record in pipeline["sources"]:
            payload = (ROOT / record["path"]).read_bytes()
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], _sha256(payload))

        artifact_records = {record["path"]: record for record in manifest["artifacts"]}
        self.assertEqual(
            set(artifact_records), EXPECTED_BUNDLE_PATHS - {evidence.MANIFEST_OUTPUT}
        )
        for path, record in artifact_records.items():
            payload = (ROOT / path).read_bytes()
            self.assertEqual(record["bytes"], len(payload))
            self.assertEqual(record["sha256"], _sha256(payload))
            self.assertTrue(record["derived_from"])
            self.assertTrue(record["claim"])

        self.assertEqual(evidence._bundle_files(ROOT), EXPECTED_BUNDLE_PATHS)
        self.assertEqual(
            manifest["install_contract"],
            {
                "console_script_present": True,
                "import_within_fresh_venv": True,
                "module_entry_point_parity": True,
                "package_index_access": False,
                "pip_present_in_fresh_venv": False,
                "site_packages_within_fresh_venv": True,
                "system_site_packages": False,
                "user_site_enabled": False,
                "venv_prefix_distinct": True,
            },
        )

    def test_raw_cli_bytes_and_domain_outcomes_are_exact(self) -> None:
        robust_payload = (ROOT / evidence.RAW_ROBUST_STDOUT).read_bytes()
        tight_payload = (ROOT / evidence.RAW_TIGHT_STDOUT).read_bytes()
        robust = _load_report(evidence.RAW_ROBUST_STDOUT)
        tight = _load_report(evidence.RAW_TIGHT_STDOUT)
        self.assertEqual(robust["status"], "robust")
        self.assertEqual(robust["worst_case"]["minimum_deadline_slack_seconds"], 900)
        self.assertEqual(tight["status"], "duration_sensitive")
        self.assertEqual(tight["worst_case"]["minimum_deadline_slack_seconds"], -120)
        self.assertEqual(
            tight["worst_case"]["witness_chain"],
            ["local_shuttle", "security_check"],
        )
        self.assertEqual(
            _sha256(robust_payload[:-1]),
            "7ddafbf24ec6300e127eb15db691d13676bbf9c83e69efc34e75695841b840a2",
        )
        self.assertEqual(
            _sha256(tight_payload[:-1]),
            "35dfcc56368c12f3a8d4e12a4e715dd1f4fc9cef30a4613c736cdb412bfc0c2e",
        )
        self.assertEqual((ROOT / evidence.RAW_ROBUST_STDERR).read_bytes(), b"")
        self.assertEqual((ROOT / evidence.RAW_TIGHT_STDERR).read_bytes(), b"")

        receipts = json.loads((ROOT / evidence.RUNS_OUTPUT).read_bytes())
        runs = {run["case_id"]: run for run in receipts["runs"]}
        self.assertEqual(runs["robust_fixture"]["exit_code"], 0)
        self.assertEqual(runs["duration_sensitive_fixture"]["exit_code"], 1)
        self.assertEqual(runs["exact_deadline_equality"]["exit_code"], 0)
        self.assertEqual(
            runs["exact_deadline_equality"]["observation"]["status"],
            "robust",
        )
        self.assertEqual(runs["minimum_bound_rejection"]["exit_code"], 3)
        self.assertEqual(
            runs["minimum_bound_rejection"]["observation"]["error_code"],
            "infeasible_deadline",
        )
        self.assertEqual(
            runs["reordered_input_invariance"]["stdout_sha256"],
            runs["robust_fixture"]["stdout_sha256"],
        )

    def test_terminal_svg_reconstructs_every_captured_stdout_byte(self) -> None:
        root = ET.fromstring((ROOT / evidence.TERMINAL_SVG_OUTPUT).read_bytes())
        tight_stdout = (ROOT / evidence.RAW_TIGHT_STDOUT).read_bytes()
        chunks = sorted(
            (
                int(element.attrib["data-byte-start"]),
                int(element.attrib["data-byte-end"]),
                element.text or "",
            )
            for element in root.iter()
            if "data-byte-start" in element.attrib
        )
        cursor = 0
        reconstructed: list[str] = []
        for start, end, text in chunks:
            self.assertEqual(start, cursor)
            self.assertEqual(end - start, len(text.encode("ascii")))
            reconstructed.append(text)
            cursor = end
        self.assertEqual("".join(reconstructed).encode("ascii"), tight_stdout[:-1])
        self.assertEqual(cursor, len(tight_stdout) - 1)
        self.assertEqual(root.attrib["data-exit-code"], "1")
        self.assertEqual(root.attrib["data-stdout-sha256"], _sha256(tight_stdout))

    def test_svg_values_match_raw_reports_and_are_accessible(self) -> None:
        robust = _load_report(evidence.RAW_ROBUST_STDOUT)
        tight = _load_report(evidence.RAW_TIGHT_STDOUT)
        reports = {"0": robust, "1": tight}
        forbidden_tags = {"a", "foreignObject", "image", "script"}
        for path in SVG_PATHS:
            with self.subTest(path=path):
                root = ET.fromstring((ROOT / path).read_bytes())
                self.assertEqual(root.attrib["viewBox"], "0 0 1800 1120")
                self.assertEqual(root.attrib["role"], "img")
                names = {_local_name(element.tag) for element in root.iter()}
                self.assertTrue({"title", "desc"}.issubset(names))
                self.assertTrue(forbidden_tags.isdisjoint(names))
                for element in root.iter():
                    self.assertFalse(
                        any(key.endswith("href") for key in element.attrib)
                    )

        envelope = ET.fromstring((ROOT / evidence.ENVELOPE_OUTPUT).read_bytes())
        self.assertGreaterEqual(
            int(envelope.attrib["data-axis-end-x"])
            - int(envelope.attrib["data-value-label-x"]),
            32,
        )
        for panel in envelope.iter():
            if "data-panel" not in panel.attrib:
                continue
            report = reports[panel.attrib["data-panel"]]
            activities = {
                activity["activity_id"]: activity
                for activity in cast(list[dict[str, Any]], report["activities"])
            }
            for group in panel.iter():
                activity_id = group.attrib.get("data-activity-id")
                if activity_id is None:
                    continue
                activity = activities[activity_id]
                self.assertEqual(
                    int(group.attrib["data-best-start"]),
                    activity["best_case"]["earliest_start_offset_seconds"],
                )
                self.assertEqual(
                    int(group.attrib["data-best-finish"]),
                    activity["best_case"]["earliest_finish_offset_seconds"],
                )
                self.assertEqual(
                    int(group.attrib["data-worst-start"]),
                    activity["worst_case"]["earliest_start_offset_seconds"],
                )
                self.assertEqual(
                    int(group.attrib["data-worst-finish"]),
                    activity["worst_case"]["earliest_finish_offset_seconds"],
                )
                self.assertEqual(
                    int(group.attrib["data-deadline"]),
                    activity["hard_deadline_offset_seconds"],
                )

        slack = ET.fromstring((ROOT / evidence.SLACK_OUTPUT).read_bytes())
        self.assertLess(int(slack.attrib["data-scale-min"]), 0)
        self.assertGreater(int(slack.attrib["data-scale-max"]), 0)
        self.assertGreaterEqual(
            int(slack.attrib["data-value-label-x"])
            - int(slack.attrib["data-axis-end-x"]),
            250,
        )
        slack_groups = [
            element for element in slack.iter() if "data-best-slack" in element.attrib
        ]
        self.assertEqual(len(slack_groups), 6)
        self.assertIn(
            -120,
            [int(group.attrib["data-worst-slack"]) for group in slack_groups],
        )

        chain = ET.fromstring((ROOT / evidence.CHAIN_OUTPUT).read_bytes())
        self.assertEqual(chain.attrib["data-chain"], "local_shuttle->security_check")
        self.assertEqual(chain.attrib["data-finish"], "1620")
        self.assertEqual(chain.attrib["data-deadline"], "1500")
        self.assertEqual(chain.attrib["data-slack"], "-120")
        self.assertIn(
            "0 + 900 + 120 + 600 = 1620",
            "".join(element.text or "" for element in chain.iter()),
        )

        workflow = ET.fromstring((ROOT / evidence.WORKFLOW_OUTPUT).read_bytes())
        source_records = [
            record
            for record in json.loads(MANIFEST.read_bytes())["subject"]["sources"]
            if record["path"] in evidence.WORKFLOW_SOURCE_PATHS
        ]
        self.assertEqual(
            [record["path"] for record in source_records],
            list(evidence.WORKFLOW_SOURCE_PATHS),
        )
        canonical_sources = (
            json.dumps(
                source_records,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        self.assertEqual(
            workflow.attrib["data-source-records-sha256"],
            _sha256(canonical_sources),
        )
        bindings = {
            element.attrib["data-source-path"]: element.attrib
            for element in workflow.iter()
            if "data-source-path" in element.attrib
        }
        self.assertEqual(set(bindings), set(evidence.WORKFLOW_SOURCE_PATHS))
        for record in source_records:
            binding = bindings[record["path"]]
            self.assertEqual(binding["data-blob-oid"], record["blob_oid"])
            self.assertEqual(binding["data-source-sha256"], record["sha256"])
        source_payloads = {
            path: _git("show", f"{evidence.DEFAULT_SUBJECT_REVISION}:{path}")
            for path in evidence.WORKFLOW_SOURCE_PATHS
        }
        symbol_contract = evidence._workflow_semantic_contract(source_payloads)
        canonical_symbols = (
            json.dumps(
                symbol_contract,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        self.assertEqual(
            workflow.attrib["data-symbol-contract-sha256"],
            _sha256(canonical_symbols),
        )
        symbol_bindings = {
            element.attrib["data-display-symbol"]: element.attrib
            for element in workflow.iter()
            if "data-display-symbol" in element.attrib
        }
        self.assertEqual(
            set(symbol_bindings),
            {
                "decode_itinerary_json",
                "compile_temporal_graph",
                "_analyze_scenario",
                "canonical_analysis_bytes",
            },
        )
        self.assertEqual(symbol_bindings["_analyze_scenario"]["data-call-count"], "2")
        self.assertEqual(
            symbol_bindings["decode_itinerary_json"]["data-call-count"], "1"
        )
        self.assertEqual(
            symbol_bindings["compile_temporal_graph"]["data-call-count"], "1"
        )
        self.assertEqual(
            symbol_bindings["canonical_analysis_bytes"]["data-call-count"],
            "1",
        )

    def test_presentation_models_are_exact_views_of_captured_runs(self) -> None:
        manifest = json.loads(MANIFEST.read_bytes())
        models = manifest["presentation_models"]
        receipts = {run["case_id"]: run for run in manifest["evidence"]["runs"]}

        terminal = models["terminal"]
        tight = receipts["duration_sensitive_fixture"]
        tight_stdout = (ROOT / evidence.RAW_TIGHT_STDOUT).read_bytes()
        self.assertEqual(terminal["case_id"], tight["case_id"])
        self.assertEqual(terminal["command"], tight["command"])
        self.assertEqual(terminal["exit_code"], tight["exit_code"])
        self.assertEqual(terminal["status"], tight["observation"]["status"])
        self.assertEqual(terminal["stdout_bytes"], len(tight_stdout))
        self.assertEqual(terminal["stdout_sha256"], _sha256(tight_stdout))
        self.assertEqual(terminal["stderr_bytes"], tight["stderr_bytes"])
        cursor = 0
        reconstructed: list[str] = []
        for chunk in terminal["chunks"]:
            self.assertEqual(chunk["byte_start"], cursor)
            self.assertEqual(
                chunk["byte_end"] - chunk["byte_start"],
                len(chunk["text"].encode("ascii")),
            )
            reconstructed.append(chunk["text"])
            cursor = chunk["byte_end"]
        self.assertEqual(("".join(reconstructed) + "\n").encode("ascii"), tight_stdout)

        demo = models["demo"]
        self.assertEqual(demo["timing_semantics"], "illustrative")
        self.assertEqual(demo["loop"], 0)
        for run_model in demo["runs"]:
            receipt = receipts[run_model["case_id"]]
            observation = receipt["observation"]
            self.assertEqual(run_model["command"], receipt["command"])
            self.assertEqual(run_model["exit_code"], receipt["exit_code"])
            self.assertEqual(run_model["status"], observation["status"])
            self.assertEqual(
                run_model["worst_slack_seconds"],
                observation["worst_case_minimum_deadline_slack_seconds"],
            )
            self.assertEqual(
                run_model["witness_chain"],
                observation["worst_case_witness_chain"],
            )
            self.assertEqual(run_model["stdout_bytes"], receipt["stdout_bytes"])
            self.assertEqual(run_model["stdout_sha256"], receipt["stdout_sha256"])
        self.assertEqual(
            [frame["case_id"] for frame in demo["frames"]],
            [
                "robust_fixture",
                "robust_fixture",
                "duration_sensitive_fixture",
                "duration_sensitive_fixture",
            ],
        )
        self.assertEqual(
            [frame["phase"] for frame in demo["frames"]],
            ["run", "observe", "run", "observe"],
        )

        terminal_presentation = evidence.TerminalPresentation(
            case_id=terminal["case_id"],
            command=tuple(terminal["command"]),
            exit_code=terminal["exit_code"],
            status=terminal["status"],
            stderr_bytes=terminal["stderr_bytes"],
            stdout=tight_stdout,
            chunks=tuple(
                evidence.TerminalChunk(
                    byte_start=chunk["byte_start"],
                    byte_end=chunk["byte_end"],
                    text=chunk["text"],
                )
                for chunk in terminal["chunks"]
            ),
        )
        self.assertEqual(
            evidence._terminal_presentation_record(terminal_presentation),
            terminal,
        )
        self.assertEqual(
            evidence._terminal_png(terminal_presentation),
            (ROOT / evidence.TERMINAL_PNG_OUTPUT).read_bytes(),
        )

        demo_presentation = evidence.DemoPresentation(
            runs=tuple(
                evidence.DemoRunPresentation(
                    case_id=run["case_id"],
                    command=tuple(run["command"]),
                    exit_code=run["exit_code"],
                    status=run["status"],
                    worst_slack_seconds=run["worst_slack_seconds"],
                    witness_chain=tuple(run["witness_chain"]),
                    stdout_bytes=run["stdout_bytes"],
                    stdout_sha256=run["stdout_sha256"],
                )
                for run in demo["runs"]
            ),
            frames=tuple(
                evidence.DemoFramePresentation(
                    case_id=frame["case_id"],
                    phase=frame["phase"],
                    step=frame["step"],
                    heading=frame["heading"],
                    accent=frame["accent"],
                    duration_ms=frame["duration_ms"],
                )
                for frame in demo["frames"]
            ),
            loop=demo["loop"],
            timing_semantics=demo["timing_semantics"],
        )
        self.assertEqual(evidence._demo_presentation_record(demo_presentation), demo)
        self.assertEqual(
            evidence._demo_gif(demo_presentation),
            (ROOT / evidence.DEMO_OUTPUT).read_bytes(),
        )

    def test_raster_capture_and_demo_have_fixed_semantics(self) -> None:
        manifest = json.loads(MANIFEST.read_bytes())
        artifacts = {record["path"]: record for record in manifest["artifacts"]}
        with Image.open(ROOT / evidence.TERMINAL_PNG_OUTPUT) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1800, 1120))
            self.assertEqual(image.mode, "RGB")
            decoded_digest = _sha256(image.convert("RGB").tobytes())
            self.assertEqual(
                decoded_digest,
                artifacts[evidence.TERMINAL_PNG_OUTPUT]["presentation"][
                    "decoded_rgb_sha256"
                ],
            )
            self.assertEqual(
                decoded_digest, evidence.EXPECTED_TERMINAL_DECODED_RGB_SHA256
            )
        with Image.open(ROOT / evidence.DEMO_OUTPUT) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.size, (1800, 1120))
            frame_count = cast(int, getattr(image, "n_frames", 1))
            self.assertEqual(frame_count, 4)
            durations: list[int] = []
            frame_digests: list[str] = []
            for frame_index in range(frame_count):
                image.seek(frame_index)
                durations.append(cast(int, image.info["duration"]))
                frame_digests.append(_sha256(image.convert("RGB").tobytes()))
            self.assertEqual(durations, [1200, 2200, 1200, 2800])
            self.assertEqual(image.info["loop"], 0)
            self.assertEqual(
                frame_digests,
                artifacts[evidence.DEMO_OUTPUT]["presentation"][
                    "decoded_rgb_frame_sha256"
                ],
            )
            self.assertEqual(
                frame_digests,
                list(evidence.EXPECTED_DEMO_DECODED_RGB_FRAME_SHA256),
            )

    def test_bundle_contains_no_environment_or_secret_surface(self) -> None:
        forbidden = (
            b"/home/",
            b"/Users/",
            b"ubuntu",
            b"AWS_SECRET",
            b"PRIVATE KEY",
            b"api_key",
            b"access_token",
        )
        for path in EXPECTED_BUNDLE_PATHS:
            with self.subTest(path=path):
                payload = (ROOT / path).read_bytes()
                self.assertFalse(any(token in payload for token in forbidden))


class AnalysisEvidenceSourceTests(unittest.TestCase):
    def test_subprocess_environment_is_an_allowlist_not_host_inheritance(
        self,
    ) -> None:
        inherited = {
            "AWS_SECRET_ACCESS_KEY": "must-not-cross-boundary",
            "GIT_CONFIG_GLOBAL": "/host/config",
            "HTTP_PROXY": "http://credential@proxy.invalid",
            "PIP_INDEX_URL": "https://credential@index.invalid/simple",
            "PYTHONPATH": "/host/injection",
        }
        code = (
            "import json,os;"
            "print(json.dumps(dict(os.environ),sort_keys=True,separators=(',',':')))"
        )
        with mock.patch.dict(os.environ, inherited):
            completed = evidence._run(
                (sys.executable, "-c", code), cwd=ROOT, timeout=20
            )
            clean = evidence._clean_environment(Path("/tmp/evidence"), "123")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            evidence._minimal_subprocess_environment(),
        )
        self.assertTrue(
            {
                "AWS_SECRET_ACCESS_KEY",
                "HTTP_PROXY",
                "PIP_INDEX_URL",
                "PYTHONPATH",
            }.isdisjoint(clean)
        )
        self.assertEqual(clean["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(clean["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(clean["PIP_NO_INDEX"], "1")
        self.assertEqual(clean["PATH"], evidence.MINIMAL_SUBPROCESS_PATH)

    def test_fresh_venv_contract_is_pipless_and_outer_pip_targeted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            for name in (
                "home",
                "pip-cache",
                "tmp",
                "xdg-cache",
                "xdg-config",
            ):
                (workspace / name).mkdir()
            installed = workspace / "installed"
            outside = workspace / "outside"
            outside.mkdir()
            environment = evidence._clean_environment(workspace, "123")
            creation = evidence._fresh_venv_command(installed)
            self.assertIn("--without-pip", creation)
            created = evidence._run(
                creation,
                cwd=outside,
                env=environment,
                timeout=60,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            installed_python = installed / "bin" / "python"
            evidence._require_pipless_fresh_venv(
                installed_python, cwd=outside, env=environment
            )
            install = evidence._outer_pip_install_command(
                installed_python, workspace / "subject.whl"
            )
            self.assertEqual(install[:4], (sys.executable, "-m", "pip", "--python"))
            self.assertEqual(install[4], str(installed_python))
            self.assertIn("--no-index", install)
            self.assertIn("--no-deps", install)
            self.assertNotEqual(install[0], str(installed_python))

    def test_wheel_contract_binds_package_bytes_and_strict_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw) / "subject.whl"
            _write_contract_wheel(wheel)
            contract = evidence._wheel_contract(
                wheel,
                root=ROOT,
                revision=evidence.DEFAULT_SUBJECT_REVISION,
            )
            self.assertEqual(contract["distribution"], "tctravel-temporal-compiler")
            self.assertEqual(
                contract["metadata_sha256"],
                _sha256(evidence.EXPECTED_WHEEL_METADATA),
            )
            package_files = cast(list[dict[str, object]], contract["package_files"])
            self.assertEqual(
                [record["path"] for record in package_files],
                sorted(
                    path
                    for path in evidence.SUBJECT_SOURCE_PATHS
                    if path.startswith("tctravel/")
                ),
            )
            wheel_files = cast(list[dict[str, object]], contract["wheel_files"])
            self.assertEqual(len(wheel_files), len(package_files) + 5)
            self.assertEqual(
                {record["path"] for record in wheel_files},
                {
                    *(record["path"] for record in package_files),
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/METADATA",
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/WHEEL",
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/entry_points.txt",
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/top_level.txt",
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/RECORD",
                },
            )

            _write_contract_wheel(
                wheel,
                source_replacement=("tctravel/analysis.py", b"altered\n"),
            )
            with self.assertRaisesRegex(
                evidence.EvidenceFailure, "wheel_package_payload_drift"
            ):
                evidence._wheel_contract(
                    wheel,
                    root=ROOT,
                    revision=evidence.DEFAULT_SUBJECT_REVISION,
                )

    def test_wheel_contract_rejects_every_runtime_affecting_extra_member(
        self,
    ) -> None:
        extras = (
            ("attacker.pth", b"import attacker\n"),
            (
                "tctravel/analysis.cpython-312-x86_64-linux-gnu.so",
                b"native-shadow",
            ),
        )
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw) / "subject.whl"
            for member, payload in extras:
                with self.subTest(member=member):
                    _write_contract_wheel(
                        wheel,
                        extra_members=((member, payload),),
                    )
                    with self.assertRaisesRegex(
                        evidence.EvidenceFailure, "wheel_member_surface_drift"
                    ):
                        evidence._wheel_contract(
                            wheel,
                            root=ROOT,
                            revision=evidence.DEFAULT_SUBJECT_REVISION,
                        )

    def test_wheel_record_binds_every_installed_member_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw) / "subject.whl"
            _write_contract_wheel(
                wheel,
                record_replacement=(
                    f"{evidence.EXPECTED_DIST_INFO_ROOT}/RECORD,,\n"
                ).encode("ascii"),
            )
            with self.assertRaisesRegex(
                evidence.EvidenceFailure, "wheel_record_surface_drift"
            ):
                evidence._wheel_contract(
                    wheel,
                    root=ROOT,
                    revision=evidence.DEFAULT_SUBJECT_REVISION,
                )

            _write_contract_wheel(wheel)
            with zipfile.ZipFile(wheel) as archive:
                payloads = {
                    member: archive.read(member) for member in archive.namelist()
                }
            record_member = f"{evidence.EXPECTED_DIST_INFO_ROOT}/RECORD"
            payloads[record_member] = payloads[record_member].replace(
                b",sha256=", b",sha256=A", 1
            )
            with zipfile.ZipFile(wheel, "w") as archive:
                for member, payload in payloads.items():
                    archive.writestr(member, payload)
            with self.assertRaisesRegex(
                evidence.EvidenceFailure, "wheel_record_payload_drift"
            ):
                evidence._wheel_contract(
                    wheel,
                    root=ROOT,
                    revision=evidence.DEFAULT_SUBJECT_REVISION,
                )

    def test_wheel_metadata_and_entry_points_reject_parseable_additions(self) -> None:
        metadata_variants = (
            evidence.EXPECTED_WHEEL_METADATA.replace(
                b"Version: 0.3.0\n", b"Version: 0.3.0\nVersion: 0.3.0\n"
            ),
            evidence.EXPECTED_WHEEL_METADATA + b"Requires-Dist: injected\n",
        )
        for payload in metadata_variants:
            with (
                self.subTest(payload=payload),
                self.assertRaises(evidence.EvidenceFailure),
            ):
                evidence._strict_metadata(payload)
        entry_point_variants = (
            b"[console_scripts]\ntctravel-analyze = attacker:main\n",
            evidence.EXPECTED_WHEEL_ENTRY_POINTS + b"unexpected = attacker:main\n",
            evidence.EXPECTED_WHEEL_ENTRY_POINTS + b"[other]\nname = attacker:main\n",
        )
        for payload in entry_point_variants:
            with (
                self.subTest(payload=payload),
                self.assertRaises(evidence.EvidenceFailure),
            ):
                evidence._strict_entry_points(payload)

    def test_runtime_expectations_are_derived_from_pinned_blobs(self) -> None:
        validation = _pinned_integer_constants("tctravel/validation.py")
        codec = _pinned_integer_constants("tctravel/codec.py")
        cli = _pinned_integer_constants("tctravel/cli.py")
        expected = evidence.EXPECTED_RUNTIME_CONTRACT
        self.assertEqual(expected["max_activities"], validation["MAX_ACTIVITIES"])
        self.assertEqual(expected["max_transfers"], validation["MAX_TRANSFERS"])
        self.assertEqual(
            expected["max_horizon_seconds"], validation["MAX_HORIZON_SECONDS"]
        )
        self.assertEqual(expected["max_input_bytes"], codec["MAX_INPUT_BYTES"])
        self.assertEqual(expected["max_json_depth"], codec["MAX_JSON_DEPTH"])
        self.assertEqual(
            expected["analysis_output_cap_bytes"],
            cli["MAX_ANALYSIS_OUTPUT_BYTES"],
        )
        self.assertEqual(expected["max_json_depth"], 24)
        self.assertEqual(expected["max_horizon_seconds"], 2_678_400)

    def test_subject_and_pipeline_allowlists_are_exact_and_legacy_free(self) -> None:
        self.assertEqual(
            list(evidence.SUBJECT_SOURCE_PATHS),
            sorted(evidence.SUBJECT_SOURCE_PATHS),
        )
        self.assertEqual(
            list(evidence.PIPELINE_SOURCE_PATHS),
            sorted(evidence.PIPELINE_SOURCE_PATHS),
        )
        self.assertEqual(
            len(set(evidence.SUBJECT_SOURCE_PATHS)),
            len(evidence.SUBJECT_SOURCE_PATHS),
        )
        self.assertEqual(
            len(set(evidence.PIPELINE_SOURCE_PATHS)),
            len(evidence.PIPELINE_SOURCE_PATHS),
        )
        for path in (*evidence.SUBJECT_SOURCE_PATHS, *evidence.PIPELINE_SOURCE_PATHS):
            self.assertNotIn("legacy", path.casefold())
            self.assertFalse(path.endswith(".html"))
            self.assertNotEqual(path, "site.json")

    def test_default_subject_and_tree_are_immutable_full_oids(self) -> None:
        self.assertEqual(len(evidence.DEFAULT_SUBJECT_REVISION), 40)
        self.assertEqual(len(evidence.DEFAULT_SUBJECT_TREE), 40)
        self.assertEqual(
            _git("rev-parse", f"{evidence.DEFAULT_SUBJECT_REVISION}^{{commit}}")
            .decode()
            .strip(),
            evidence.DEFAULT_SUBJECT_REVISION,
        )
        self.assertEqual(
            _git("rev-parse", f"{evidence.DEFAULT_SUBJECT_REVISION}^{{tree}}")
            .decode()
            .strip(),
            evidence.DEFAULT_SUBJECT_TREE,
        )

    def test_pipeline_dependencies_match_the_pinned_requirements(self) -> None:
        locked = _requirements_lock()
        requirements = {name: version for name, (version, _digest) in locked.items()}
        self.assertEqual(requirements, evidence.EXPECTED_PIPELINE_DISTRIBUTIONS)
        self.assertEqual(locked["Pillow"][1], evidence.EXPECTED_PILLOW_WHEEL_SHA256)
        for distribution, expected in requirements.items():
            self.assertEqual(importlib.metadata.version(distribution), expected)

    def test_renderer_environment_is_exactly_byte_bound(self) -> None:
        self.assertEqual(
            evidence._renderer_contract(), evidence.EXPECTED_RENDERER_CONTRACT
        )

    def test_workflow_labels_are_validated_against_pinned_source_symbols(
        self,
    ) -> None:
        sources = {
            path: _git("show", f"{evidence.DEFAULT_SUBJECT_REVISION}:{path}")
            for path in evidence.WORKFLOW_SOURCE_PATHS
        }
        contract = evidence._workflow_semantic_contract(sources)
        records = {record["display_symbol"]: record for record in contract}
        self.assertEqual(
            records["decode_itinerary_json"]["definition_path"],
            "tctravel/codec.py",
        )
        self.assertEqual(
            records["compile_temporal_graph"]["definition_path"],
            "tctravel/compiler.py",
        )
        self.assertEqual(records["decode_itinerary_json"]["call_count"], 1)
        self.assertEqual(records["compile_temporal_graph"]["call_count"], 1)
        self.assertEqual(records["_analyze_scenario"]["call_count"], 2)
        self.assertEqual(records["canonical_analysis_bytes"]["call_count"], 1)

        altered = dict(sources)
        altered["tctravel/analysis.py"] = altered["tctravel/analysis.py"].replace(
            b"best_timings, best_summary = _analyze_scenario(\n",
            b"best_timings, best_summary = renamed_scenario(\n",
            1,
        )
        with self.assertRaisesRegex(
            evidence.EvidenceFailure, "workflow_symbol_call_count_drift"
        ):
            evidence._workflow_semantic_contract(altered)

        altered_cli = dict(sources)
        altered_cli["tctravel/cli.py"] = altered_cli["tctravel/cli.py"].replace(
            b"report = analyze_interval_feasibility(itinerary)",
            b"report = renamed_analysis(itinerary)",
            1,
        )
        with self.assertRaisesRegex(
            evidence.EvidenceFailure, "workflow_symbol_call_count_drift"
        ):
            evidence._workflow_semantic_contract(altered_cli)

    def test_noncanonical_cli_bytes_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            evidence.EvidenceFailure, "cli_stdout_not_canonical"
        ):
            evidence._canonical_report(b'{"z":0,"a":1}\n')
        with self.assertRaisesRegex(
            evidence.EvidenceFailure, "cli_stdout_newline_contract_drift"
        ):
            evidence._canonical_report(b'{"a":1}\n\n')


if __name__ == "__main__":
    unittest.main()
