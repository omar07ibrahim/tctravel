# Reproducible visual evidence

TCTravel's current visuals are deterministic evidence artifacts for the
version-1 contract core. They are generated from original synthetic input and
the public package API; they are not designed screenshots, product mockups, or
claims about behavior that the current code does not implement.

## Reproduce and verify

Run the read-only verification command from the repository root:

```bash
python3 tools/generate_visuals.py --check
```

Run `python3 tools/generate_visuals.py` only when intentionally regenerating
the artifacts after a reviewed source change. Generation and verification use
the Python standard library and make no network requests.

[`docs/visuals/manifest.json`](visuals/manifest.json) records:

- byte size and SHA-256 for the synthetic fixture, generator, and each public
  package module used by the generator;
- byte size and SHA-256 for each generated SVG;
- the complete compiled edge ledger, activity order, node/edge counts, and
  canonical itinerary/graph digests;
- every observed accepted/rejected boundary probe; and
- the public resource constants and complete public error-code inventory read
  during generation.

There is no generation timestamp, hostname, absolute path, external asset, or
network-derived value, so identical reviewed sources reproduce identical
bytes.

## Compiled temporal DAG

![Exact compiled temporal DAG with release, duration, deadline, and transfer constraints](visuals/generated/temporal-dag.svg)

*Caption — exact compiled DAG.* The generator decodes
[`examples/synthetic_connection.v1.json`](../examples/synthetic_connection.v1.json)
with `decode_itinerary_json()`, compiles it with
`compile_temporal_graph()`, and renders every resulting node and edge. The
diagram visibly separates release lower bounds, bounded durations, hard
deadline upper bounds, and minimum transfer gaps. Its constraint ledger carries
the exact source/target node IDs and bounds from all eleven `TemporalEdge`
objects. Full canonical SHA-256 digests remain visible and machine-readable.

## Executed contract-boundary matrix

![Executed fail-closed contract-boundary matrix](visuals/generated/contract-boundary-matrix.svg)

*Caption — executed contract-boundary matrix.* The generator submits nine
representative synthetic probes to the public decoder and compiler. Two
accepted probes demonstrate baseline decoding and order-independent canonical
digests. Seven rejected probes exercise unknown fields, duplicate keys, JSON
type strictness, dependency cycles, infeasible minimum completion, maximum
nesting, and maximum input size. The displayed status and code are the observed
result of execution; rejection labels come from `ContractError.code`, not from
copied prose.

The resource-bound cards read the exported `MAX_INPUT_BYTES`,
`MAX_JSON_DEPTH`, `MAX_ACTIVITIES`, `MAX_TRANSFERS`, and
`MAX_HORIZON_SECONDS` constants at generation time.

## Claim and privacy boundary

The artifacts use only the committed synthetic fixture. They do not read or
reuse the quarantined Nicepage HTML, metadata, prose, contact values, or
embedded media. Tests reject host paths, common personal-contact markers,
external SVG resources, and byte drift.

The visuals prove deterministic contract execution for these reviewed probes.
They do **not** demonstrate route search, live travel availability, booking,
worst-case deadline safety, reliability calibration, scenario simulation, or
prediction. Those capabilities require separate implementations and separately
generated evidence.
