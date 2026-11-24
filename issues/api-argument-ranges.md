---
status: unshaped
kind: feature
appetite: small
target: 2.10.0
lane: public
---

# The Python API does not hold explicit arguments to the numeric ranges

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`bounded-inputs` (2.9.0) gives every numeric setting one range and one parser, and holds every
option, configuration key and `HYPERSHELL_*` variable to it. Its GOAL gate (C3, maintainer,
2026-11-24) kept the Python API's explicit arguments as they are: a default the API takes from
configuration goes through the range check, but a value a caller passes (`num_threads=0`,
`bundlesize=0`, `evict_after=10**30`, `max_retries=100000`) is used as given, as before. The ranges
are documented, not enforced. An out-of-range argument can still reach the database's column types or
a thread wait, where it fails late or hangs, which is what the table exists to prevent for every other
way in.

## Outcome sought

Each public entry point (`hypershell.server`, `hypershell.client`, `hypershell.cluster`,
`hypershell.submit`, the relay) checks each parameter that mirrors a setting against that setting's
range through `bounded-inputs`' parsers, and raises `ValueError` naming the parameter, the range and
the value before it starts work. In-range arguments behave as today.

## Notes

- Refusing an argument accepted today is an interface change, hence a minor release.
- Decide at shaping which parameters mirror a setting (some take Python objects, not numbers), and
  whether a `None` that means "from configuration" stays accepted everywhere.
- Related: `spec/bounded-inputs/` (the table and parsers, GOAL C3).
