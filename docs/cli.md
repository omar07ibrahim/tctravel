# Interval-feasibility CLI

TCTravel exposes the same interval-feasibility analysis through an installed
console script and a Python module entry point:

```text
tctravel-analyze [INPUT|-]
python3 -m tctravel [INPUT|-]
```

The package requires Python 3.11 or newer and has no runtime dependencies
outside the standard library. From a repository checkout, install the current
source with:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

No package-index publication is implied by these commands.

## Input selection

Pass one regular-file path as `INPUT`. Omit the argument or pass `-` to read
binary JSON from standard input:

```bash
tctravel-analyze examples/synthetic_connection.v1.json
python3 -m tctravel - < examples/synthetic_connection.v1.json
```

`--help` prints static usage text. It is the only accepted option; additional
arguments and other option-like values are rejected as `invalid_invocation`.

Input is capped at 65,536 bytes (64 KiB). Standard input is read incrementally
up to that boundary plus one detection byte. A path must identify a regular
file: missing paths, directories, symbolic links, and special files such as
FIFOs are rejected. The reader checks the opened file's identity, size, and
metadata around the bounded read and rejects observed in-place mutation.

An oversized stream or regular file is a contract rejection with code
`input_too_large`, not an input-availability failure. JSON and semantic limits
are defined by [itinerary contract v1](input-contract-v1.md).

## Successful output

For either analysis status, stdout is the complete
`canonical_analysis_bytes(report)` followed by exactly one newline. A normal
successful write leaves stderr empty. The rendered report, including that
newline, is capped at 1,048,576 bytes (1 MiB).

The report contract and interpretation are documented in
[interval-feasibility analysis v1](interval-feasibility-v1.md).

The two valid analysis outcomes deliberately use different process statuses:

```bash
tctravel-analyze examples/synthetic_connection.v1.json > robust.json
echo $?  # 0: robust

tctravel-analyze examples/synthetic_tight_connection.v1.json > tight.json
echo $?  # 1: duration_sensitive; tight.json is still a complete report
```

Exit 1 is a modeled result, not a malformed-input or internal-error signal.
Shell scripts using `set -e`, `&&`, or pipelines should account for it.

## Exit codes

| Code | Name | Output contract |
| ---: | --- | --- |
| `0` | `ROBUST` | A complete `robust` report on stdout, or static help for `--help`. |
| `1` | `DURATION_SENSITIVE` | A complete `duration_sensitive` report on stdout. |
| `2` | `INVALID_INVOCATION` | `{"error":{"code":"invalid_invocation"}}` on stderr. |
| `3` | `CONTRACT_REJECTED` | The existing redacted `ContractError.as_dict()` record on stderr. |
| `4` | `INPUT_UNAVAILABLE` | `{"error":{"code":"input_unavailable"}}` on stderr. |
| `5` | `INTERNAL_ERROR` | An attempted `{"error":{"code":"internal_error"}}` diagnostic on stderr. |

Every emitted JSON record is compact, sorted-key ASCII JSON followed by one
newline. For ordinary invocation, input, and contract failures, stdout is not
written.

Exit 5 also covers a failed or short output write. If the operating system
fails a stream after accepting a prefix, that stream can contain partial
bytes; the process status is authoritative. If stderr itself is unwritable, a
diagnostic cannot be guaranteed.

## Error and privacy boundary

Contract diagnostics expose only a stable `ErrorCode`. Invocation,
input-availability, and unexpected internal failures likewise emit fixed
codes; submitted JSON, submitted identifiers, local paths, and exception text
are not included in those diagnostics.

Redaction applies to failures. A successful analysis report intentionally
contains validated activity identifiers and timing values from the submitted
itinerary, so callers must treat stdout according to their own data policy.

The CLI reads only the selected path or standard input and performs no network
access. Its output is deterministic for identical accepted input and package
version, subject to the canonicalization rules in the linked contracts.

## Reproduce the behavior

From the repository root:

```bash
python3 -m unittest tests.test_cli tests.test_packaging -v
```

The packaging test builds a wheel without consulting a package index, checks
its contents and console-script metadata, installs it into an isolated test
environment, and exercises both `tctravel-analyze` and `python -m tctravel`
outside the source tree.
