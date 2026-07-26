# TCTravel — Time-Critical Travel Compiler

TCTravel is an original, non-NLP reliability system for time-critical travel
plans. Its first executable core converts an untrusted, versioned itinerary
into a deterministic temporal dependency graph: hard completion deadlines,
bounded activity durations, earliest starts, and transfer dependencies become
explicit machine-readable constraints.

This slice is deliberately narrow. It establishes trustworthy input and graph
semantics before route search, simulation, or statistical calibration are
added. The inherited Nicepage snapshot remains quarantined behind an
independent fail-closed attestation layer.

## Current state

| Capability | State in this commit |
| --- | --- |
| Exact legacy inventory, byte sizes, and SHA-256 checks | Implemented |
| Nicepage 4.14.1 / trial / unpublished-state checks | Implemented |
| Embedded JPEG duplicate-map verification | Implemented |
| Redacted contact, prose, media, and third-party-mark risk counts | Implemented |
| Strict version-1 itinerary JSON decoder | Implemented |
| Immutable itinerary and temporal graph domain models | Implemented |
| Deterministic dependency DAG compilation and canonical SHA-256 | Implemented |
| Duplicate, reference, cycle, deadline, and resource-bound validation | Implemented |
| Reliability model, routing, simulation, CLI, or API | Planned |
| Product screenshots, diagrams, charts, or demos | Not yet available |

No visual is included merely to make the repository look complete. Real,
reproducible architecture diagrams, CLI captures, failure traces, and result
plots will arrive with the executable core that generates them.

## Reproduce the attestation

The verifier and tests use only the Python standard library and make no network
requests.

```bash
python3 tools/verify_legacy.py --check
python3 -m unittest discover -s tests -v
```

A successful verifier run reports counts only:

```text
TCTravel legacy attestation: PASS
snapshot: legacy_files=9 legacy_bytes=740710
risk: legacy_prose_documents=8
risk: visible_contact_surface_documents=1
risk: visible_email_like_occurrences=1
risk: visible_phone_like_occurrences=2
risk: embedded_jpeg_occurrences=6
risk: embedded_jpeg_unique_payloads=3
risk: third_party_mark_occurrences=6
risk: third_party_mark_unique_payloads=1
```

The verifier intentionally never prints inherited prose, contact values, image
bytes, or filesystem paths. It rejects a missing or additional legacy HTML
file, byte drift, manifest schema drift, generator-state drift, a changed JPEG
duplicate map, and changed third-party-mark counts.

## Compile the synthetic contract

The example is original synthetic data. Decoding and graph compilation make no
network or filesystem calls; reading the file is explicit at the call site.

```python
from pathlib import Path

from tctravel import (
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
print(graph.activity_order)
print(graph_digest(graph))
```

Equivalent activity, transfer, and JSON-key orderings produce identical
canonical bytes and digests. Public errors contain stable codes rather than
submitted values or local paths. See
[the version-1 contract](docs/input-contract-v1.md) for the exact schema,
resource limits, constraint edges, and claim boundaries.

## Engineering direction

The implementation proceeds independently of the inherited site:

1. compile a strict versioned itinerary into a temporal dependency graph;
2. add separately evaluated disruption scenarios and uncertainty models;
3. produce auditable feasibility decisions and machine-readable failure
   explanations;
4. replay disruption scenarios and compare robust plans against simple
   shortest-duration baselines; and
5. generate every published diagram, chart, CLI capture, and demo from reviewed
   source inputs.

No booking, live-routing, recommendation, stochastic calibration, or
predictive-quality claim is made at this stage. A compiled graph records
constraints; it does not prove that all declared durations meet every deadline.

## Legacy quarantine

The inherited HTML and `site.json` remain solely as an attested historical
snapshot. Their prose, contact details, embedded media, and third-party marks
are blocked from reuse in the new product, documentation, fixtures, demos, and
visuals unless provenance and rights are established independently.

See [the legacy provenance note](docs/legacy-provenance.md) and
[`legacy.manifest.json`](legacy.manifest.json) for the exact boundary.
