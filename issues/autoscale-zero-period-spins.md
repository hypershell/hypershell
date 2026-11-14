---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A zero autoscaling period makes the autoscaler loop without waiting

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`-P`/`--period` (`hs cluster` and `hs server` with `--autoscaling`) and `autoscale.period` accept
`0`. The autoscaler turns the period into `wait_check = timedelta(seconds=period)`
(`src/hypershell/cluster/remote.py:430` on `develop`, `f6a1d62`), and its `WAIT` state goes to
`CHECK` once `waited > wait_check` (`:492-503`); it sleeps one second only when it does not. With a
period of 0 the comparison is true at once on every pass, so the state machine goes from `WAIT` to
`CHECK` and back without ever sleeping. Each `CHECK` cleans up exited clients and reads the
database to size every zone, so the autoscaler spins a core and keeps the database busy for the
whole run.

Reproduced on `develop` (`f6a1d62`), 2026-11-14: in a throwaway site,
`hs cluster in.txt --no-tls --launcher=<a script that sleeps> --autoscaling fixed -I 1 -P 0` at
debug level logged 654 `Autoscale check` lines in 4 s; the same run with `-P 2` logged one.

A relay's site autoscaler takes the same `autoscale.period` (`src/hypershell/relay/site.py:85-88`,
`:134`), so a relay inherits the loop.

**Pre-existing** in `develop` and on the 2.8 line, where `-P` is a bare integer and the same
comparison stands. `duration-units-remaining` (#106) made the option read units and refuse a sign,
and kept `0` valid.

## Direction

Either refuse a period below some floor (at least one second) with a usage error naming the option
or the key, or treat 0 as the smallest allowed wait. Whichever is chosen, `hs relay`'s use of the
key should follow it.

## Related

- `spec/duration-units-remaining/` (the parser behind `-P` and `autoscale.period`).
- Found by: `cluster-launch-failures` planning (a pre-existing problem noted for the foreman),
  2026-11-14; verified on `develop` before filing.
