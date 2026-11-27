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

`bounded-inputs` (#125) gives every numeric setting one range and one parser, and holds every
option, configuration key and `HYPERSHELL_*` variable to it. Its GOAL gate (C3, maintainer,
2026-11-24) kept the Python API's explicit arguments as they are: a default the API takes from
configuration goes through the range check, but a value a caller passes (`bundlesize=0` to
`ServerThread` or `ClientThread`, `max_retries=100000` to `ServerThread`) is used as given, as
before. The ranges are documented, not enforced. An out-of-range argument can still reach the
database's column types or a thread wait, where it fails late or hangs, which is what the table
exists to prevent for every other way in.

One argument is already held to its range: `HeartMonitor`'s `evict_after` (so `ServerThread`'s too)
raises `RuntimeError` with `server.evict`'s refusal outside 10 s to 2,147,483,647 s
(`evict_after=10**30` included), by the plan-gate ruling of `bounded-inputs`. Not every argument
out of an option's everyday use is out of range: `num_threads=0` is in range, one thread per core.

## Outcome sought

Each public entry point (`hypershell.server`, `hypershell.client`, `hypershell.cluster`,
`hypershell.submit`, the relay) checks each parameter that mirrors a setting against that setting's
range through `bounded-inputs`' parsers, and raises `ValueError` naming the parameter, the range and
the value before it starts work. In-range arguments behave as today.

## Notes

- Refusing an argument accepted today is an interface change, hence a minor release.
- Decide at shaping which parameters mirror a setting (some take Python objects, not numbers), and
  whether a `None` that means "from configuration" stays accepted everywhere.
- Decide at shaping whether the refusal is `ValueError` everywhere, or `RuntimeError` as
  `HeartMonitor`'s `evict_after` raises today.
- Related: `spec/bounded-inputs/` (the table and parsers, GOAL C3).
