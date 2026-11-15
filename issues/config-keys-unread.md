---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Some documented configuration keys are never read by the command they describe

> **Candidate, not a contract.** This file records deferred work in enough detail that a future
> session does not have to re-derive it. It is **not** graded by `hs-review` and must never be
> copied into `spec/{slug}/GOAL.md` verbatim — `/hs-feature` promotes it, and that is where
> appetite, non-goals and the R-IDs get negotiated with a human. The `status:` field above is the
> guard.

## Problem

`env-config-keys`' research (`spec/env-config-keys/research/03-duration-keys.md`,
`04-typed-readers.md`) found three keys that the parameter reference documents but that a command
ignores, from a file and from the environment alike. Each was read on `develop` `90ab2fc2`:

- **`server.eager`.** `ServerApp` and `ClusterApp` set the class attribute `eager_mode` from
  `config.server.eager` and then register `--eager` with `action='store_true', dest='eager_mode'`
  (`src/hypershell/server.py:1766-1769`, `src/hypershell/cluster/__init__.py:162-165`). The option's
  default (False) is what the parsed namespace carries, so the key never takes effect (probed:
  `HYPERSHELL_SERVER_EAGER=true` gives `eager_mode == False` in both apps).
- **`server.evict` under `hs cluster`.** `hs cluster` passes no eviction period, and
  `RemoteCluster`/`AutoScalingCluster` default to `DEFAULT_EVICT = default.server.evict`
  (`src/hypershell/server.py:738`, `src/hypershell/cluster/remote.py:963`), the built-in value, so a
  configured `evict` is ignored there; `hs server` and `hs relay` read it.
- **`client.ratelimit` under `hs cluster`.** `hs cluster -R` defaults to `config.task.get('ratelimit')`
  (`src/hypershell/cluster/__init__.py:240`), a key the reference does not document, not
  `client.ratelimit`.

**Pre-existing** on `develop`; not introduced by `env-config-keys`, which only makes these keys
reachable from the environment by their documented names.

## Why it was deferred

Out of `env-config-keys`' scope: its GOAL is how a variable reaches a key and how the value is typed,
not which command reads which key. Each fix changes a command's behaviour for users who set the key
and saw nothing happen, so it deserves its own release-note line.

## Outcome / vision

Every key the parameter reference documents is read by the commands it names, or the reference says
which commands ignore it.

## Sketch of the acceptance criteria

- **R1** — WHEN `server.eager` is true in configuration and `--eager` is not given, `hs server` and
  `hs cluster` SHALL schedule failed tasks before new ones.
- **R2** — WHEN `server.evict` is configured, `hs cluster` SHALL use it as `hs server` does.
- **R3** — `hs cluster -R` SHALL default to `client.ratelimit`.

## Notes

- Related: [`client-bundlesize-config-key.md`](client-bundlesize-config-key.md) (the same class
  for `client.bundlesize`/`bundlewait`, already filed); `spec/env-config-keys/` (found there).
- Found by: `env-config-keys` `hs-plan` research.
