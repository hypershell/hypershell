---
status: unshaped
kind: test
appetite: small
target: 2.9.0
lane: public
---

# The relay site-key test fails when a scanned process exits mid-read

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`tests/test_relay_workload.py::test_site_key_reaches_site_clients_in_their_environment_only`
failed once on CI (Python 3.14, 2026-11-15, on the head of #107) with
`psutil.AccessDenied: (pid=…)`, and passed on a re-run with no change.

The test polls `scan_site_key`, which reads `cmdline()` and `environ()` of every process the relay
launched and skips a process that is gone (`NoSuchProcess`, `ZombieProcess`). On Linux, a process
that is exiting can refuse the read of its environment with a permission error before it becomes a
zombie, so psutil raises `AccessDenied` and the scan, and the test, fail. `scan_site` in the same
file has the same `except`. The scan is documented as counting "environments that could be read",
and the test already retries until it has seen enough readable ones, so a process whose environment
cannot be read is the same case as one that withholds it.

## Direction

Skip `psutil.AccessDenied` in both scans, as an environment that could not be read. Check the other
`environ()` reads in the file (`site_clients` callers) for the same race.

## Related

- Found by: CI on #107, 2026-11-15; re-run green.
