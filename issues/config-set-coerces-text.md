---
status: unshaped
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# `hs config set` writes text that looks like a number, boolean or null as that type

> **Candidate, not a contract.** This file records deferred work in enough detail that a future
> session does not have to re-derive it. It is **not** graded by `hs-review` and must never be
> copied into `spec/{slug}/GOAL.md` verbatim — `/hs-feature` promotes it, and that is where
> appetite, non-goals and the R-IDs get negotiated with a human. The `status:` field above is the
> guard.

## Problem

`hs config set` passes its VALUE argument through `smart_coerce`
(`src/hypershell/config.py:284`, `src/hypershell/core/types.py:36`) before writing it to the TOML
file, whatever the key. `true`/`false` in any case become booleans, anything `int()` or `float()`
accepts becomes a number, and `null`/`none` become `None`, which tomlkit cannot write: the command
fails with `ConvertError` and a traceback file (exit 5). So a key that takes text cannot be given
those texts from this command (probed on `develop` `90ab2fc2`, `--local` in a throwaway site; line
numbers as of `fix/env-config-keys`):

- `hs config set autoscale.launcher false` writes `launcher = false`, the boolean, not the command
  `false`.
- `hs config set server.auth 0012345678901234` writes the integer `12345678901234`, which
  `hs server` then refuses as shorter than 16 characters (`resolve_auth` stringifies it,
  `src/hypershell/core/config.py:458`).
- `hs config set export.RUN_ID 007` writes `RUN_ID = 7`, and a task sees `RUN_ID=7`.

A key that takes a number or boolean gets no check either: `hs config set server.eager no` writes the
text `"no"`, which every reader treats as true. This is the defect class that `env-config-keys`
fixes for `HYPERSHELL_*` variables, on another surface. **Pre-existing** on `develop` and the 2.8
line.

## Why it was deferred

Out of scope for `env-config-keys` by its GOAL's Non-goals ("`hs config set`'s own typing"): that
cycle changes how the environment layer is read, not what `hs config set` writes. Its key table
(the documented keys and their declared types) is the natural reader for this fix, so this should
follow it.

## Outcome / vision

`hs config set KEY VALUE` writes VALUE with the type the key declares: text stays text, a number or
boolean is checked and converted (an error naming the key and the form expected when it does not
convert), and a key with no type of its own follows the same rule the environment layer uses for it.

## Sketch of the acceptance criteria

- **R1** — WHEN `hs config set` is given a key that takes text, the file SHALL hold VALUE as written
  (including `false`, `true`, `null`, an empty value and digits with leading zeros).
- **R2** — WHEN it is given a key that takes a whole number, a number or a boolean, the file SHALL
  hold the converted value; IF VALUE does not convert THEN the command SHALL exit with a
  configuration error naming the key and the form expected, and leave the file unchanged.
- **R3** — WHEN it is given an open-table entry or an undocumented key, VALUE SHALL follow the
  environment layer's rule for keys with no type of their own.

## Notes

- Related: `spec/env-config-keys/` (the key table and typed reader this would reuse; its GOAL's
  Non-goals name this follow-up).
- Found by: `env-config-keys` shaping (GOAL Non-goals), filed at `hs-plan`.
