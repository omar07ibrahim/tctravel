# Contributing

TCTravel is built around deterministic contracts and source-bound evidence.
Changes should preserve those properties rather than hand-editing outputs.

## Development setup

Runtime code supports Python 3.11 and newer. Canonical raster evidence is
pinned to CPython 3.12.3 on Linux x86_64.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --no-deps -r requirements-ci.txt
.venv/bin/python -m pip install --require-hashes --no-deps -r requirements-evidence.txt
```

Run the same gates as CI:

```bash
.venv/bin/python -m pyright --project pyrightconfig.ci.json
.venv/bin/python -m ruff check . --select E4,E7,E9,F63,F7,F82 --per-file-ignores "tools/generate_visuals.py:E402"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/generate_analysis_evidence.py --check
.venv/bin/python tools/generate_visuals.py --check
.venv/bin/python tools/verify_legacy.py --check
```

## Evidence rules

- Use only original synthetic fixtures; never copy legacy prose, contact data,
  embedded media, or third-party marks into tests or documentation.
- Visuals must be generated from executed code or captured installed CLI
  output, include honest scope labels, and remain byte-reproducible.
- Do not hand-edit files under `docs/**/generated/` or raw receipts.
- A change to a pinned subject or pipeline source requires an intentional
  evidence re-baseline, review of every changed artifact, and a matching
  manifest update.
- Keep CLI stdout canonical, stderr redacted, and failure codes stable.
- Never commit secrets, local paths, hostnames, private itineraries, or
  personal data.

Keep each commit focused and make the pull-request description identify the
contract, evidence, and compatibility effects.
