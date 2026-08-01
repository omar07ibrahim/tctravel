"""Bounded command-line interface for interval-feasibility analysis."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Sequence
from enum import IntEnum
from typing import BinaryIO, Final

from .analysis import (
    FeasibilityStatus,
    analyze_interval_feasibility,
    canonical_analysis_bytes,
)
from .codec import MAX_INPUT_BYTES, decode_itinerary_json
from .errors import ContractError, ErrorCode

MAX_ANALYSIS_OUTPUT_BYTES: Final = 1024 * 1024
_READ_CHUNK_BYTES: Final = 16 * 1024
_USAGE: Final = (
    b"usage: tctravel-analyze [INPUT|-]\n"
    b"Analyze a contract-v1 itinerary; omitted INPUT and '-' read stdin.\n"
)


class CliExitCode(IntEnum):
    """Stable process statuses for shell and automation consumers."""

    ROBUST = 0
    DURATION_SENSITIVE = 1
    INVALID_INVOCATION = 2
    CONTRACT_REJECTED = 3
    INPUT_UNAVAILABLE = 4
    INTERNAL_ERROR = 5


class _InputUnavailable(Exception):
    pass


def _canonical_line(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def _read_stream_bounded(stream: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_INPUT_BYTES:
        remaining = MAX_INPUT_BYTES + 1 - size
        try:
            chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
        except (OSError, ValueError):
            raise _InputUnavailable from None
        if type(chunk) is not bytes:
            raise _InputUnavailable
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_INPUT_BYTES:
        raise ContractError(ErrorCode.INPUT_TOO_LARGE)
    return payload


def _read_path_bounded(path: str) -> bytes:
    try:
        before = os.lstat(path)
    except (OSError, ValueError):
        raise _InputUnavailable from None
    if not stat.S_ISREG(before.st_mode):
        raise _InputUnavailable
    if before.st_size > MAX_INPUT_BYTES:
        raise ContractError(ErrorCode.INPUT_TOO_LARGE)

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        raise _InputUnavailable from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _InputUnavailable
        if opened.st_size > MAX_INPUT_BYTES:
            raise ContractError(ErrorCode.INPUT_TOO_LARGE)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = _read_stream_bounded(stream)
            after = os.fstat(stream.fileno())
            if after.st_size > MAX_INPUT_BYTES:
                raise ContractError(ErrorCode.INPUT_TOO_LARGE)
            opened_identity = (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if opened_identity != after_identity or len(payload) != opened.st_size:
                raise _InputUnavailable
            return payload
    except ContractError:
        raise
    except _InputUnavailable:
        raise
    except OSError:
        raise _InputUnavailable from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write(stream: BinaryIO, payload: bytes) -> None:
    written = stream.write(payload)
    if written != len(payload):
        raise OSError("short write")
    stream.flush()


def _try_write(stream: BinaryIO, payload: bytes) -> bool:
    try:
        _write(stream, payload)
    # Output adapters can raise arbitrary library exceptions. This boundary
    # deliberately converts all of them into one redacted process status.
    except Exception:  # noqa: BLE001
        return False
    return True


def _emit_internal_error(stream: BinaryIO) -> int:
    _try_write(
        stream,
        _canonical_line({"error": {"code": "internal_error"}}),
    )
    return int(CliExitCode.INTERNAL_ERROR)


def _emit_error(
    stream: BinaryIO,
    payload: object,
    status: CliExitCode,
) -> int:
    if not _try_write(stream, _canonical_line(payload)):
        return int(CliExitCode.INTERNAL_ERROR)
    return int(status)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> int:
    """Analyze one bounded input without echoing paths or submitted values."""

    args = list(sys.argv[1:] if argv is None else argv)
    input_stream = sys.stdin.buffer if stdin is None else stdin
    output_stream = sys.stdout.buffer if stdout is None else stdout
    error_stream = sys.stderr.buffer if stderr is None else stderr

    if args == ["--help"]:
        if not _try_write(output_stream, _USAGE):
            return _emit_internal_error(error_stream)
        return int(CliExitCode.ROBUST)
    if len(args) > 1 or (args and args[0].startswith("-") and args[0] != "-"):
        return _emit_error(
            error_stream,
            {"error": {"code": "invalid_invocation"}},
            CliExitCode.INVALID_INVOCATION,
        )

    try:
        payload = (
            _read_stream_bounded(input_stream)
            if not args or args[0] == "-"
            else _read_path_bounded(args[0])
        )
        itinerary = decode_itinerary_json(payload)
        report = analyze_interval_feasibility(itinerary)
        rendered = canonical_analysis_bytes(report) + b"\n"
        if len(rendered) > MAX_ANALYSIS_OUTPUT_BYTES:
            raise RuntimeError("bounded report exceeded public output cap")
        if not _try_write(output_stream, rendered):
            return _emit_internal_error(error_stream)
        return int(
            CliExitCode.ROBUST
            if report.status is FeasibilityStatus.ROBUST
            else CliExitCode.DURATION_SENSITIVE
        )
    except ContractError as exc:
        return _emit_error(
            error_stream,
            exc.as_dict(),
            CliExitCode.CONTRACT_REJECTED,
        )
    except _InputUnavailable:
        return _emit_error(
            error_stream,
            {"error": {"code": "input_unavailable"}},
            CliExitCode.INPUT_UNAVAILABLE,
        )
    # The process boundary owns a single redacted fallback for unexpected
    # implementation failures; exception details never cross it.
    except Exception:  # noqa: BLE001
        return _emit_internal_error(error_stream)
