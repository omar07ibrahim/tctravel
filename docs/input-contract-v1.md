# Itinerary input contract v1

Version 1 is an original, deterministic contract for compiling time-critical
itineraries into temporal dependency graphs. It is intentionally smaller than
a routing or reliability model: it defines timing constraints and their
dependencies, but it does not choose a route, estimate a probability, or claim
that a duration interval is calibrated.

## Input shape

The top-level JSON object has exactly these fields:

| Field | Type | Semantics |
| --- | --- | --- |
| `schema_version` | integer | Must be exactly `1`. |
| `itinerary_id` | string | Matches `[a-z][a-z0-9_-]{0,63}`. |
| `anchor_time_utc` | string | Canonical whole-second UTC timestamp, `YYYY-MM-DDTHH:MM:SSZ`. |
| `horizon_seconds` | integer | Relative planning horizon, from 1 second through 31 days. |
| `activities` | array | Between 1 and 128 activity objects. |
| `transfers` | array | Between 0 and 512 transfer objects. |

Each activity has exactly:

```json
{
  "duration": {
    "maximum_seconds": 900,
    "minimum_seconds": 600
  },
  "hard_deadline_offset_seconds": 1800,
  "id": "local_shuttle",
  "release_offset_seconds": 0
}
```

`release_offset_seconds` is the earliest permitted start, relative to the
anchor. `duration` is a closed interval of possible whole-second durations;
both bounds are positive and the minimum cannot exceed the maximum.
`hard_deadline_offset_seconds` is an absolute completion constraint on the
same relative timeline. It is not a target or a percentile. Releases are
between zero and the horizon; both duration bounds and the deadline are between
one second and the horizon. Activity IDs use the same syntax as
`itinerary_id` and must be unique.

Each transfer has exactly:

```json
{
  "from_activity_id": "local_shuttle",
  "minimum_seconds": 120,
  "to_activity_id": "security_check"
}
```

The successor cannot start before the predecessor finishes plus the nonnegative
transfer gap. Transfers are directed, unique by endpoint pair, and must
reference declared activities. Self-transfers and dependency cycles are
rejected. A transfer gap is between zero and the horizon.

The complete original synthetic example is
[`examples/synthetic_connection.v1.json`](../examples/synthetic_connection.v1.json).

## Graph semantics

Compilation creates one anchor node and a start/finish node pair for every
activity. It emits four kinds of temporal constraint:

For each directed edge, the bounds constrain
`target_time - source_time`. A missing lower or upper bound means that side is
unbounded by that edge.

| Edge kind | Source → target | Bounds |
| --- | --- | --- |
| `release` | anchor → activity start | lower = release offset |
| `duration` | activity start → activity finish | lower/upper = duration interval |
| `hard_deadline` | anchor → activity finish | upper = deadline offset |
| `transfer` | predecessor finish → successor start | lower = transfer gap |

Topological activity ordering uses lexical identifiers as its only tie-break.
Node order follows that activity order; edge order follows node position and a
stable constraint-kind ordering. Equivalent inputs with reordered object keys,
activities, or transfers therefore compile to identical bytes.

`TemporalGraph` is a result-only model. Obtain it from
`compile_temporal_graph()`; manually assembled graph instances are not accepted
as validated contract inputs.

The validator propagates only lower duration bounds and minimum transfer gaps.
It rejects an activity when even this earliest possible completion misses its
hard deadline. Passing that check does **not** mean every duration in the
declared intervals will meet every deadline. Worst-case analysis, stochastic
calibration, scenario simulation, route selection, and live travel data are
outside contract v1.

## Canonical bytes and digests

Decoded activities and transfers are order-normalized. Canonical itinerary and
graph encoders use sorted JSON object keys, compact separators, ASCII output,
and no trailing newline. SHA-256 digests are lowercase hexadecimal over those
exact bytes.

```python
from pathlib import Path

from tctravel import (
    canonical_graph_bytes,
    compile_temporal_graph,
    decode_itinerary_json,
    graph_digest,
    itinerary_digest,
)

itinerary = decode_itinerary_json(
    Path("examples/synthetic_connection.v1.json").read_bytes()
)
graph = compile_temporal_graph(itinerary)

print(itinerary_digest(itinerary))
print(graph_digest(graph))
print(canonical_graph_bytes(graph).decode("ascii"))
```

Digests identify canonical contract and compiler output. They do not certify
source truth, travel availability, safety, or predictive quality.

## Fail-closed boundary

The decoder accepts only UTF-8 JSON at or below 64 KiB and scans nesting before
parsing. Nesting deeper than 24 containers is rejected. Floating-point values,
non-finite values, oversized integer tokens, duplicate JSON keys, unknown or
missing fields, invalid types, and out-of-range values fail closed. JSON
booleans are never accepted as integers.

All decoder and itinerary-validation failures use `ContractError.code`, one of
the stable values in `ErrorCode`. The exception string and `as_dict()` payload
contain only that code. They never include submitted identifiers, submitted
values, JSON locations, or filesystem paths. Internal parser exceptions are
detached rather than retained as exception context.

| Code | Rejected condition |
| --- | --- |
| `input_too_large` | UTF-8 representation exceeds 64 KiB. |
| `input_too_deep` | JSON container nesting exceeds 24. |
| `invalid_utf8` / `invalid_json` | Text or JSON syntax is not accepted. |
| `duplicate_json_key` | Any object repeats a key. |
| `unknown_field` / `missing_field` | An object differs from its exact field set. |
| `invalid_type` / `out_of_range` | A value has the wrong JSON type or numeric bounds. |
| `invalid_identifier` / `invalid_timestamp` | A string violates its canonical syntax. |
| `duplicate_activity_id` / `duplicate_transfer` | A semantic identity or transfer pair repeats. |
| `self_transfer` / `unknown_transfer_reference` | A transfer is not a declared cross-activity dependency. |
| `dependency_cycle` | Transfer dependencies are cyclic. |
| `infeasible_deadline` | Minimum durations and handoffs already miss a hard deadline. |
