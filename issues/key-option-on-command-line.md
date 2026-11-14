---
status: unshaped
kind: fix
appetite: small
lane: public
---

# On the 2.8 line, the queue key can still be given on a command line without a warning

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Since 2.9.0a5 and 2.8.5, `hs cluster` hands its run key to the clients it launches in their
environment, never on a command line, where a process listing shows it to every user on the host.
On 2.9, `key-option-refused` (#104) closed the rest: `-k`/`--auth` on `hs client`, `hs server` and
`hs submit -q` is refused with a usage error that names `HYPERSHELL_SERVER_AUTH` and `server.auth`,
and no help text, man page, completion or tutorial offers it.

The 2.8 line still takes the option silently, as of 2.8.7 (`master`, `f0e498b`):

- `hs client -k KEY` (`src/hypershell/client.py:1527`)
- `hs server -k KEY` (`src/hypershell/server.py:1418`)
- `hs submit -q -k KEY` (`src/hypershell/submit.py:942`)

A persistent `hs server` and the `hs client` processes that join it hold the key on their command
lines for as long as they run, and the 2.8 help, usage lines, man pages, completions and docs all
show the option, so that is what a reader copies. Each command already takes the key without it,
from `HYPERSHELL_SERVER_AUTH` or `server.auth` in a configuration file.

## Why it was deferred

The seed held a 2.8 half (warn) and a 2.9 half (refuse). `key-option-refused` (#104) took the 2.9
half only; its GOAL left the 2.8 line out because `master` is the maintainer's, with a backport, if
wanted, as its own cycle against that line. Whether and when to ship it is the maintainer's call.
**Pre-existing** on the 2.8 line.

## Outcome / vision

On 2.8.x, the option keeps working, for compatibility, and says loudly each time that it should not
be used and what to use instead. The 2.8 docs mark it as deprecated and show the key given through
the environment or the configuration.

## Sketch of the acceptance criteria

- **R1** — WHEN `-k` or `--auth` is given to `hs client`, `hs server` or `hs submit` on 2.8.x, the
  command SHALL log a warning that the key is visible on its command line, and that
  `HYPERSHELL_SERVER_AUTH` or `server.auth` is the way to give it, and SHALL then run as before.
- **R2** — The warning SHALL never contain the key.
- **R3** — The 2.8 usage lines, help text, man pages, shell completions and documentation SHALL mark
  the option as deprecated, and every example SHALL give the key through the environment or the
  configuration.
- **R4** — Tests SHALL cover the warning on each of the three commands, and a key from each
  remaining source.

## Notes

- **The maintainer's direction** (2026-11-11): for 2.8.x keep `-k` for compatibility but warn
  loudly not to use it; for 2.9.x do not allow `-k` at all. The 2.9 half shipped as #104.
- This is a hotfix against `master` (a `hotfix/2.8.x` branch), not work on `develop`.
- `src/hypershell/cluster/launch.py:122-123` on `master` redacts `-k` and `--auth` from logged
  client commands; it stays on 2.8, where the option stays.
- The Python API's `auth=` parameters are not a command line and are not in scope.
- Related: `spec/key-option-refused/` (#104: the 2.9 refusal, its GOAL *Non-goals* and
  `META.md` F2 on the split), `spec/server-auth-variant-collision/` (#97, also in 2.8.7: `auth_env`
  and `auth_eval` in a configuration file, the other way to keep a key out of a file),
  [`env-config-key-mapping.md`](env-config-key-mapping.md) (which configuration keys an environment
  variable can reach).
- Found by: the maintainer, reading the 2.9 release notes (2026-11-11); narrowed to the 2.8 half when
  `key-option-refused` landed (2026-11-14).
