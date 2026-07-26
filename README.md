# TCTravel — Time-Critical Travel Compiler

TCTravel is being rebuilt as an original, non-NLP reliability system for
time-critical travel plans. The intended core will compile hard timing
constraints, transfer dependencies, and uncertainty budgets into an auditable
execution plan. It will answer a narrow engineering question: **is this plan
still feasible, and which dependency fails first if conditions change?**

This repository does not have that executable core yet. The current commit is
only a fail-closed attestation layer around nine inherited Nicepage artifacts.
It makes the starting state measurable without presenting the inherited site as
new work.

## Current state

| Capability | State in this commit |
| --- | --- |
| Exact legacy inventory, byte sizes, and SHA-256 checks | Implemented |
| Nicepage 4.14.1 / trial / unpublished-state checks | Implemented |
| Embedded JPEG duplicate-map verification | Implemented |
| Redacted contact, prose, media, and third-party-mark risk counts | Implemented |
| Constraint compiler, temporal graph, reliability model, CLI, or API | Planned |
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

## Planned engineering direction

The next implementation slice will start independently of the inherited site:

1. compile a versioned journey specification into a temporal dependency graph;
2. propagate bounded delay distributions through transfers and hard deadlines;
3. produce deterministic feasibility decisions and machine-readable failure
   explanations;
4. replay disruption scenarios and compare robust plans against simple
   shortest-duration baselines; and
5. generate every published diagram, chart, CLI capture, and demo from reviewed
   source inputs.

No booking, live-routing, recommendation, or predictive-quality claim is made
at this stage.

## Legacy quarantine

The inherited HTML and `site.json` remain solely as an attested historical
snapshot. Their prose, contact details, embedded media, and third-party marks
are blocked from reuse in the new product, documentation, fixtures, demos, and
visuals unless provenance and rights are established independently.

See [the legacy provenance note](docs/legacy-provenance.md) and
[`legacy.manifest.json`](legacy.manifest.json) for the exact boundary.
