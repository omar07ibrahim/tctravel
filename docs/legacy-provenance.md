# Legacy provenance boundary

## Decision

The repository begins with eight generated HTML documents and one Nicepage
project-state document from commit
`87ac060312e0f00149732503f8bc305dda9b6cb2`. Their origin, authorship chain,
media licenses, and permission to republish contact information are not
established by repository evidence.

These nine files are therefore preserved as a quarantined historical snapshot,
not treated as portfolio output and not used as source material for the planned
TCTravel implementation.

## What is attested

`legacy.manifest.json` binds:

- the exact nine-path root inventory;
- each file's byte length and SHA-256 digest;
- Nicepage application version `4.14.1`, trial state, and the recorded
  unpublished-state fields;
- three unique embedded JPEG payload digests and the fact that each occurs
  twice in the same generator-state field; and
- redacted risk counts, including one repeated inline SVG third-party mark.

JPEG payloads are decoded only in memory long enough to validate base64,
measure bytes, and compute SHA-256. They are never extracted, rendered, copied
into documentation, or printed. The repeated SVG is counted as a third-party
provenance risk; its bytes and visual content are not reproduced.

## Reuse policy

Until provenance and rights are established independently, do not reuse:

- inherited prose, names, destinations, offers, or marketing claims;
- contact values or contact-bearing fragments;
- embedded JPEG or SVG media;
- generator branding or other third-party marks; or
- layout, styling, and generated page fragments as a basis for the new system.

The planned compiler, fixtures, examples, and visuals must be created from
original or separately licensed inputs. Test fixtures must not contain
obfuscated copies of blocked material.

## Redacted verification

Run:

```bash
python3 tools/verify_legacy.py --check
python3 -m unittest discover -s tests -v
```

The verifier deliberately emits only pass/fail state and aggregate risk
counts. Failure output is a stable category code, never a path, source phrase,
contact value, data URI, decoded payload, or local environment detail.

Resource use is part of the fail-closed contract. The verifier caps the
manifest at 64 KiB, each legacy file at 1 MiB, the complete legacy snapshot at
2 MiB, an encoded media payload at 512 KiB, and a decoded payload at 256 KiB.
It checks filesystem sizes before reading and repeats the check on the opened
file descriptor.

The tests prove fail-closed behavior for a modified file, a missing file, an
additional root legacy HTML file (including a case-variant suffix), manifest
schema drift, generator metadata drift, oversized resources, and inline-SVG
count drift after the outer file checksum is deliberately rebased in a
temporary copy.

## Limits of the attestation

This mechanism detects repository drift against the recorded snapshot. It does
not establish ownership, copyright permission, factual accuracy, historical
authenticity, absence of personal data, or fitness for publication. A matching
checksum means only that the local bytes match this manifest.
