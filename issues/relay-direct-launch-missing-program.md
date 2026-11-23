---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# A relay with no launcher exits when its client program is missing

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A relay with no `--launcher` starts each site client directly. If the client program does not exist
on the relay host, `Popen` raises `FileNotFoundError` inside the site scaler. The relay logs CRITICAL
and exits, rather than the launch failing the way a launched process that exits non-zero does (a
warning, then relaunch or `failed_launches` accounting). Seen at plan time of
`relay-client-exe-deprecated-name` (2026-11-23), on `develop`, with a relay started from a `bin/`
holding only `hs` and `hsx`: the old `hyper-shell` default made the scaler die this way.

That cycle removes the trigger it hit (the default is now the relay's own program), but an explicit
`--remote-exe` or `relay.remote_exe` naming a missing program still ends the relay the same way.

## Outcome sought

A direct launch whose program cannot be started counts as a failed launch, like a launched process
that exits at once. It is logged with the program name and the error, it goes into the same
failed-launch accounting, and the relay keeps serving its other zones.

## Notes

- Check `hs cluster` with no launcher for the same shape (`cluster/remote.py` direct launches).
- Related: `spec/relay-client-exe-deprecated-name/` (the cycle that found it).
