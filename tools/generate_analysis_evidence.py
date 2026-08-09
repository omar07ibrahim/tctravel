"""Capture source-bound evidence from an isolated TCTravel wheel.

The subject is materialized from allowlisted Git blobs, built without network
access, installed into a fresh virtual environment, and exercised through the
generated ``tctravel-analyze`` console script. Result visuals are derived from
captured stdout, stderr, exits, and parsed reports. The architecture visual
also validates its labels against exact subject-source blobs. The quarantined
legacy snapshot is never archived or read.
"""

# ruff: noqa: ISC004

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import email.policy
import hashlib
import html
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import textwrap
import zipfile
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Final, cast

import PIL
from PIL import Image, ImageDraw, ImageFont, features

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_SUBJECT_REVISION: Final = "7e9951e65941d05c6b757b1c2ade4f30af6cd9f8"
DEFAULT_SUBJECT_TREE: Final = "381c6ed9bb7c6e2dc155a1b111cb6bca3d683476"
SUBJECT_SOURCE_PATHS: Final = (
    "examples/synthetic_connection.v1.json",
    "examples/synthetic_tight_connection.v1.json",
    "pyproject.toml",
    "tctravel/__init__.py",
    "tctravel/__main__.py",
    "tctravel/analysis.py",
    "tctravel/cli.py",
    "tctravel/codec.py",
    "tctravel/compiler.py",
    "tctravel/errors.py",
    "tctravel/model.py",
    "tctravel/validation.py",
)
WORKFLOW_SOURCE_PATHS: Final = (
    "tctravel/analysis.py",
    "tctravel/cli.py",
    "tctravel/codec.py",
    "tctravel/compiler.py",
)
PIPELINE_SOURCE_PATHS: Final = (
    "requirements-evidence.txt",
    "tests/test_analysis_evidence.py",
    "tools/generate_analysis_evidence.py",
)

EVIDENCE_ROOT: Final = "docs/analysis-evidence"
RAW_ROBUST_STDOUT: Final = f"{EVIDENCE_ROOT}/raw/robust.stdout.json"
RAW_ROBUST_STDERR: Final = f"{EVIDENCE_ROOT}/raw/robust.stderr.txt"
RAW_TIGHT_STDOUT: Final = f"{EVIDENCE_ROOT}/raw/duration-sensitive.stdout.json"
RAW_TIGHT_STDERR: Final = f"{EVIDENCE_ROOT}/raw/duration-sensitive.stderr.txt"
RUNS_OUTPUT: Final = f"{EVIDENCE_ROOT}/raw/runs.json"
TERMINAL_SVG_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/cli-terminal.svg"
TERMINAL_PNG_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/cli-terminal.png"
ENVELOPE_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/feasibility-envelope.svg"
SLACK_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/deadline-slack.svg"
CHAIN_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/critical-chain.svg"
WORKFLOW_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/analysis-workflow.svg"
DECISION_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/analysis-decision-matrix.svg"
DEMO_OUTPUT: Final = f"{EVIDENCE_ROOT}/generated/cli-demo.gif"
MANIFEST_OUTPUT: Final = f"{EVIDENCE_ROOT}/manifest.json"

CHECK_COMMAND: Final = ".venv/bin/python tools/generate_analysis_evidence.py --check"
WRITE_COMMAND: Final = ".venv/bin/python tools/generate_analysis_evidence.py --write"
EXPECTED_PIPELINE_DISTRIBUTIONS: Final = {
    "Pillow": "12.3.0",
    "pip": "26.2",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}
EXPECTED_PILLOW_WHEEL_SHA256: Final = (
    "78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91"
)
EXPECTED_PILLOW_DIST_INFO_ROOT: Final = "pillow-12.3.0.dist-info"
EXPECTED_PILLOW_NATIVE_ROOT: Final = "pillow.libs"
EXPECTED_RENDERER_CONTRACT: Final = {
    "platform_machine": "x86_64",
    "platform_system": "Linux",
    "platform_tag": "linux-x86_64",
    "pillow_distribution_file_count": 142,
    "pillow_distribution_tree_sha256": (
        "74e7be577256b575983c8379b3ca639773daa168f0e207868c30964ea56b00ef"
    ),
    "pillow_module_within_distribution": True,
    "pillow_version": "12.3.0",
    "locked_pillow_wheel_sha256": EXPECTED_PILLOW_WHEEL_SHA256,
    "pillow_zlib_version": "1.3",
    "python_abi": "cpython-312-x86_64-linux-gnu",
    "python_implementation": "CPython",
    "python_version": "3.12.3",
    "python_zlib_build_version": "1.3",
    "python_zlib_runtime_version": "1.3",
}
EXPECTED_TERMINAL_DECODED_RGB_SHA256: Final = (
    "ceca72ea03151608958cad11f9e3fa84dbe176f9d2b7d3c39262dbc600a4141f"
)
EXPECTED_DEMO_DECODED_RGB_FRAME_SHA256: Final = (
    "8d3ab2b69f7507be82b334d4863e847d691fc7d32695dcccbecc50f1912a7191",
    "118e49b354f5d6063f3d494c46cb910269c39eea94a7a6a535e9e16528c1e7b4",
    "b9c8c5da2d28981fbd2b1f1bf253ab2a52310c21e76babc4af8a77e0e326ee82",
    "6b78d81f0ca2e48643a9d486a4b648b9ae1a24f2c80b37735c4f295a6e2c3248",
)
EXPECTED_WHEEL_METADATA: Final = (
    b"Metadata-Version: 2.4\n"
    b"Name: tctravel-temporal-compiler\n"
    b"Version: 0.3.0\n"
    b"Summary: Deterministic temporal graphs and interval-feasibility analysis\n"
    b"Requires-Python: >=3.11\n"
)
EXPECTED_WHEEL_ENTRY_POINTS: Final = (
    b"[console_scripts]\ntctravel-analyze = tctravel.cli:main\n"
)
EXPECTED_WHEEL_DESCRIPTOR: Final = (
    b"Wheel-Version: 1.0\n"
    b"Generator: setuptools (83.0.0)\n"
    b"Root-Is-Purelib: true\n"
    b"Tag: py3-none-any\n"
    b"\n"
)
EXPECTED_WHEEL_TOP_LEVEL: Final = b"tctravel\n"
EXPECTED_DIST_INFO_ROOT: Final = "tctravel_temporal_compiler-0.3.0.dist-info"
MINIMAL_SUBPROCESS_PATH: Final = "/usr/bin:/bin"
EXPECTED_RUNTIME_CONTRACT: Final = {
    "analysis_output_cap_bytes": 1048576,
    "base_prefix_distinct": True,
    "distribution_version": "0.3.0",
    "import_within_venv": True,
    "max_activities": 128,
    "max_horizon_seconds": 2678400,
    "max_input_bytes": 65536,
    "max_json_depth": 24,
    "max_transfers": 512,
    "pip_present": False,
    "site_packages_within_venv": True,
    "system_site_packages": False,
    "user_site_enabled": False,
}

SVG_WIDTH: Final = 1800
SVG_HEIGHT: Final = 1120
TERMINAL_COLUMNS: Final = 104


class EvidenceFailure(RuntimeError):
    """A redacted, stable failure while constructing evidence."""


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


@dataclass(frozen=True, slots=True)
class Execution:
    case_id: str
    command: tuple[str, ...]
    input_kind: str
    input_sha256: str
    derivation: str | None
    exit_code: int
    stdout: bytes
    stderr: bytes
    report: dict[str, Any] | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class CaptureSet:
    subject_revision: str
    subject_tree: str
    subject_sources: tuple[dict[str, object], ...]
    pipeline_revision: str
    pipeline_sources: tuple[dict[str, object], ...]
    wheel_contract: dict[str, object]
    install_contract: dict[str, object]
    runtime_contract: dict[str, object]
    executions: tuple[Execution, ...]


@dataclass(frozen=True, slots=True)
class TerminalChunk:
    byte_start: int
    byte_end: int
    text: str


@dataclass(frozen=True, slots=True)
class TerminalPresentation:
    case_id: str
    command: tuple[str, ...]
    exit_code: int
    status: str
    stderr_bytes: int
    stdout: bytes
    chunks: tuple[TerminalChunk, ...]


@dataclass(frozen=True, slots=True)
class DemoRunPresentation:
    case_id: str
    command: tuple[str, ...]
    exit_code: int
    status: str
    worst_slack_seconds: int
    witness_chain: tuple[str, ...]
    stdout_bytes: int
    stdout_sha256: str


@dataclass(frozen=True, slots=True)
class DemoFramePresentation:
    case_id: str
    phase: str
    step: str
    heading: str
    accent: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class DemoPresentation:
    runs: tuple[DemoRunPresentation, ...]
    frames: tuple[DemoFramePresentation, ...]
    loop: int
    timing_semantics: str


@dataclass(frozen=True, slots=True)
class PresentationModels:
    terminal: TerminalPresentation
    demo: DemoPresentation


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(
    document: object,
    *,
    pretty: bool = False,
    sort_keys: bool = True,
) -> bytes:
    if pretty:
        rendered = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=sort_keys,
        )
    else:
        rendered = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=sort_keys,
            separators=(",", ":"),
        )
    return (rendered + "\n").encode("ascii")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            env=(_minimal_subprocess_environment() if env is None else dict(env)),
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise EvidenceFailure("subprocess_unavailable") from None


def _minimal_subprocess_environment() -> dict[str, str]:
    """Return a fixed allowlist, never a filtered copy of the host environment."""

    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": MINIMAL_SUBPROCESS_PATH,
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "TMPDIR": "/tmp",
        "TZ": "UTC",
    }


def _git(root: Path, *args: str) -> bytes:
    completed = _run(("git", *args), cwd=root, timeout=30)
    if completed.returncode != 0:
        raise EvidenceFailure("git_contract_failed")
    return completed.stdout


def _require_clean_worktree(root: Path) -> None:
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise EvidenceFailure("write_requires_clean_worktree")


def _require_subject(root: Path, revision: str) -> str:
    resolved = _git(root, "rev-parse", f"{revision}^{{commit}}").decode().strip()
    if resolved != revision:
        raise EvidenceFailure("subject_revision_not_full_commit")
    ancestor = _run(
        ("git", "merge-base", "--is-ancestor", revision, "HEAD"),
        cwd=root,
        timeout=30,
    )
    if ancestor.returncode != 0:
        raise EvidenceFailure("subject_not_current_ancestor")
    tree = _git(root, "rev-parse", f"{revision}^{{tree}}").decode().strip()
    return tree


def _git_blob_record(root: Path, revision: str, path: str) -> dict[str, object]:
    payload = _git(root, "show", f"{revision}:{path}")
    blob_oid = _git(root, "rev-parse", f"{revision}:{path}").decode().strip()
    return {
        "blob_oid": blob_oid,
        "bytes": len(payload),
        "path": path,
        "sha256": _sha256(payload),
    }


def _subject_records(root: Path, revision: str) -> tuple[dict[str, object], ...]:
    return tuple(
        _git_blob_record(root, revision, path) for path in SUBJECT_SOURCE_PATHS
    )


def _pipeline_revision(root: Path) -> str:
    return (
        _git(
            root,
            "log",
            "-1",
            "--format=%H",
            "--",
            *PIPELINE_SOURCE_PATHS,
        )
        .decode()
        .strip()
    )


def _pipeline_records(root: Path, revision: str) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for path in PIPELINE_SOURCE_PATHS:
        committed = _git(root, "show", f"{revision}:{path}")
        current = (root / path).read_bytes()
        if current != committed:
            raise EvidenceFailure(f"pipeline_source_drift:{path}")
        records.append(_git_blob_record(root, revision, path))
    return tuple(records)


def _verify_current_subject(root: Path, revision: str) -> None:
    for path in SUBJECT_SOURCE_PATHS:
        committed = _git(root, "show", f"{revision}:{path}")
        current = (root / path).read_bytes()
        if current != committed:
            raise EvidenceFailure(f"subject_source_drift:{path}")


def _materialize_subject(root: Path, revision: str, target: Path) -> None:
    target.mkdir(parents=True)
    archive = _run(
        (
            "git",
            "archive",
            "--format=tar",
            revision,
            "--",
            *SUBJECT_SOURCE_PATHS,
        ),
        cwd=root,
        timeout=30,
    )
    if archive.returncode != 0 or archive.stderr:
        raise EvidenceFailure("subject_archive_failed")

    extracted: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or member.name not in SUBJECT_SOURCE_PATHS:
                raise EvidenceFailure("subject_archive_member_rejected")
            source = bundle.extractfile(member)
            if source is None:
                raise EvidenceFailure("subject_archive_member_unreadable")
            destination = target / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read())
            extracted.add(member.name)
    if extracted != set(SUBJECT_SOURCE_PATHS):
        raise EvidenceFailure("subject_archive_incomplete")


def _clean_environment(workspace: Path, source_date_epoch: str) -> dict[str, str]:
    environment = _minimal_subprocess_environment()
    environment.update(
        {
            "HOME": str(workspace / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PIP_CACHE_DIR": str(workspace / "pip-cache"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "SOURCE_DATE_EPOCH": source_date_epoch,
            "TMPDIR": str(workspace / "tmp"),
            "TZ": "UTC",
            "XDG_CACHE_HOME": str(workspace / "xdg-cache"),
            "XDG_CONFIG_HOME": str(workspace / "xdg-config"),
        }
    )
    return environment


def _validate_pipeline_versions() -> None:
    for distribution, expected in EXPECTED_PIPELINE_DISTRIBUTIONS.items():
        try:
            observed = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            raise EvidenceFailure("pipeline_dependency_missing") from None
        if observed != expected:
            raise EvidenceFailure("pipeline_dependency_version_mismatch")


def _strict_metadata(payload: bytes) -> None:
    try:
        message = BytesParser(policy=email.policy.strict).parsebytes(payload)
    except (UnicodeError, ValueError):
        raise EvidenceFailure("wheel_metadata_malformed") from None
    expected_fields = (
        ("Metadata-Version", "2.4"),
        ("Name", "tctravel-temporal-compiler"),
        ("Version", "0.3.0"),
        (
            "Summary",
            "Deterministic temporal graphs and interval-feasibility analysis",
        ),
        ("Requires-Python", ">=3.11"),
    )
    if (
        message.defects
        or tuple(message.raw_items()) != expected_fields
        or message.get_payload() != ""
        or payload != EXPECTED_WHEEL_METADATA
    ):
        raise EvidenceFailure("wheel_metadata_drift")


def _strict_entry_points(payload: bytes) -> None:
    try:
        text = payload.decode("ascii")
        parser = _CaseSensitiveConfigParser(
            allow_no_value=False,
            comment_prefixes=(),
            delimiters=("=",),
            empty_lines_in_values=False,
            inline_comment_prefixes=(),
            interpolation=None,
            strict=True,
        )
        parser.read_string(text)
    except (UnicodeError, configparser.Error):
        raise EvidenceFailure("wheel_entry_points_malformed") from None
    if (
        parser.defaults()
        or parser.sections() != ["console_scripts"]
        or parser.items("console_scripts", raw=True)
        != [("tctravel-analyze", "tctravel.cli:main")]
        or payload != EXPECTED_WHEEL_ENTRY_POINTS
    ):
        raise EvidenceFailure("wheel_entry_point_drift")


def _wheel_record_hash(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _strict_wheel_record(
    payload: bytes,
    *,
    record_member: str,
    installed_payloads: Mapping[str, bytes],
) -> None:
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise EvidenceFailure("wheel_record_not_canonical")
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("ascii"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        raise EvidenceFailure("wheel_record_malformed") from None
    if any(len(row) != 3 for row in rows):
        raise EvidenceFailure("wheel_record_malformed")
    by_path = {row[0]: (row[1], row[2]) for row in rows}
    if len(by_path) != len(rows):
        raise EvidenceFailure("wheel_record_duplicate_path")
    expected_paths = {*installed_payloads, record_member}
    if set(by_path) != expected_paths:
        raise EvidenceFailure("wheel_record_surface_drift")
    for path, member_payload in installed_payloads.items():
        expected = (_wheel_record_hash(member_payload), str(len(member_payload)))
        if by_path[path] != expected:
            raise EvidenceFailure("wheel_record_payload_drift")
    if by_path[record_member] != ("", ""):
        raise EvidenceFailure("wheel_record_self_hash_present")


def _wheel_contract(wheel: Path, *, root: Path, revision: str) -> dict[str, object]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            archive_members = archive.namelist()
            if len(archive_members) != len(set(archive_members)):
                raise EvidenceFailure("wheel_duplicate_member")
            members = sorted(archive_members)
            expected_package = sorted(
                path for path in SUBJECT_SOURCE_PATHS if path.startswith("tctravel/")
            )
            metadata_member = f"{EXPECTED_DIST_INFO_ROOT}/METADATA"
            descriptor_member = f"{EXPECTED_DIST_INFO_ROOT}/WHEEL"
            entry_point_member = f"{EXPECTED_DIST_INFO_ROOT}/entry_points.txt"
            top_level_member = f"{EXPECTED_DIST_INFO_ROOT}/top_level.txt"
            record_member = f"{EXPECTED_DIST_INFO_ROOT}/RECORD"
            expected_members = sorted(
                (
                    *expected_package,
                    metadata_member,
                    descriptor_member,
                    entry_point_member,
                    top_level_member,
                    record_member,
                )
            )
            if members != expected_members:
                raise EvidenceFailure("wheel_member_surface_drift")
            if any(archive.getinfo(member).is_dir() for member in members):
                raise EvidenceFailure("wheel_non_file_member")

            package_files: list[dict[str, object]] = []
            installed_payloads = {
                member: archive.read(member)
                for member in members
                if member != record_member
            }
            for member in expected_package:
                payload = installed_payloads[member]
                pinned_blob = _git(root, "show", f"{revision}:{member}")
                if payload != pinned_blob:
                    raise EvidenceFailure("wheel_package_payload_drift")
                package_files.append(
                    {
                        "bytes": len(payload),
                        "path": member,
                        "sha256": _sha256(payload),
                    }
                )
            _strict_metadata(installed_payloads[metadata_member])
            if installed_payloads[descriptor_member] != EXPECTED_WHEEL_DESCRIPTOR:
                raise EvidenceFailure("wheel_descriptor_drift")
            _strict_entry_points(installed_payloads[entry_point_member])
            if installed_payloads[top_level_member] != EXPECTED_WHEEL_TOP_LEVEL:
                raise EvidenceFailure("wheel_top_level_drift")
            record_payload = archive.read(record_member)
            _strict_wheel_record(
                record_payload,
                record_member=record_member,
                installed_payloads=installed_payloads,
            )
            all_payloads = {**installed_payloads, record_member: record_payload}
            wheel_files = [
                {
                    "bytes": len(all_payloads[member]),
                    "path": member,
                    "sha256": _sha256(all_payloads[member]),
                }
                for member in members
            ]
    except zipfile.BadZipFile:
        raise EvidenceFailure("wheel_archive_invalid") from None
    return {
        "distribution": "tctravel-temporal-compiler",
        "entry_point": "tctravel-analyze = tctravel.cli:main",
        "metadata_sha256": _sha256(EXPECTED_WHEEL_METADATA),
        "package_files": package_files,
        "wheel_files": wheel_files,
        "version": "0.3.0",
    }


def _reverse_object_keys(value: object) -> object:
    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        return {
            key: _reverse_object_keys(mapping[key]) for key in reversed(tuple(mapping))
        }
    if type(value) is list:
        return [_reverse_object_keys(item) for item in cast(list[object], value)]
    return value


def _canonical_report(stdout: bytes) -> dict[str, Any]:
    if not stdout.endswith(b"\n") or stdout.endswith(b"\n\n"):
        raise EvidenceFailure("cli_stdout_newline_contract_drift")
    try:
        document = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure("cli_stdout_not_json") from None
    if type(document) is not dict:
        raise EvidenceFailure("cli_stdout_not_object")
    report = cast(dict[str, object], document)
    if _json_bytes(report) != stdout:
        raise EvidenceFailure("cli_stdout_not_canonical")
    return cast(dict[str, Any], report)


def _execute(
    executable: Path,
    *,
    case_id: str,
    display_input: str,
    input_path: Path,
    input_kind: str,
    derivation: str | None,
    env: Mapping[str, str],
    cwd: Path,
) -> Execution:
    payload = input_path.read_bytes()
    completed = _run(
        (str(executable), str(input_path)),
        cwd=cwd,
        env=env,
        timeout=20,
    )
    report: dict[str, Any] | None = None
    error_code: str | None = None
    if completed.stdout:
        report = _canonical_report(completed.stdout)
        if completed.stderr:
            raise EvidenceFailure("accepted_run_wrote_stderr")
    elif completed.stderr:
        try:
            error = json.loads(completed.stderr)
            error_code = cast(str, error["error"]["code"])
        except (KeyError, TypeError, json.JSONDecodeError):
            raise EvidenceFailure("rejected_run_error_shape_drift") from None
        if _json_bytes({"error": {"code": error_code}}) != completed.stderr:
            raise EvidenceFailure("rejected_run_not_canonical")
    return Execution(
        case_id=case_id,
        command=("tctravel-analyze", display_input),
        input_kind=input_kind,
        input_sha256=_sha256(payload),
        derivation=derivation,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        report=report,
        error_code=error_code,
    )


def _require_pipless_fresh_venv(
    python: Path, *, cwd: Path, env: Mapping[str, str]
) -> None:
    probe_code = (
        "import importlib.util,json,sys;"
        "print(json.dumps({'base_prefix_distinct':sys.prefix!=sys.base_prefix,"
        "'pip_present':importlib.util.find_spec('pip') is not None},"
        "sort_keys=True,separators=(',',':')))"
    )
    completed = _run(
        (str(python), "-I", "-c", probe_code),
        cwd=cwd,
        env=env,
        timeout=20,
    )
    if completed.returncode != 0 or completed.stderr:
        raise EvidenceFailure("fresh_venv_inspection_failed")
    try:
        observed = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure("fresh_venv_inspection_failed") from None
    if observed != {"base_prefix_distinct": True, "pip_present": False}:
        raise EvidenceFailure("fresh_venv_isolation_failed")


def _fresh_venv_command(destination: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "venv",
        "--without-pip",
        str(destination),
    )


def _outer_pip_install_command(installed_python: Path, wheel: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "pip",
        "--python",
        str(installed_python),
        "install",
        "--no-deps",
        "--no-index",
        "--no-compile",
        str(wheel),
    )


def _capture_subject(root: Path, revision: str) -> CaptureSet:
    _validate_pipeline_versions()
    tree = _require_subject(root, revision)
    _verify_current_subject(root, revision)
    subject_sources = _subject_records(root, revision)
    pipeline_revision = _pipeline_revision(root)
    pipeline_sources = _pipeline_records(root, pipeline_revision)
    source_date_epoch = (
        _git(root, "show", "-s", "--format=%ct", revision).decode().strip()
    )

    work_parent = root / ".evidence-work"
    work_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="analysis-", dir=work_parent) as raw:
        workspace = Path(raw)
        source = workspace / "subject"
        wheelhouse = workspace / "wheelhouse"
        installed = workspace / "installed"
        outside = workspace / "outside"
        probes = workspace / "probes"
        for directory in (
            wheelhouse,
            outside,
            probes,
            workspace / "home",
            workspace / "pip-cache",
            workspace / "tmp",
            workspace / "xdg-cache",
            workspace / "xdg-config",
        ):
            directory.mkdir(parents=True)
        _materialize_subject(root, revision, source)
        environment = _clean_environment(workspace, source_date_epoch)

        built = _run(
            (
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "--wheel-dir",
                str(wheelhouse),
                str(source),
            ),
            cwd=outside,
            env=environment,
            timeout=60,
        )
        if built.returncode != 0:
            raise EvidenceFailure("offline_wheel_build_failed")
        wheels = list(wheelhouse.glob("*.whl"))
        if len(wheels) != 1:
            raise EvidenceFailure("wheel_count_drift")
        wheel = wheels[0]
        wheel_contract = _wheel_contract(wheel, root=root, revision=revision)

        created = _run(
            _fresh_venv_command(installed),
            cwd=outside,
            env=environment,
            timeout=60,
        )
        if created.returncode != 0:
            raise EvidenceFailure("isolated_venv_creation_failed")
        installed_python = installed / "bin" / "python"
        installed_cli = installed / "bin" / "tctravel-analyze"
        _require_pipless_fresh_venv(installed_python, cwd=outside, env=environment)
        installed_result = _run(
            _outer_pip_install_command(installed_python, wheel),
            cwd=outside,
            env=environment,
            timeout=60,
        )
        if installed_result.returncode != 0 or not installed_cli.is_file():
            raise EvidenceFailure("isolated_wheel_install_failed")

        inspection_code = textwrap.dedent(
            """
            import importlib.metadata
            import importlib.util
            import json
            import pathlib
            import site
            import sys
            import tctravel
            from tctravel import (
                MAX_ACTIVITIES,
                MAX_HORIZON_SECONDS,
                MAX_INPUT_BYTES,
                MAX_JSON_DEPTH,
                MAX_TRANSFERS,
            )
            from tctravel.cli import MAX_ANALYSIS_OUTPUT_BYTES

            prefix = pathlib.Path(sys.prefix).resolve()
            config = {}
            for raw_line in (prefix / "pyvenv.cfg").read_text(
                encoding="utf-8"
            ).splitlines():
                if "=" not in raw_line:
                    raise RuntimeError("malformed venv configuration")
                key, value = (part.strip() for part in raw_line.split("=", 1))
                if not key or key in config:
                    raise RuntimeError("ambiguous venv configuration")
                config[key] = value
            include_system = config.get("include-system-site-packages", "")
            if include_system.casefold() not in {"true", "false"}:
                raise RuntimeError("missing venv isolation observation")
            site_paths = [
                pathlib.Path(path).resolve() for path in site.getsitepackages()
            ]
            document = {
                "analysis_output_cap_bytes": MAX_ANALYSIS_OUTPUT_BYTES,
                "base_prefix_distinct": sys.prefix != sys.base_prefix,
                "distribution_version": importlib.metadata.version(
                    "tctravel-temporal-compiler"
                ),
                "import_within_venv": pathlib.Path(
                    tctravel.__file__
                ).resolve().is_relative_to(prefix),
                "max_activities": MAX_ACTIVITIES,
                "max_horizon_seconds": MAX_HORIZON_SECONDS,
                "max_input_bytes": MAX_INPUT_BYTES,
                "max_json_depth": MAX_JSON_DEPTH,
                "max_transfers": MAX_TRANSFERS,
                "pip_present": importlib.util.find_spec("pip") is not None,
                "site_packages_within_venv": bool(site_paths) and all(
                    path.is_relative_to(prefix) for path in site_paths
                ),
                "system_site_packages": include_system.casefold() == "true",
                "user_site_enabled": bool(site.ENABLE_USER_SITE),
            }
            print(json.dumps(document, sort_keys=True, separators=(",", ":")))
            """
        )
        inspected = _run(
            (str(installed_python), "-I", "-c", inspection_code),
            cwd=outside,
            env=environment,
            timeout=20,
        )
        if inspected.returncode != 0 or inspected.stderr:
            raise EvidenceFailure("isolated_install_inspection_failed")
        try:
            runtime_contract = cast(dict[str, object], json.loads(inspected.stdout))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EvidenceFailure("runtime_contract_not_json") from None
        if runtime_contract != EXPECTED_RUNTIME_CONTRACT:
            raise EvidenceFailure("runtime_contract_drift")

        robust_path = source / "examples" / "synthetic_connection.v1.json"
        tight_path = source / "examples" / "synthetic_tight_connection.v1.json"
        robust = _execute(
            installed_cli,
            case_id="robust_fixture",
            display_input="examples/synthetic_connection.v1.json",
            input_path=robust_path,
            input_kind="committed_fixture",
            derivation=None,
            env=environment,
            cwd=outside,
        )
        tight = _execute(
            installed_cli,
            case_id="duration_sensitive_fixture",
            display_input="examples/synthetic_tight_connection.v1.json",
            input_path=tight_path,
            input_kind="committed_fixture",
            derivation=None,
            env=environment,
            cwd=outside,
        )

        tight_document = cast(dict[str, Any], json.loads(tight_path.read_bytes()))
        equality_document = json.loads(json.dumps(tight_document))
        for activity in equality_document["activities"]:
            if activity["id"] == "security_check":
                activity["hard_deadline_offset_seconds"] = 1620
        equality_path = probes / "exact-deadline-equality.json"
        equality_path.write_bytes(_json_bytes(equality_document, pretty=True))
        equality = _execute(
            installed_cli,
            case_id="exact_deadline_equality",
            display_input="<derived:exact-deadline-equality>",
            input_path=equality_path,
            input_kind="derived_probe",
            derivation="tight fixture with security_check deadline set to 1620",
            env=environment,
            cwd=outside,
        )

        robust_document = cast(dict[str, Any], json.loads(robust_path.read_bytes()))
        infeasible_document = json.loads(json.dumps(robust_document))
        for activity in infeasible_document["activities"]:
            if activity["id"] == "local_shuttle":
                activity["hard_deadline_offset_seconds"] = 599
        infeasible_path = probes / "minimum-bound-rejection.json"
        infeasible_path.write_bytes(_json_bytes(infeasible_document, pretty=True))
        infeasible = _execute(
            installed_cli,
            case_id="minimum_bound_rejection",
            display_input="<derived:minimum-bound-rejection>",
            input_path=infeasible_path,
            input_kind="derived_probe",
            derivation="robust fixture with local_shuttle deadline set to 599",
            env=environment,
            cwd=outside,
        )

        reordered_document = cast(
            dict[str, object], _reverse_object_keys(robust_document)
        )
        reordered_document["activities"] = list(
            reversed(cast(list[object], reordered_document["activities"]))
        )
        reordered_document["transfers"] = list(
            reversed(cast(list[object], reordered_document["transfers"]))
        )
        reordered_path = probes / "reordered-input.json"
        reordered_path.write_bytes(
            _json_bytes(reordered_document, pretty=True, sort_keys=False)
        )
        reordered = _execute(
            installed_cli,
            case_id="reordered_input_invariance",
            display_input="<derived:reordered-input>",
            input_path=reordered_path,
            input_kind="derived_probe",
            derivation="robust fixture with reversed arrays and object keys",
            env=environment,
            cwd=outside,
        )

        executions = (robust, tight, equality, infeasible, reordered)
        _validate_executions(executions)

        module = _run(
            (str(installed_python), "-m", "tctravel", str(robust_path)),
            cwd=outside,
            env=environment,
            timeout=20,
        )
        if (
            module.returncode != robust.exit_code
            or module.stdout != robust.stdout
            or module.stderr != robust.stderr
        ):
            raise EvidenceFailure("module_entry_point_parity_failed")

        return CaptureSet(
            subject_revision=revision,
            subject_tree=tree,
            subject_sources=subject_sources,
            pipeline_revision=pipeline_revision,
            pipeline_sources=pipeline_sources,
            wheel_contract=wheel_contract,
            install_contract={
                "console_script_present": True,
                "import_within_fresh_venv": runtime_contract["import_within_venv"],
                "module_entry_point_parity": True,
                "package_index_access": False,
                "pip_present_in_fresh_venv": runtime_contract["pip_present"],
                "site_packages_within_fresh_venv": runtime_contract[
                    "site_packages_within_venv"
                ],
                "system_site_packages": runtime_contract["system_site_packages"],
                "user_site_enabled": runtime_contract["user_site_enabled"],
                "venv_prefix_distinct": runtime_contract["base_prefix_distinct"],
            },
            runtime_contract=runtime_contract,
            executions=executions,
        )


def _validate_executions(executions: Sequence[Execution]) -> None:
    by_id = {execution.case_id: execution for execution in executions}
    robust = by_id["robust_fixture"]
    tight = by_id["duration_sensitive_fixture"]
    equality = by_id["exact_deadline_equality"]
    infeasible = by_id["minimum_bound_rejection"]
    reordered = by_id["reordered_input_invariance"]
    if (
        robust.exit_code != 0
        or robust.report is None
        or robust.report["status"] != "robust"
        or robust.report["worst_case"]["minimum_deadline_slack_seconds"] != 900
    ):
        raise EvidenceFailure("robust_fixture_observation_drift")
    if (
        tight.exit_code != 1
        or tight.report is None
        or tight.report["status"] != "duration_sensitive"
        or tight.report["worst_case"]["minimum_deadline_slack_seconds"] != -120
        or tight.report["worst_case"]["witness_chain"]
        != ["local_shuttle", "security_check"]
    ):
        raise EvidenceFailure("tight_fixture_observation_drift")
    if (
        equality.exit_code != 0
        or equality.report is None
        or equality.report["status"] != "robust"
        or equality.report["worst_case"]["minimum_deadline_slack_seconds"] != 0
    ):
        raise EvidenceFailure("deadline_equality_observation_drift")
    if (
        infeasible.exit_code != 3
        or infeasible.stdout
        or infeasible.error_code != "infeasible_deadline"
    ):
        raise EvidenceFailure("minimum_bound_rejection_drift")
    if (
        reordered.exit_code != 0
        or reordered.stdout != robust.stdout
        or reordered.stderr
    ):
        raise EvidenceFailure("reordered_input_invariance_drift")


def _execution(captures: CaptureSet, case_id: str) -> Execution:
    try:
        return next(item for item in captures.executions if item.case_id == case_id)
    except StopIteration:
        raise EvidenceFailure("required_execution_missing") from None


def _require_report(execution: Execution) -> dict[str, Any]:
    if execution.report is None:
        raise EvidenceFailure("accepted_report_missing")
    return execution.report


def _activity(report: Mapping[str, Any], activity_id: str) -> dict[str, Any]:
    try:
        return next(
            activity
            for activity in report["activities"]
            if activity["activity_id"] == activity_id
        )
    except StopIteration:
        raise EvidenceFailure("required_activity_missing") from None


def _svg_open(
    *,
    title_id: str,
    description_id: str,
    title: str,
    description: str,
    attributes: Mapping[str, object] | None = None,
) -> list[str]:
    rendered_attributes = "".join(
        f' data-{_escape(key)}="{_escape(value)}"'
        for key, value in sorted((attributes or {}).items())
    )
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
            f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" '
            f'role="img" aria-labelledby="{title_id} {description_id}"'
            f"{rendered_attributes}>"
        ),
        f'  <title id="{title_id}">{_escape(title)}</title>',
        f'  <desc id="{description_id}">{_escape(description)}</desc>',
        '  <rect width="1800" height="1120" fill="#f8fafc"/>',
    ]


def _svg_header(lines: list[str], title: str, subtitle: str) -> None:
    lines.extend(
        (
            '  <rect x="32" y="28" width="1736" height="136" rx="26" fill="#0f172a"/>',
            (
                '  <text x="76" y="84" fill="#f8fafc" font-size="34" '
                'font-weight="800" font-family="Inter, Segoe UI, Arial, '
                f'sans-serif">{_escape(title)}</text>'
            ),
            (
                '  <text x="76" y="124" fill="#cbd5e1" font-size="18" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">'
                f"{_escape(subtitle)}</text>"
            ),
        )
    )


def _svg_finish(lines: list[str]) -> bytes:
    lines.extend(("</svg>", ""))
    return "\n".join(lines).encode("utf-8")


def _pillow_distribution_tree() -> tuple[int, str, Path]:
    """Hash Pillow's installed payload as sorted path/NUL/size/NUL/hash rows.

    The tree is exactly every non-bytecode regular file listed by the installed
    distribution below ``PIL/``, ``pillow.libs/``, or
    ``pillow-12.3.0.dist-info/``. Each row is UTF-8 relative path, NUL, decimal
    byte length, NUL, lowercase SHA-256 hex, then LF. Installer-created caches
    are excluded so import history cannot alter the renderer identity.
    """

    try:
        distribution = importlib.metadata.distribution("Pillow")
    except importlib.metadata.PackageNotFoundError:
        raise EvidenceFailure("renderer_pillow_distribution_missing") from None
    distribution_files = distribution.files
    if distribution_files is None:
        raise EvidenceFailure("renderer_pillow_file_manifest_missing")
    selected: dict[str, Path] = {}
    for relative in distribution_files:
        relative_path = Path(str(relative))
        if not relative_path.parts:
            continue
        root_name = relative_path.parts[0]
        if root_name not in (
            "PIL",
            EXPECTED_PILLOW_DIST_INFO_ROOT,
            EXPECTED_PILLOW_NATIVE_ROOT,
        ):
            continue
        if "__pycache__" in relative_path.parts or relative_path.suffix == ".pyc":
            continue
        rendered = relative_path.as_posix()
        if rendered in selected:
            raise EvidenceFailure("renderer_pillow_duplicate_file")
        installed = Path(str(distribution.locate_file(relative)))
        if not installed.is_file():
            raise EvidenceFailure("renderer_pillow_file_missing")
        selected[rendered] = installed
    if not selected:
        raise EvidenceFailure("renderer_pillow_tree_empty")

    digest = hashlib.sha256()
    for relative, installed in sorted(selected.items()):
        payload = installed.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256(payload).encode("ascii"))
        digest.update(b"\n")
    site_root = Path(str(distribution.locate_file(""))).resolve()
    return len(selected), digest.hexdigest(), site_root


def _renderer_contract() -> dict[str, object]:
    file_count, tree_digest, site_root = _pillow_distribution_tree()
    module_file = PIL.__file__
    module_path = Path(module_file).resolve()
    python_abi = sysconfig.get_config_var("SOABI")
    pillow_zlib = features.version("zlib")
    if type(python_abi) is not str or type(pillow_zlib) is not str:
        raise EvidenceFailure("renderer_runtime_detail_missing")
    observed: dict[str, object] = {
        "platform_machine": platform.machine(),
        "platform_system": platform.system(),
        "platform_tag": sysconfig.get_platform(),
        "pillow_distribution_file_count": file_count,
        "pillow_distribution_tree_sha256": tree_digest,
        "pillow_module_within_distribution": module_path.is_relative_to(site_root),
        "pillow_version": PIL.__version__,
        "locked_pillow_wheel_sha256": EXPECTED_PILLOW_WHEEL_SHA256,
        "pillow_zlib_version": pillow_zlib,
        "python_abi": python_abi,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_zlib_build_version": zlib.ZLIB_VERSION,
        "python_zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
    }
    if observed != EXPECTED_RENDERER_CONTRACT:
        raise EvidenceFailure("renderer_environment_drift")
    return observed


def _terminal_chunks(stdout: bytes) -> tuple[TerminalChunk, ...]:
    try:
        text = stdout[:-1].decode("ascii")
    except UnicodeDecodeError:
        raise EvidenceFailure("terminal_stdout_not_ascii") from None
    chunks: list[TerminalChunk] = []
    for start in range(0, len(text), TERMINAL_COLUMNS):
        end = min(len(text), start + TERMINAL_COLUMNS)
        chunks.append(TerminalChunk(start, end, text[start:end]))
    return tuple(chunks)


def _terminal_presentation(captures: CaptureSet) -> TerminalPresentation:
    tight = _execution(captures, "duration_sensitive_fixture")
    report = _require_report(tight)
    return TerminalPresentation(
        case_id=tight.case_id,
        command=tight.command,
        exit_code=tight.exit_code,
        status=cast(str, report["status"]),
        stderr_bytes=len(tight.stderr),
        stdout=tight.stdout,
        chunks=_terminal_chunks(tight.stdout),
    )


def _terminal_presentation_record(
    presentation: TerminalPresentation,
) -> dict[str, object]:
    return {
        "case_id": presentation.case_id,
        "chunks": [
            {
                "byte_end": chunk.byte_end,
                "byte_start": chunk.byte_start,
                "text": chunk.text,
            }
            for chunk in presentation.chunks
        ],
        "command": list(presentation.command),
        "exit_code": presentation.exit_code,
        "status": presentation.status,
        "stderr_bytes": presentation.stderr_bytes,
        "stdout_bytes": len(presentation.stdout),
        "stdout_sha256": _sha256(presentation.stdout),
        "terminal_columns": TERMINAL_COLUMNS,
        "trailing_newline": "LF",
    }


def _terminal_svg(presentation: TerminalPresentation) -> bytes:
    lines = _svg_open(
        title_id="terminal-title",
        description_id="terminal-desc",
        title="Installed TCTravel CLI capture",
        description=(
            "The complete canonical stdout from the installed wheel is wrapped "
            "visually. Byte offsets on each line reconstruct the original output."
        ),
        attributes={
            "case-id": presentation.case_id,
            "exit-code": presentation.exit_code,
            "stdout-bytes": len(presentation.stdout),
            "stdout-sha256": _sha256(presentation.stdout),
        },
    )
    _svg_header(
        lines,
        "Installed CLI · exact captured stdout",
        "duration-sensitive fixture · complete ASCII JSON · visual wrapping only",
    )
    lines.extend(
        (
            '  <rect x="52" y="188" width="1696" height="858" rx="22" '
            'fill="#07111f" stroke="#334155" stroke-width="2"/>',
            '  <circle cx="86" cy="224" r="8" fill="#fb7185"/>',
            '  <circle cx="112" cy="224" r="8" fill="#fbbf24"/>',
            '  <circle cx="138" cy="224" r="8" fill="#4ade80"/>',
            (
                '  <text x="170" y="232" fill="#94a3b8" font-size="15" '
                'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                'monospace">isolated wheel · no PYTHONPATH · no package index</text>'
            ),
            (
                '  <text x="78" y="276" fill="#67e8f9" font-size="16" '
                'font-weight="700" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">$ {_escape(" ".join(presentation.command))}'
                "</text>"
            ),
            (
                '  <text x="78" y="310" fill="#fda4af" font-size="14" '
                'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                f'monospace">exit {presentation.exit_code} · status '
                f"{_escape(presentation.status)} · stdout "
                f"{len(presentation.stdout)} bytes · stderr "
                f"{presentation.stderr_bytes} bytes</text>"
            ),
        )
    )
    start_y = 354
    line_height = 25
    if start_y + len(presentation.chunks) * line_height > 1000:
        raise EvidenceFailure("terminal_capture_exceeds_viewport")
    for index, chunk in enumerate(presentation.chunks):
        y = start_y + index * line_height
        lines.append(
            f'  <text x="78" y="{y}" fill="#d1fae5" font-size="13" '
            'font-family="SFMono-Regular, Consolas, Liberation Mono, '
            f'monospace" data-byte-start="{chunk.byte_start}" '
            f'data-byte-end="{chunk.byte_end}">{_escape(chunk.text)}</text>'
        )
    footer_y = start_y + len(presentation.chunks) * line_height + 18
    lines.extend(
        (
            (
                f'  <text x="78" y="{footer_y}" fill="#94a3b8" '
                'font-size="13" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">[byte {len(presentation.stdout) - 1}: '
                "LF]</text>"
            ),
            (
                f'  <text x="78" y="{footer_y + 30}" fill="#94a3b8" '
                'font-size="13" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">stdout sha256 '
                f"{_sha256(presentation.stdout)}</text>"
            ),
        )
    )
    return _svg_finish(lines)


def _terminal_png(presentation: TerminalPresentation) -> bytes:
    logical = Image.new("RGB", (900, 560), "#07111f")
    draw = ImageDraw.Draw(logical)
    font = ImageFont.load_default()
    draw.rounded_rectangle(
        (10, 10, 890, 550),
        radius=14,
        fill="#07111f",
        outline="#334155",
        width=2,
    )
    draw.ellipse((28, 26, 38, 36), fill="#fb7185")
    draw.ellipse((46, 26, 56, 36), fill="#fbbf24")
    draw.ellipse((64, 26, 74, 36), fill="#4ade80")
    draw.text((90, 26), "ISOLATED WHEEL / EXACT STDOUT", font=font, fill="#94a3b8")
    draw.text(
        (28, 54),
        "$ " + " ".join(presentation.command),
        font=font,
        fill="#67e8f9",
    )
    draw.text(
        (28, 72),
        (
            f"exit={presentation.exit_code} status={presentation.status} "
            f"stdout={len(presentation.stdout)}B "
            f"stderr={presentation.stderr_bytes}B"
        ),
        font=font,
        fill="#fda4af",
    )
    y = 96
    for chunk in presentation.chunks:
        draw.text((28, y), chunk.text, font=font, fill="#d1fae5")
        y += 13
    draw.text(
        (28, 520),
        f"LF / stdout sha256 {_sha256(presentation.stdout)}",
        font=font,
        fill="#94a3b8",
    )
    scaled = logical.resize(  # pyright: ignore[reportUnknownMemberType]
        (SVG_WIDTH, SVG_HEIGHT),
        resample=Image.Resampling.NEAREST,
    )
    output = io.BytesIO()
    scaled.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _envelope_svg(captures: CaptureSet) -> bytes:
    robust = _execution(captures, "robust_fixture")
    tight = _execution(captures, "duration_sensitive_fixture")
    reports = (
        ("ROBUST FIXTURE", _require_report(robust), "#0369a1"),
        ("DURATION-SENSITIVE FIXTURE", _require_report(tight), "#be123c"),
    )
    axis_x = 350
    axis_width = 1360
    value_label_x = 1670
    lines = _svg_open(
        title_id="envelope-title",
        description_id="envelope-desc",
        title="Executed interval-feasibility envelope",
        description=(
            "Best and worst earliest start-finish bars from the installed CLI, "
            "with exact hard-deadline markers for both committed fixtures."
        ),
        attributes={
            "axis-end-x": axis_x + axis_width,
            "subject-revision": captures.subject_revision,
            "value-label-x": value_label_x,
        },
    )
    _svg_header(
        lines,
        "Executed interval-feasibility envelope",
        "all-minimum vs all-maximum earliest schedule · seconds after anchor",
    )
    panel_y_values = (198, 640)
    for panel_index, ((label, report, accent), panel_y) in enumerate(
        zip(reports, panel_y_values, strict=True)
    ):
        activities = cast(list[dict[str, Any]], report["activities"])
        axis_max = max(
            cast(int, activity["hard_deadline_offset_seconds"])
            for activity in activities
        )
        scale = axis_width / axis_max
        status = cast(str, report["status"])
        slack = cast(int, report["worst_case"]["minimum_deadline_slack_seconds"])
        lines.extend(
            (
                (
                    f'  <g data-panel="{panel_index}" data-status="{status}" '
                    f'data-axis-max-seconds="{axis_max}" '
                    f'data-minimum-worst-slack-seconds="{slack}">'
                ),
                (
                    f'    <rect x="48" y="{panel_y}" width="1704" height="404" '
                    'rx="22" fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>'
                ),
                (
                    f'    <text x="78" y="{panel_y + 42}" fill="{accent}" '
                    'font-size="19" font-weight="800" font-family="Inter, '
                    f'Segoe UI, Arial, sans-serif">{label}</text>'
                ),
                (
                    f'    <text x="1718" y="{panel_y + 42}" text-anchor="end" '
                    'fill="#334155" font-size="15" font-weight="700" '
                    'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                    f'monospace">status {status} · min worst slack {slack:+d}s</text>'
                ),
                (
                    f'    <line x1="{axis_x}" y1="{panel_y + 82}" '
                    f'x2="{axis_x + axis_width}" y2="{panel_y + 82}" '
                    'stroke="#94a3b8" stroke-width="2"/>'
                ),
            )
        )
        for tick_index in range(5):
            tick_value = round(axis_max * tick_index / 4)
            x = axis_x + axis_width * tick_index / 4
            lines.extend(
                (
                    f'    <line x1="{x:.1f}" y1="{panel_y + 76}" '
                    f'x2="{x:.1f}" y2="{panel_y + 340}" '
                    'stroke="#e2e8f0" stroke-width="1"/>',
                    (
                        f'    <text x="{x:.1f}" y="{panel_y + 70}" '
                        'text-anchor="middle" fill="#64748b" font-size="12" '
                        'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                        f'monospace">{tick_value}s</text>'
                    ),
                )
            )
        for index, activity in enumerate(activities):
            row_y = panel_y + 116 + index * 86
            activity_id = cast(str, activity["activity_id"])
            best = cast(dict[str, Any], activity["best_case"])
            worst = cast(dict[str, Any], activity["worst_case"])
            deadline = cast(int, activity["hard_deadline_offset_seconds"])
            best_start = cast(int, best["earliest_start_offset_seconds"])
            best_finish = cast(int, best["earliest_finish_offset_seconds"])
            worst_start = cast(int, worst["earliest_start_offset_seconds"])
            worst_finish = cast(int, worst["earliest_finish_offset_seconds"])
            deadline_x = axis_x + deadline * scale
            best_x = axis_x + best_start * scale
            best_width = max(3.0, (best_finish - best_start) * scale)
            worst_x = axis_x + worst_start * scale
            worst_width = max(3.0, (worst_finish - worst_start) * scale)
            breach = worst_finish > deadline
            lines.extend(
                (
                    (
                        f'    <g data-activity-id="{_escape(activity_id)}" '
                        f'data-best-start="{best_start}" data-best-finish="{best_finish}" '
                        f'data-worst-start="{worst_start}" '
                        f'data-worst-finish="{worst_finish}" '
                        f'data-deadline="{deadline}" data-breach="{str(breach).lower()}">'
                    ),
                    (
                        f'      <text x="78" y="{row_y + 28}" fill="#0f172a" '
                        'font-size="15" font-weight="700" font-family="Inter, '
                        f'Segoe UI, Arial, sans-serif">{_escape(activity_id)}</text>'
                    ),
                    (
                        f'      <rect x="{best_x:.1f}" y="{row_y}" '
                        f'width="{best_width:.1f}" height="22" rx="7" '
                        'fill="#e0f2fe" stroke="#0284c7" stroke-width="2" '
                        'stroke-dasharray="7 4"/>'
                    ),
                    (
                        f'      <rect x="{worst_x:.1f}" y="{row_y + 30}" '
                        f'width="{worst_width:.1f}" height="22" rx="7" '
                        f'fill="{"#fecdd3" if breach else "#dcfce7"}" '
                        f'stroke="{"#be123c" if breach else "#15803d"}" '
                        'stroke-width="2"/>'
                    ),
                    (
                        f'      <line x1="{deadline_x:.1f}" y1="{row_y - 7}" '
                        f'x2="{deadline_x:.1f}" y2="{row_y + 59}" '
                        'stroke="#b91c1c" stroke-width="3"/>'
                    ),
                    (
                        f'      <text x="{value_label_x}" y="{row_y + 19}" '
                        'text-anchor="end" '
                        'fill="#0369a1" font-size="12" font-family="SFMono-Regular, '
                        f'Consolas, Liberation Mono, monospace">best {best_start}→'
                        f"{best_finish}s</text>"
                    ),
                    (
                        f'      <text x="{value_label_x}" y="{row_y + 48}" '
                        'text-anchor="end" '
                        f'fill="{"#be123c" if breach else "#166534"}" font-size="12" '
                        'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                        f'monospace">worst {worst_start}→{worst_finish}s · '
                        f"D {deadline}s</text>"
                    ),
                    "    </g>",
                )
            )
        lines.append("  </g>")
    lines.extend(
        (
            '  <rect x="80" y="1064" width="22" height="12" rx="4" '
            'fill="#e0f2fe" stroke="#0284c7" stroke-dasharray="5 3"/>',
            '  <text x="112" y="1075" fill="#475569" font-size="13" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">all-minimum '
            "earliest activity</text>",
            '  <rect x="360" y="1064" width="22" height="12" rx="4" '
            'fill="#dcfce7" stroke="#15803d"/>',
            '  <text x="392" y="1075" fill="#475569" font-size="13" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">all-maximum '
            "earliest activity</text>",
            '  <line x1="686" y1="1060" x2="686" y2="1080" '
            'stroke="#b91c1c" stroke-width="3"/>',
            '  <text x="700" y="1075" fill="#475569" font-size="13" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">hard completion '
            "deadline</text>",
        )
    )
    return _svg_finish(lines)


def _slack_svg(captures: CaptureSet) -> bytes:
    observations = (
        ("robust", _require_report(_execution(captures, "robust_fixture"))),
        (
            "duration-sensitive",
            _require_report(_execution(captures, "duration_sensitive_fixture")),
        ),
    )
    values = [
        cast(int, activity[scenario]["deadline_slack_seconds"])
        for _, report in observations
        for activity in report["activities"]
        for scenario in ("best_case", "worst_case")
    ]
    lower = min(-300, (min(values) // 300) * 300)
    upper = max(300, ((max(values) + 299) // 300) * 300)
    axis_x = 420
    axis_width = 1000
    value_label_x = 1712
    scale = axis_width / (upper - lower)
    zero_x = axis_x + (0 - lower) * scale
    lines = _svg_open(
        title_id="slack-title",
        description_id="slack-desc",
        title="Executed deadline-slack profile",
        description=(
            "Best and worst deadline slack share one signed scale. Direct labels "
            "show the exact values observed in installed CLI reports."
        ),
        attributes={
            "axis-end-x": axis_x + axis_width,
            "scale-min": lower,
            "scale-max": upper,
            "value-label-x": value_label_x,
            "zero-x": f"{zero_x:.1f}",
        },
    )
    _svg_header(
        lines,
        "Executed deadline-slack profile",
        "best → worst margin on one shared scale · negative crosses the deadline",
    )
    lines.extend(
        (
            '  <rect x="48" y="194" width="1704" height="850" rx="22" '
            'fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>',
            (
                f'  <line x1="{zero_x:.1f}" y1="250" x2="{zero_x:.1f}" '
                'y2="980" stroke="#0f172a" stroke-width="3"/>'
            ),
            (
                f'  <text x="{zero_x:.1f}" y="232" text-anchor="middle" '
                'fill="#0f172a" font-size="13" font-weight="800" '
                'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                'monospace">0s deadline boundary</text>'
            ),
        )
    )
    for tick in range(lower, upper + 1, 600):
        x = axis_x + (tick - lower) * scale
        lines.extend(
            (
                f'  <line x1="{x:.1f}" y1="250" x2="{x:.1f}" y2="980" '
                'stroke="#e2e8f0" stroke-width="1"/>',
                (
                    f'  <text x="{x:.1f}" y="1008" text-anchor="middle" '
                    'fill="#64748b" font-size="12" font-family="SFMono-Regular, '
                    f'Consolas, Liberation Mono, monospace">{tick:+d}s</text>'
                ),
            )
        )
    row = 0
    for fixture_id, report in observations:
        for activity in report["activities"]:
            activity_id = cast(str, activity["activity_id"])
            best = cast(int, activity["best_case"]["deadline_slack_seconds"])
            worst = cast(int, activity["worst_case"]["deadline_slack_seconds"])
            best_x = axis_x + (best - lower) * scale
            worst_x = axis_x + (worst - lower) * scale
            y = 306 + row * 105
            negative = worst < 0
            lines.extend(
                (
                    (
                        f'  <g data-fixture="{fixture_id}" '
                        f'data-activity-id="{_escape(activity_id)}" '
                        f'data-best-slack="{best}" data-worst-slack="{worst}">'
                    ),
                    (
                        f'    <text x="82" y="{y - 6}" fill="#0f172a" '
                        'font-size="15" font-weight="800" font-family="Inter, '
                        f'Segoe UI, Arial, sans-serif">{_escape(activity_id)}</text>'
                    ),
                    (
                        f'    <text x="82" y="{y + 18}" fill="#64748b" '
                        'font-size="12" font-family="SFMono-Regular, Consolas, '
                        f'Liberation Mono, monospace">{fixture_id}</text>'
                    ),
                    (
                        f'    <line x1="{min(best_x, worst_x):.1f}" y1="{y}" '
                        f'x2="{max(best_x, worst_x):.1f}" y2="{y}" '
                        'stroke="#64748b" stroke-width="5"/>'
                    ),
                    (
                        f'    <circle cx="{best_x:.1f}" cy="{y}" r="11" '
                        'fill="#e0f2fe" stroke="#0369a1" stroke-width="3"/>'
                    ),
                    (
                        f'    <path d="M {worst_x:.1f} {y - 13} L '
                        f"{worst_x - 12:.1f} {y + 10} L {worst_x + 12:.1f} "
                        f'{y + 10} Z" fill="{"#fecdd3" if negative else "#dcfce7"}" '
                        f'stroke="{"#be123c" if negative else "#15803d"}" '
                        'stroke-width="3"/>'
                    ),
                    (
                        f'    <text x="{value_label_x}" y="{y + 5}" '
                        'text-anchor="end" '
                        f'fill="{"#be123c" if negative else "#334155"}" '
                        'font-size="14" font-weight="800" '
                        'font-family="SFMono-Regular, Consolas, Liberation Mono, '
                        f'monospace">best {best:+d}s → worst {worst:+d}s</text>'
                    ),
                    "  </g>",
                )
            )
            row += 1
    lines.extend(
        (
            '  <circle cx="90" cy="1074" r="9" fill="#e0f2fe" '
            'stroke="#0369a1" stroke-width="3"/>',
            '  <text x="110" y="1079" fill="#475569" font-size="13" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">best-case slack</text>',
            '  <path d="M 292 1061 L 281 1082 L 303 1082 Z" fill="#dcfce7" '
            'stroke="#15803d" stroke-width="3"/>',
            '  <text x="315" y="1079" fill="#475569" font-size="13" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">worst-case slack</text>',
        )
    )
    return _svg_finish(lines)


def _chain_svg(captures: CaptureSet) -> bytes:
    report = _require_report(_execution(captures, "duration_sensitive_fixture"))
    chain = cast(list[str], report["worst_case"]["witness_chain"])
    if chain != ["local_shuttle", "security_check"]:
        raise EvidenceFailure("critical_chain_drift")
    shuttle = _activity(report, "local_shuttle")
    security = _activity(report, "security_check")
    gate = _activity(report, "gate_walk")
    shuttle_worst = cast(dict[str, Any], shuttle["worst_case"])
    security_worst = cast(dict[str, Any], security["worst_case"])
    gap = cast(int, security_worst["earliest_start_offset_seconds"]) - cast(
        int, shuttle_worst["earliest_finish_offset_seconds"]
    )
    shuttle_duration = cast(int, shuttle["duration_interval_seconds"]["maximum"])
    security_duration = cast(int, security["duration_interval_seconds"]["maximum"])
    finish = cast(int, security_worst["earliest_finish_offset_seconds"])
    deadline = cast(int, security["hard_deadline_offset_seconds"])
    slack = cast(int, security_worst["deadline_slack_seconds"])
    equation = f"0 + {shuttle_duration} + {gap} + {security_duration} = {finish}"
    lines = _svg_open(
        title_id="chain-title",
        description_id="chain-desc",
        title="Deterministic worst-case witness chain",
        description=(
            "The binding predecessor explanation for the tight fixture, derived "
            "from its installed CLI report. It is not a CPM critical path."
        ),
        attributes={
            "chain": "->".join(chain),
            "deadline": deadline,
            "finish": finish,
            "slack": slack,
            "transfer-gap": gap,
        },
    )
    _svg_header(
        lines,
        "Deterministic worst-case witness",
        "binding predecessor explanation · tight fixture · all-maximum durations",
    )
    lines.extend(
        (
            '  <rect x="48" y="198" width="1704" height="830" rx="24" '
            'fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>',
            '  <rect x="92" y="248" width="680" height="238" rx="24" '
            'fill="#eff6ff" stroke="#0369a1" stroke-width="3"/>',
            '  <text x="132" y="302" fill="#075985" font-size="18" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "1 · local_shuttle</text>",
            (
                '  <text x="132" y="352" fill="#0f172a" font-size="28" '
                'font-weight="800" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">0 → {shuttle_duration}s</text>'
            ),
            (
                '  <text x="132" y="395" fill="#475569" font-size="16" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">release binds · '
                f"maximum duration {shuttle_duration}s</text>"
            ),
            (
                '  <text x="132" y="438" fill="#166534" font-size="16" '
                'font-weight="700" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">finish '
                f"{shuttle_worst['earliest_finish_offset_seconds']}s · slack "
                f"{shuttle_worst['deadline_slack_seconds']:+d}s</text>"
            ),
            '  <path d="M 772 366 C 860 366, 896 366, 980 366" fill="none" '
            'stroke="#7c3aed" stroke-width="6"/>',
            '  <path d="M 980 366 L 950 348 L 950 384 Z" fill="#7c3aed"/>',
            (
                '  <rect x="810" y="300" width="150" height="52" rx="18" '
                'fill="#ede9fe" stroke="#7c3aed" stroke-width="2"/>'
            ),
            (
                '  <text x="885" y="333" text-anchor="middle" fill="#5b21b6" '
                'font-size="16" font-weight="800" font-family="SFMono-Regular, '
                f'Consolas, Liberation Mono, monospace">gap +{gap}s</text>'
            ),
            '  <rect x="980" y="248" width="680" height="238" rx="24" '
            'fill="#fff1f2" stroke="#be123c" stroke-width="3"/>',
            '  <text x="1020" y="302" fill="#9f1239" font-size="18" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "2 · security_check · witness endpoint</text>",
            (
                '  <text x="1020" y="352" fill="#0f172a" font-size="28" '
                'font-weight="800" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">{security_worst["earliest_start_offset_seconds"]} '
                f"→ {finish}s</text>"
            ),
            (
                '  <text x="1020" y="395" fill="#475569" font-size="16" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">predecessor binds · '
                f"maximum duration {security_duration}s</text>"
            ),
            (
                '  <text x="1020" y="438" fill="#be123c" font-size="16" '
                'font-weight="800" font-family="SFMono-Regular, Consolas, '
                f'Liberation Mono, monospace">deadline {deadline}s · slack '
                f"{slack:+d}s</text>"
            ),
            '  <rect x="104" y="558" width="1592" height="148" rx="22" '
            'fill="#0f172a"/>',
            '  <text x="900" y="610" text-anchor="middle" fill="#cbd5e1" '
            'font-size="16" font-family="Inter, Segoe UI, Arial, sans-serif">'
            "checked max-envelope equation</text>",
            (
                '  <text x="900" y="666" text-anchor="middle" fill="#f8fafc" '
                'font-size="34" font-weight="800" font-family="SFMono-Regular, '
                f'Consolas, Liberation Mono, monospace">{equation}</text>'
            ),
            '  <rect x="104" y="754" width="1592" height="202" rx="22" '
            'fill="#f8fafc" stroke="#cbd5e1" stroke-width="2"/>',
            (
                '  <text x="144" y="810" fill="#334155" font-size="17" '
                'font-weight="800" font-family="Inter, Segoe UI, Arial, '
                f'sans-serif">deadline arithmetic: {deadline} − {finish} = '
                f"{slack:+d} seconds</text>"
            ),
            (
                '  <text x="144" y="858" fill="#475569" font-size="16" '
                'font-family="Inter, Segoe UI, Arial, sans-serif">gate_walk remains '
                f"context: worst finish {gate['worst_case']['earliest_finish_offset_seconds']}s "
                f"equals deadline {gate['hard_deadline_offset_seconds']}s, but its "
                "0s slack is not the minimum.</text>"
            ),
            '  <text x="144" y="910" fill="#64748b" font-size="15" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">Witness selection '
            "follows the lexical binding predecessor encoded in the report; it "
            "does not assign probability or real-world causality.</text>",
        )
    )
    return _svg_finish(lines)


def _workflow_function(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise EvidenceFailure("workflow_symbol_definition_drift")
    return matches[0]


def _workflow_call_count(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        for node in ast.walk(function)
    )


def _workflow_semantic_contract(
    sources: Mapping[str, bytes],
) -> tuple[dict[str, object], ...]:
    try:
        trees = {
            path: ast.parse(sources[path].decode("utf-8"), filename=path)
            for path in WORKFLOW_SOURCE_PATHS
        }
    except (KeyError, SyntaxError, UnicodeDecodeError):
        raise EvidenceFailure("workflow_source_parse_failed") from None

    decode = _workflow_function(trees["tctravel/codec.py"], "decode_itinerary_json")
    compile_graph = _workflow_function(
        trees["tctravel/compiler.py"], "compile_temporal_graph"
    )
    analyze_scenario = _workflow_function(
        trees["tctravel/analysis.py"], "_analyze_scenario"
    )
    analyze = _workflow_function(
        trees["tctravel/analysis.py"], "analyze_interval_feasibility"
    )
    canonical = _workflow_function(
        trees["tctravel/analysis.py"], "canonical_analysis_bytes"
    )
    cli_main = _workflow_function(trees["tctravel/cli.py"], "main")
    decode_calls = _workflow_call_count(cli_main, "decode_itinerary_json")
    analyze_calls = _workflow_call_count(cli_main, "analyze_interval_feasibility")
    compile_calls = _workflow_call_count(analyze, "compile_temporal_graph")
    scenario_calls = _workflow_call_count(analyze, "_analyze_scenario")
    canonical_calls = _workflow_call_count(cli_main, "canonical_analysis_bytes")
    if (
        decode_calls != 1
        or analyze_calls != 1
        or compile_calls != 1
        or scenario_calls != 2
        or canonical_calls != 1
    ):
        raise EvidenceFailure("workflow_symbol_call_count_drift")

    return (
        {
            "call_count": decode_calls,
            "call_path": "tctravel/cli.py",
            "definition_line": decode.lineno,
            "definition_path": "tctravel/codec.py",
            "display_symbol": "decode_itinerary_json",
        },
        {
            "call_count": compile_calls,
            "call_path": "tctravel/analysis.py",
            "definition_line": compile_graph.lineno,
            "definition_path": "tctravel/compiler.py",
            "display_symbol": "compile_temporal_graph",
        },
        {
            "call_count": scenario_calls,
            "call_path": "tctravel/analysis.py",
            "definition_line": analyze_scenario.lineno,
            "definition_path": "tctravel/analysis.py",
            "display_symbol": "_analyze_scenario",
        },
        {
            "call_count": canonical_calls,
            "call_path": "tctravel/cli.py",
            "definition_line": canonical.lineno,
            "definition_path": "tctravel/analysis.py",
            "display_symbol": "canonical_analysis_bytes",
        },
    )


def _workflow_source_records(
    captures: CaptureSet,
) -> tuple[dict[str, object], ...]:
    by_path = {cast(str, record["path"]): record for record in captures.subject_sources}
    try:
        return tuple(by_path[path] for path in WORKFLOW_SOURCE_PATHS)
    except KeyError:
        raise EvidenceFailure("workflow_source_binding_missing") from None


def _workflow_svg(captures: CaptureSet) -> bytes:
    runtime = captures.runtime_contract
    source_records = _workflow_source_records(captures)
    source_records_sha256 = _sha256(_json_bytes(source_records))
    source_payloads = {
        path: _git(
            REPOSITORY_ROOT,
            "show",
            f"{captures.subject_revision}:{path}",
        )
        for path in WORKFLOW_SOURCE_PATHS
    }
    for record in source_records:
        path = cast(str, record["path"])
        if _sha256(source_payloads[path]) != record["sha256"]:
            raise EvidenceFailure("workflow_source_record_drift")
    semantic_contract = _workflow_semantic_contract(source_payloads)
    semantic_contract_sha256 = _sha256(_json_bytes(semantic_contract))
    boxes = (
        (
            "1",
            "Bounded JSON",
            f"≤ {runtime['max_input_bytes']:,} bytes · depth ≤ {runtime['max_json_depth']}",
            "#e0f2fe",
            "#0369a1",
        ),
        (
            "2",
            "decode_itinerary_json",
            "strict types · stable redacted errors",
            "#fef3c7",
            "#a16207",
        ),
        (
            "3",
            "compile_temporal_graph",
            "lexical DAG · canonical graph digest",
            "#ede9fe",
            "#6d28d9",
        ),
        (
            "4",
            "_analyze_scenario × 2",
            "all-minimum + all-maximum max-plus passes",
            "#dcfce7",
            "#15803d",
        ),
        (
            "5",
            "canonical_analysis_bytes",
            f"ASCII JSON + SHA-256 · ≤ {runtime['analysis_output_cap_bytes']:,} bytes",
            "#ffe4e6",
            "#be123c",
        ),
    )
    lines = _svg_open(
        title_id="workflow-title",
        description_id="workflow-desc",
        title="Installed-wheel analysis workflow",
        description=(
            "The bounded contract, deterministic graph compiler, two endpoint "
            "passes, and canonical report encoder exercised by the evidence run."
        ),
        attributes={
            "input-cap-bytes": runtime["max_input_bytes"],
            "output-cap-bytes": runtime["analysis_output_cap_bytes"],
            "source-count": len(source_records),
            "source-records-sha256": source_records_sha256,
            "symbol-contract-sha256": semantic_contract_sha256,
            "subject-revision": captures.subject_revision,
        },
    )
    lines.extend(
        (
            '  <metadata id="workflow-source-bindings" '
            f'data-records-sha256="{source_records_sha256}">',
            *(
                "    <metadata "
                f'data-source-path="{_escape(record["path"])}" '
                f'data-blob-oid="{_escape(record["blob_oid"])}" '
                f'data-source-sha256="{_escape(record["sha256"])}"/>'
                for record in source_records
            ),
            "  </metadata>",
            '  <metadata id="workflow-symbol-contract" '
            f'data-records-sha256="{semantic_contract_sha256}">',
            *(
                "    <metadata "
                f'data-display-symbol="{_escape(record["display_symbol"])}" '
                f'data-definition-path="{_escape(record["definition_path"])}" '
                f'data-definition-line="{_escape(record["definition_line"])}" '
                f'data-call-count="{_escape(record["call_count"])}"'
                + (
                    f' data-call-path="{_escape(record["call_path"])}"'
                    if "call_path" in record
                    else ""
                )
                + "/>"
                for record in semantic_contract
            ),
            "  </metadata>",
        )
    )
    _svg_header(
        lines,
        "Installed-wheel execution architecture",
        "one-way evidence flow · committed Git blobs → isolated CLI → captured bytes",
    )
    y_positions = (208, 374, 540, 706, 872)
    for index, ((step, title, detail, fill, stroke), y) in enumerate(
        zip(boxes, y_positions, strict=True)
    ):
        lines.extend(
            (
                (f'  <g data-step="{step}" data-label="{_escape(title)}">'),
                (
                    f'    <rect x="180" y="{y}" width="1440" height="118" '
                    f'rx="24" fill="{fill}" stroke="{stroke}" stroke-width="3"/>'
                ),
                (f'    <circle cx="244" cy="{y + 59}" r="34" fill="{stroke}"/>'),
                (
                    f'    <text x="244" y="{y + 69}" text-anchor="middle" '
                    'fill="#ffffff" font-size="28" font-weight="900" '
                    f'font-family="Inter, Segoe UI, Arial, sans-serif">{step}</text>'
                ),
                (
                    f'    <text x="310" y="{y + 49}" fill="#0f172a" '
                    'font-size="24" font-weight="800" font-family="SFMono-Regular, '
                    f'Consolas, Liberation Mono, monospace">{_escape(title)}</text>'
                ),
                (
                    f'    <text x="310" y="{y + 82}" fill="#475569" '
                    'font-size="16" font-family="Inter, Segoe UI, Arial, '
                    f'sans-serif">{_escape(detail)}</text>'
                ),
                "  </g>",
            )
        )
        if index < len(boxes) - 1:
            lines.extend(
                (
                    f'  <line x1="900" y1="{y + 118}" x2="900" '
                    f'y2="{y + 151}" stroke="#64748b" stroke-width="5"/>',
                    f'  <path d="M 900 {y + 158} L 885 {y + 137} L 915 '
                    f'{y + 137} Z" fill="#64748b"/>',
                )
            )
    return _svg_finish(lines)


def _execution_record(execution: Execution) -> dict[str, object]:
    observation: dict[str, object]
    if execution.report is not None:
        observation = {
            "analysis_digest": _sha256(execution.stdout[:-1]),
            "best_case_minimum_deadline_slack_seconds": execution.report["best_case"][
                "minimum_deadline_slack_seconds"
            ],
            "status": execution.report["status"],
            "worst_case_minimum_deadline_slack_seconds": execution.report["worst_case"][
                "minimum_deadline_slack_seconds"
            ],
            "worst_case_witness_chain": execution.report["worst_case"]["witness_chain"],
        }
    else:
        observation = {"error_code": execution.error_code}
    return {
        "case_id": execution.case_id,
        "command": list(execution.command),
        "derivation": execution.derivation,
        "exit_code": execution.exit_code,
        "input_kind": execution.input_kind,
        "input_sha256": execution.input_sha256,
        "observation": observation,
        "stderr_bytes": len(execution.stderr),
        "stderr_sha256": _sha256(execution.stderr),
        "stdout_bytes": len(execution.stdout),
        "stdout_sha256": _sha256(execution.stdout),
    }


def _decision_svg(captures: CaptureSet) -> bytes:
    lines = _svg_open(
        title_id="decision-title",
        description_id="decision-desc",
        title="Executed analysis decision matrix",
        description=(
            "Five installed-CLI runs observe robust, duration-sensitive, exact "
            "deadline equality, minimum-bound rejection, and reorder invariance."
        ),
        attributes={"case-count": len(captures.executions)},
    )
    _svg_header(
        lines,
        "Executed analysis decision matrix",
        "installed console script · exact exits · canonical output/error digests",
    )
    lines.extend(
        (
            '  <rect x="48" y="198" width="1704" height="820" rx="22" '
            'fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>',
            '  <text x="82" y="248" fill="#64748b" font-size="13" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, '
            'sans-serif">CASE</text>',
            '  <text x="660" y="248" fill="#64748b" font-size="13" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, '
            'sans-serif">EXIT</text>',
            '  <text x="790" y="248" fill="#64748b" font-size="13" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, '
            'sans-serif">OBSERVED CONTRACT</text>',
            '  <text x="1270" y="248" fill="#64748b" font-size="13" '
            'font-weight="800" font-family="Inter, Segoe UI, Arial, '
            'sans-serif">OUTPUT IDENTITY</text>',
        )
    )
    labels = {
        "robust_fixture": "Committed robust fixture",
        "duration_sensitive_fixture": "Committed tight fixture",
        "exact_deadline_equality": "Exact deadline equality",
        "minimum_bound_rejection": "Minimum-bound rejection",
        "reordered_input_invariance": "Reordered-input invariance",
    }
    for index, execution in enumerate(captures.executions):
        y = 278 + index * 137
        fill = "#f8fafc" if index % 2 == 0 else "#f1f5f9"
        if execution.report is not None:
            observed = cast(str, execution.report["status"])
            identity = f"analysis {_sha256(execution.stdout[:-1])[:24]}…"
            detail = (
                "worst slack "
                f"{execution.report['worst_case']['minimum_deadline_slack_seconds']:+d}s"
            )
        else:
            observed = cast(str, execution.error_code)
            identity = f"stderr {_sha256(execution.stderr)[:24]}…"
            detail = "fail-closed before analysis"
        lines.extend(
            (
                (
                    f'  <g data-case-id="{execution.case_id}" '
                    f'data-exit-code="{execution.exit_code}" '
                    f'data-observation="{_escape(observed)}" '
                    f'data-stdout-sha256="{_sha256(execution.stdout)}" '
                    f'data-stderr-sha256="{_sha256(execution.stderr)}">'
                ),
                (
                    f'    <rect x="68" y="{y}" width="1664" height="112" '
                    f'rx="16" fill="{fill}"/>'
                ),
                (
                    f'    <text x="92" y="{y + 43}" fill="#0f172a" '
                    'font-size="17" font-weight="800" font-family="Inter, '
                    f'Segoe UI, Arial, sans-serif">{labels[execution.case_id]}</text>'
                ),
                (
                    f'    <text x="92" y="{y + 76}" fill="#64748b" '
                    'font-size="12" font-family="SFMono-Regular, Consolas, '
                    f'Liberation Mono, monospace">{execution.case_id}</text>'
                ),
                (
                    f'    <rect x="640" y="{y + 29}" width="82" height="46" '
                    'rx="18" fill="#0f172a"/>'
                ),
                (
                    f'    <text x="681" y="{y + 60}" text-anchor="middle" '
                    'fill="#ffffff" font-size="20" font-weight="900" '
                    'font-family="SFMono-Regular, Consolas, monospace">'
                    f"{execution.exit_code}</text>"
                ),
                (
                    f'    <text x="790" y="{y + 45}" fill="#0f172a" '
                    'font-size="16" font-weight="800" font-family="SFMono-Regular, '
                    f'Consolas, Liberation Mono, monospace">{_escape(observed)}</text>'
                ),
                (
                    f'    <text x="790" y="{y + 76}" fill="#64748b" '
                    'font-size="13" font-family="Inter, Segoe UI, Arial, '
                    f'sans-serif">{_escape(detail)}</text>'
                ),
                (
                    f'    <text x="1270" y="{y + 58}" fill="#334155" '
                    'font-size="13" font-family="SFMono-Regular, Consolas, '
                    f'Liberation Mono, monospace">{_escape(identity)}</text>'
                ),
                "  </g>",
            )
        )
    lines.extend(
        (
            '  <text x="82" y="988" fill="#475569" font-size="14" '
            'font-family="Inter, Segoe UI, Arial, sans-serif">Exit 1 is a valid '
            "duration-sensitive classification with a complete report; exit 3 "
            "is the synthetic minimum-bound rejection.</text>",
        )
    )
    return _svg_finish(lines)


def _demo_run(execution: Execution) -> DemoRunPresentation:
    report = _require_report(execution)
    return DemoRunPresentation(
        case_id=execution.case_id,
        command=execution.command,
        exit_code=execution.exit_code,
        status=cast(str, report["status"]),
        worst_slack_seconds=cast(
            int, report["worst_case"]["minimum_deadline_slack_seconds"]
        ),
        witness_chain=tuple(cast(list[str], report["worst_case"]["witness_chain"])),
        stdout_bytes=len(execution.stdout),
        stdout_sha256=_sha256(execution.stdout),
    )


def _demo_presentation(captures: CaptureSet) -> DemoPresentation:
    robust = _demo_run(_execution(captures, "robust_fixture"))
    tight = _demo_run(_execution(captures, "duration_sensitive_fixture"))
    return DemoPresentation(
        runs=(robust, tight),
        frames=(
            DemoFramePresentation(
                robust.case_id,
                "run",
                "01 / RUN",
                "ROBUST FIXTURE",
                "#67e8f9",
                1200,
            ),
            DemoFramePresentation(
                robust.case_id,
                "observe",
                "02 / OBSERVE",
                "COMPLETE REPORT · EXIT 0",
                "#4ade80",
                2200,
            ),
            DemoFramePresentation(
                tight.case_id,
                "run",
                "03 / RUN",
                "TIGHT FIXTURE",
                "#67e8f9",
                1200,
            ),
            DemoFramePresentation(
                tight.case_id,
                "observe",
                "04 / OBSERVE",
                "VALID DOMAIN RESULT · EXIT 1",
                "#fda4af",
                2800,
            ),
        ),
        loop=0,
        timing_semantics="illustrative",
    )


def _demo_run_by_id(
    presentation: DemoPresentation, case_id: str
) -> DemoRunPresentation:
    try:
        return next(run for run in presentation.runs if run.case_id == case_id)
    except StopIteration:
        raise EvidenceFailure("demo_case_missing") from None


def _demo_frame_lines(
    frame: DemoFramePresentation,
    run: DemoRunPresentation,
) -> tuple[str, ...]:
    if frame.phase == "run":
        return ("$ " + " ".join(run.command),)
    if frame.phase != "observe":
        raise EvidenceFailure("demo_phase_invalid")
    lines = (
        f"status = {run.status}",
        f"worst-case minimum deadline slack = {run.worst_slack_seconds:+d}s",
        "witness chain = " + " -> ".join(run.witness_chain),
        f"stdout = {run.stdout_bytes} bytes / sha256 {run.stdout_sha256}",
    )
    if run.exit_code == 1:
        return (*lines, "exit 1 is a classification, not a crash")
    return lines


def _demo_presentation_record(
    presentation: DemoPresentation,
) -> dict[str, object]:
    return {
        "frames": [
            {
                "accent": frame.accent,
                "case_id": frame.case_id,
                "duration_ms": frame.duration_ms,
                "heading": frame.heading,
                "lines": list(
                    _demo_frame_lines(
                        frame, _demo_run_by_id(presentation, frame.case_id)
                    )
                ),
                "phase": frame.phase,
                "step": frame.step,
            }
            for frame in presentation.frames
        ],
        "loop": presentation.loop,
        "runs": [
            {
                "case_id": run.case_id,
                "command": list(run.command),
                "exit_code": run.exit_code,
                "status": run.status,
                "stdout_bytes": run.stdout_bytes,
                "stdout_sha256": run.stdout_sha256,
                "witness_chain": list(run.witness_chain),
                "worst_slack_seconds": run.worst_slack_seconds,
            }
            for run in presentation.runs
        ],
        "timing_semantics": presentation.timing_semantics,
    }


def _presentation_models(captures: CaptureSet) -> PresentationModels:
    return PresentationModels(
        terminal=_terminal_presentation(captures),
        demo=_demo_presentation(captures),
    )


def _presentation_models_record(
    presentations: PresentationModels,
) -> dict[str, object]:
    return {
        "demo": _demo_presentation_record(presentations.demo),
        "terminal": _terminal_presentation_record(presentations.terminal),
    }


def _draw_demo_frame(
    frame: DemoFramePresentation,
    run: DemoRunPresentation,
) -> Image.Image:
    image = Image.new("RGB", (900, 560), "#07111f")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rounded_rectangle(
        (12, 12, 888, 548),
        radius=16,
        fill="#07111f",
        outline="#334155",
        width=2,
    )
    draw.rectangle((12, 12, 888, 76), fill="#0f172a")
    draw.text((34, 32), frame.step, font=font, fill=frame.accent)
    draw.text((180, 32), frame.heading, font=font, fill="#f8fafc")
    y = 112
    for line in _demo_frame_lines(frame, run):
        for wrapped in textwrap.wrap(line, width=102) or [""]:
            draw.text((38, y), wrapped, font=font, fill="#d1fae5")
            y += 22
        y += 8
    draw.text(
        (38, 516),
        "DETERMINISTIC REPLAY FROM CAPTURED BYTES / TIMING IS ILLUSTRATIVE",
        font=font,
        fill="#94a3b8",
    )
    return image.resize(  # pyright: ignore[reportUnknownMemberType]
        (SVG_WIDTH, SVG_HEIGHT),
        resample=Image.Resampling.NEAREST,
    )


def _demo_gif(presentation: DemoPresentation) -> bytes:
    frames = [
        _draw_demo_frame(frame, _demo_run_by_id(presentation, frame.case_id))
        for frame in presentation.frames
    ]
    palette = frames[0].quantize(
        colors=96,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    )
    quantized = [
        frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames
    ]
    output = io.BytesIO()
    quantized[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=quantized[1:],
        duration=[frame.duration_ms for frame in presentation.frames],
        loop=presentation.loop,
        disposal=2,
        optimize=False,
    )
    return output.getvalue()


def _runs_bytes(captures: CaptureSet) -> bytes:
    return _json_bytes(
        {
            "runs": [_execution_record(execution) for execution in captures.executions],
            "schema_version": 1,
            "subject_revision": captures.subject_revision,
        },
        pretty=True,
    )


def _decoded_rgb_sha256(image: Image.Image) -> str:
    return _sha256(image.convert("RGB").tobytes())


def _artifact_contract(path: str, payload: bytes) -> dict[str, object]:
    derived_from: list[str]
    claim: str
    media_type: str
    presentation: dict[str, object] = {}
    if path.endswith(".svg"):
        media_type = "image/svg+xml"
        presentation = {
            "height": SVG_HEIGHT,
            "view_box": f"0 0 {SVG_WIDTH} {SVG_HEIGHT}",
            "width": SVG_WIDTH,
        }
    elif path.endswith(".png"):
        media_type = "image/png"
        with Image.open(io.BytesIO(payload)) as image:
            presentation = {
                "decoded_rgb_sha256": _decoded_rgb_sha256(image),
                "height": image.height,
                "mode": image.mode,
                "width": image.width,
            }
    elif path.endswith(".gif"):
        media_type = "image/gif"
        with Image.open(io.BytesIO(payload)) as image:
            durations: list[int] = []
            frame_digests: list[str] = []
            frame_count = cast(int, getattr(image, "n_frames", 1))
            for frame_index in range(frame_count):
                image.seek(frame_index)
                durations.append(cast(int, image.info.get("duration", 0)))
                frame_digests.append(_decoded_rgb_sha256(image))
            presentation = {
                "decoded_rgb_frame_sha256": frame_digests,
                "frame_count": frame_count,
                "frame_durations_ms": durations,
                "height": image.height,
                "loop": cast(int, image.info.get("loop", 0)),
                "timing_semantics": "illustrative",
                "width": image.width,
            }
    elif path.endswith(".json"):
        media_type = "application/json"
    elif path.endswith(".txt"):
        media_type = "text/plain"
    else:
        raise EvidenceFailure("artifact_media_type_unknown")

    if path in (RAW_ROBUST_STDOUT, RAW_ROBUST_STDERR):
        derived_from = ["robust_fixture"]
        claim = "Exact installed-console bytes for the committed robust fixture."
    elif path in (RAW_TIGHT_STDOUT, RAW_TIGHT_STDERR):
        derived_from = ["duration_sensitive_fixture"]
        claim = (
            "Exact installed-console bytes for the committed duration-sensitive "
            "fixture."
        )
    elif path == RUNS_OUTPUT:
        derived_from = [
            "robust_fixture",
            "duration_sensitive_fixture",
            "exact_deadline_equality",
            "minimum_bound_rejection",
            "reordered_input_invariance",
        ]
        claim = "Canonical run receipts with symbolic argv, exits, sizes, and hashes."
    elif path in (TERMINAL_SVG_OUTPUT, TERMINAL_PNG_OUTPUT):
        derived_from = ["duration_sensitive_fixture"]
        claim = (
            "Complete tight-run stdout rendered from exact captured ASCII bytes; "
            "line wrapping is presentational."
        )
    elif path == ENVELOPE_OUTPUT:
        derived_from = ["robust_fixture", "duration_sensitive_fixture"]
        claim = "Executed best/worst earliest-time envelopes against hard deadlines."
    elif path == SLACK_OUTPUT:
        derived_from = ["robust_fixture", "duration_sensitive_fixture"]
        claim = "Executed best-to-worst slack values on one shared signed scale."
    elif path == CHAIN_OUTPUT:
        derived_from = ["duration_sensitive_fixture"]
        claim = "Worst-case lexical binding-predecessor witness and checked equation."
    elif path == WORKFLOW_OUTPUT:
        derived_from = [
            "runtime_contract",
            *(f"subject:{path}" for path in WORKFLOW_SOURCE_PATHS),
        ]
        claim = (
            "Installed-wheel stages and observed public bounds, with architecture "
            "labels bound to exact subject source blobs embedded in the SVG."
        )
    elif path == DECISION_OUTPUT:
        derived_from = [
            "robust_fixture",
            "duration_sensitive_fixture",
            "exact_deadline_equality",
            "minimum_bound_rejection",
            "reordered_input_invariance",
        ]
        claim = "Five executed CLI decisions with exact exits and output identities."
    elif path == DEMO_OUTPUT:
        derived_from = ["robust_fixture", "duration_sensitive_fixture"]
        claim = (
            "Deterministic replay of captured run records; frame timing is explicitly "
            "illustrative."
        )
    else:
        raise EvidenceFailure("artifact_claim_missing")

    return {
        "bytes": len(payload),
        "claim": claim,
        "derived_from": derived_from,
        "media_type": media_type,
        "path": path,
        "presentation": presentation,
        "sha256": _sha256(payload),
    }


def _manifest_bytes(
    captures: CaptureSet,
    outputs: Mapping[str, bytes],
    presentations: PresentationModels,
    renderer_contract: Mapping[str, object],
) -> bytes:
    robust = _require_report(_execution(captures, "robust_fixture"))
    tight = _require_report(_execution(captures, "duration_sensitive_fixture"))
    manifest = {
        "artifacts": [
            _artifact_contract(path, outputs[path]) for path in sorted(outputs)
        ],
        "check_command": CHECK_COMMAND,
        "evidence": {
            "robust": {
                "analysis_digest": _sha256(
                    _execution(captures, "robust_fixture").stdout[:-1]
                ),
                "minimum_worst_case_slack_seconds": robust["worst_case"][
                    "minimum_deadline_slack_seconds"
                ],
                "status": robust["status"],
                "witness_chain": robust["worst_case"]["witness_chain"],
            },
            "duration_sensitive": {
                "analysis_digest": _sha256(
                    _execution(captures, "duration_sensitive_fixture").stdout[:-1]
                ),
                "minimum_worst_case_slack_seconds": tight["worst_case"][
                    "minimum_deadline_slack_seconds"
                ],
                "status": tight["status"],
                "witness_chain": tight["worst_case"]["witness_chain"],
            },
            "runs": [_execution_record(execution) for execution in captures.executions],
        },
        "install_contract": captures.install_contract,
        "limitations": [
            "Synthetic fixtures are contract examples, not live travel observations.",
            "Worst case means only all declared maximum durations, fixed transfer "
            "minima, and the deterministic earliest-start policy.",
            "Duration intervals are bounds, not calibrated probability distributions.",
            "Digests identify bytes; they do not prove input truth or real-world safety.",
            "GIF frame timing is editorial and carries no execution-latency claim.",
        ],
        "pipeline": {
            "dependency_versions": EXPECTED_PIPELINE_DISTRIBUTIONS,
            "revision": captures.pipeline_revision,
            "sources": list(captures.pipeline_sources),
        },
        "presentation_models": _presentation_models_record(presentations),
        "renderer_contract": dict(renderer_contract),
        "runtime_contract": captures.runtime_contract,
        "schema_version": 1,
        "subject": {
            "revision": captures.subject_revision,
            "sources": list(captures.subject_sources),
            "tree": captures.subject_tree,
            "wheel_contract": captures.wheel_contract,
        },
        "write_command": WRITE_COMMAND,
    }
    return _json_bytes(manifest, pretty=True)


def build_bundle(root: Path, subject_revision: str) -> dict[str, bytes]:
    """Build the complete evidence bundle in memory from one Git subject."""

    captures = _capture_subject(root, subject_revision)
    renderer_contract = _renderer_contract()
    if (
        subject_revision == DEFAULT_SUBJECT_REVISION
        and captures.subject_tree != DEFAULT_SUBJECT_TREE
    ):
        raise EvidenceFailure("default_subject_tree_drift")
    robust = _execution(captures, "robust_fixture")
    tight = _execution(captures, "duration_sensitive_fixture")
    presentations = _presentation_models(captures)
    outputs = {
        RAW_ROBUST_STDOUT: robust.stdout,
        RAW_ROBUST_STDERR: robust.stderr,
        RAW_TIGHT_STDOUT: tight.stdout,
        RAW_TIGHT_STDERR: tight.stderr,
        RUNS_OUTPUT: _runs_bytes(captures),
        TERMINAL_SVG_OUTPUT: _terminal_svg(presentations.terminal),
        TERMINAL_PNG_OUTPUT: _terminal_png(presentations.terminal),
        ENVELOPE_OUTPUT: _envelope_svg(captures),
        SLACK_OUTPUT: _slack_svg(captures),
        CHAIN_OUTPUT: _chain_svg(captures),
        WORKFLOW_OUTPUT: _workflow_svg(captures),
        DECISION_OUTPUT: _decision_svg(captures),
        DEMO_OUTPUT: _demo_gif(presentations.demo),
    }
    forbidden_tokens = (b"/home/", b"/Users/", b"ubuntu", b"AWS_SECRET")
    for path, payload in outputs.items():
        if any(token in payload for token in forbidden_tokens):
            raise EvidenceFailure(f"artifact_contains_environment_detail:{path}")
    manifest = _manifest_bytes(
        captures,
        outputs,
        presentations,
        renderer_contract,
    )
    return {**outputs, MANIFEST_OUTPUT: manifest}


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _bundle_files(root: Path) -> set[str]:
    evidence_root = root / EVIDENCE_ROOT
    if not evidence_root.exists():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }


def _write_bundle(root: Path, bundle: Mapping[str, bytes]) -> None:
    for path, payload in sorted(bundle.items()):
        _atomic_write(root / path, payload)
    unexpected = _bundle_files(root) - set(bundle)
    if unexpected:
        raise EvidenceFailure(
            "unexpected_evidence_artifacts:" + ",".join(sorted(unexpected))
        )


def _check_bundle(root: Path, bundle: Mapping[str, bytes]) -> None:
    observed_files = _bundle_files(root)
    expected_files = set(bundle)
    if observed_files != expected_files:
        raise EvidenceFailure("evidence_bundle_membership_drift")
    mismatches = [
        path
        for path, expected in sorted(bundle.items())
        if (root / path).read_bytes() != expected
    ]
    if mismatches:
        raise EvidenceFailure("evidence_artifact_drift:" + ",".join(mismatches))


def _manifest_subject(root: Path) -> str:
    manifest_path = root / MANIFEST_OUTPUT
    if not manifest_path.is_file():
        raise EvidenceFailure("evidence_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_bytes())
        revision = manifest["subject"]["revision"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise EvidenceFailure("evidence_manifest_invalid") from None
    if type(revision) is not str or len(revision) != 40:
        raise EvidenceFailure("evidence_subject_invalid")
    return revision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture or verify installed-wheel analysis evidence."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument(
        "--subject",
        default=None,
        help="full subject commit for --write (defaults to the reviewed analyzer)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.check and args.subject is not None:
            raise EvidenceFailure("subject_argument_only_valid_for_write")
        if args.write:
            _require_clean_worktree(REPOSITORY_ROOT)
            subject = args.subject or DEFAULT_SUBJECT_REVISION
        else:
            subject = _manifest_subject(REPOSITORY_ROOT)
        bundle = build_bundle(REPOSITORY_ROOT, subject)
        if args.write:
            _write_bundle(REPOSITORY_ROOT, bundle)
            action = "wrote"
        else:
            _check_bundle(REPOSITORY_ROOT, bundle)
            action = "verified"
        visual_count = sum(path.endswith((".svg", ".png", ".gif")) for path in bundle)
        print(
            "TCTravel analysis evidence: PASS "
            f"({action} {visual_count} visuals + raw runs + manifest)"
        )
        return 0
    except EvidenceFailure as exc:
        print(
            f"TCTravel analysis evidence: FAIL code={exc}",
            file=sys.stderr,
        )
        return 1
    except Exception:  # noqa: BLE001
        print(
            "TCTravel analysis evidence: FAIL code=internal_error",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
