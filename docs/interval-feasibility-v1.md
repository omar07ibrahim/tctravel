# Interval-feasibility analysis v1

Analysis schema version 1 determines whether a validated itinerary remains
deadline-feasible across its declared duration intervals. It is a separate
result contract built on [itinerary contract v1](input-contract-v1.md) and
temporal graph v1; it does not change either of those schemas.

The analyzer evaluates a deterministic earliest-start policy with fixed
minimum transfer gaps. It does not estimate a probability, calibrate a travel
duration, choose a route, or account for additional waiting or disruption.

## Public API

```python
from pathlib import Path

from tctravel import decode_itinerary_json
from tctravel.analysis import (
    analysis_digest,
    analyze_interval_feasibility,
    canonical_analysis_bytes,
)

itinerary = decode_itinerary_json(
    Path("examples/synthetic_connection.v1.json").read_bytes()
)
report = analyze_interval_feasibility(itinerary)

print(report.status.value)
print(report.worst_case.minimum_deadline_slack_seconds)
print(analysis_digest(report))
print(canonical_analysis_bytes(report).decode("ascii"))
```

`analyze_interval_feasibility()` accepts an `Itinerary`. It invokes the
existing compiler, so manually constructed models cross the same semantic
validation boundary as decoded models. The function performs no filesystem or
network access.

## Earliest-time envelope

For an activity `v`, let:

- `r(v)` be its release offset;
- `D(v)` be its hard completion deadline;
- `d_min(v)` and `d_max(v)` be its duration bounds;
- `P(v)` be its incoming transfer predecessors; and
- `g(p,v)` be the declared minimum transfer gap from `p` to `v`.

The analyzer runs the same forward recurrence twice in the compiler's lexical
topological activity order. For scenario `s`, `d_s` is `d_min` in the best
case and `d_max` in the worst case:

```text
ES_s(v) = max(r(v), max(EF_s(p) + g(p,v) for p in P(v)))
EF_s(v) = ES_s(v) + d_s(v)
slack_s(v) = D(v) - EF_s(v)
```

When `P(v)` is empty, `ES_s(v) = r(v)`. All values are integer offsets in
seconds from the itinerary anchor.

The constraints are monotone in activity durations. Consequently, the
all-minimum pass is the componentwise earliest envelope and the all-maximum
pass is the componentwise latest earliest-start envelope over every
whole-second duration combination in the declared rectangular intervals. The
input validator already rejects an itinerary if its all-minimum pass misses a
deadline.

Transfer values remain lower-bound constraints. The recurrence starts every
activity as soon as its release and predecessor constraints permit; it does
not introduce voluntary waiting or an unbounded real-world transfer delay.

## Status and slack

An activity's status is determined by its worst-case deadline slack:

| Status | Exact condition | Meaning within the model |
| --- | --- | --- |
| `robust` | `worst_case.deadline_slack_seconds >= 0` | Every modeled duration combination meets this activity's deadline under the earliest-start policy. |
| `duration_sensitive` | `worst_case.deadline_slack_seconds < 0` | The valid all-minimum schedule meets the deadline, but the all-maximum envelope misses it. |

Deadline equality is therefore `robust`. Negative slack is the number of
seconds by which an earliest finish exceeds its deadline. This is deadline
margin, not CPM total float or free float.

The overall report is `robust` exactly when the minimum worst-case slack across
all activities is nonnegative. Otherwise it is `duration_sensitive`. There is
no analysis status named `infeasible`: an all-minimum miss is rejected earlier
as `ContractError` code `infeasible_deadline`.

## Binding constraints and witness chains

Each timing record exposes the constraints that determine its earliest start:

- `release_is_binding` is true when the release equals the selected earliest
  start;
- `binding_predecessor_ids` contains every predecessor whose finish plus gap
  equals that start, in lexical predecessor-ID order; and
- `witness_predecessor_id` is the first binding predecessor, or `null` when no
  predecessor binds.

If a release and one or more transfers tie, both kinds of binding information
are retained and the witness follows the first lexical transfer predecessor.

For each scenario, `critical_activity_ids` contains every activity tied for
minimum deadline slack, sorted lexically. `witness_activity_id` is the first of
those IDs. `witness_chain` recursively follows each selected witness
predecessor and includes the witness activity itself, from root to target.
This is one deterministic binding-predecessor explanation; it is not a CPM
critical path and does not imply causal or probabilistic importance.

## Canonical report fields

`IntervalFeasibilityReport` serializes these top-level fields:

| Field | Meaning |
| --- | --- |
| `analysis_schema_version` | Exactly `1`. |
| `contract_schema_version` | Contract version copied from the compiled graph. |
| `graph_schema_version` | Temporal graph version used by the analyzer. |
| `itinerary_digest` | SHA-256 identity of canonical itinerary bytes. |
| `graph_digest` | SHA-256 identity of canonical graph bytes. |
| `anchor_time_utc` | Original canonical UTC anchor. |
| `horizon_seconds` | Declared input horizon; it does not clamp derived worst-case offsets. |
| `status` | Overall `robust` or `duration_sensitive` classification. |
| `best_case` / `worst_case` | Scenario summaries described below. |
| `activities` | Activity records in deterministic graph activity order. |

Each activity record contains:

| Field | Meaning |
| --- | --- |
| `activity_id` | Validated activity identifier. |
| `release_offset_seconds` | Earliest permitted start. |
| `duration_interval_seconds.minimum` / `.maximum` | Declared duration endpoints. |
| `hard_deadline_offset_seconds` | Hard completion deadline. |
| `status` | Per-activity classification from worst-case slack. |
| `best_case` / `worst_case` | `ActivityTiming` documents. |

Each `ActivityTiming` document contains
`earliest_start_offset_seconds`, `earliest_finish_offset_seconds`,
`deadline_slack_seconds`, `release_is_binding`,
`binding_predecessor_ids`, and `witness_predecessor_id`.

Each scenario summary contains:

- `minimum_deadline_slack_seconds`;
- `critical_activity_ids`;
- `witness_activity_id`; and
- `witness_chain`.

`canonical_analysis_bytes()` emits ASCII JSON with sorted object keys, compact
separators, and no trailing newline. `analysis_digest()` is lowercase SHA-256
over exactly those bytes. The digest identifies deterministic analyzer output;
it does not establish source truth, real-world safety, or predictive quality.

## Executed synthetic examples

[`synthetic_connection.v1.json`](../examples/synthetic_connection.v1.json)
produces `robust`, with 900 seconds of minimum worst-case slack and
`local_shuttle` as its worst-case witness.

[`synthetic_tight_connection.v1.json`](../examples/synthetic_tight_connection.v1.json)
produces `duration_sensitive`. Its minimum worst-case slack is -120 seconds at
`security_check`, with witness chain `local_shuttle -> security_check`.

Both fixtures are original synthetic inputs. They are examples of contract
behavior, not observations of a live journey.

## Claim boundary

The analysis covers only the declared duration intervals, releases, hard
deadlines, transfer minima, and dependency DAG. It assumes that any
whole-second duration in each interval can be combined with any duration in
the other intervals. It assigns no likelihood or correlation to those
combinations.

It does not perform route search, optimization, scenario simulation,
stochastic calibration, live-data lookup, booking, or recommendation. A
`robust` result is therefore conditional on this model and earliest-start
policy; it is not a guarantee about a real trip.
