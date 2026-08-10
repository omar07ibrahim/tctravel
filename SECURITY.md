# Security policy

## Supported state

Security fixes target the default branch and the maintained `0.3.x` source
state. No GitHub release archive is currently published because the
quarantined legacy snapshot has unresolved redistribution rights.

## Reporting a vulnerability

Use
[GitHub private vulnerability reporting](https://github.com/omar07ibrahim/tctravel/security/advisories/new).
Do not open a public issue for an undisclosed vulnerability and do not include
credentials, personal data, private itineraries, or live travel records in a
report.

A useful report includes the affected public API or CLI path, a minimal
synthetic reproducer, the observed stable error or output, the expected
boundary, and the Python version. Replace real names, locations, account data,
and booking details with synthetic values.

## Security boundaries

TCTravel accepts bounded local JSON and emits deterministic local reports. It
does not contact travel providers, execute bookings, ingest live feeds, or
claim real-world safety. Report digests identify canonical bytes; they are not
signatures or attestations that submitted durations are true.

The inherited HTML and Nicepage state are an untrusted historical quarantine,
not application input or portfolio media. Their verification path must remain
fail-closed and must not print contact values, embedded assets, or source
phrases.
