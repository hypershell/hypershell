---
status: unshaped
kind: feature
appetite: small
target: 2.9.x
lane: public
---

# Warn on an unrecognized configuration key or section

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and the R-IDs get negotiated with a human. The `status:` field above is the
> guard.

## Problem

A misspelled key in a configuration file is silently ignored. `[server] attempt = 3`
(for `attempts`), `[clinet] bundlesize = 10`, or `[relay] advertize = ...` loads without
complaint, and the command runs with the default. The user sees a setting that "doesn't take", with
nothing to say why. The same applies to a `HYPERSHELL_*` variable whose name matches no key in a
closed table: it is held as written and never read.

Since #113, `src/hypershell/core/config_schema.py` declares every documented key (`KEYS`), every
table and how a name past it is read (`TABLES`: `CLOSED`, `OPEN`, `ZONE`, `EXPORT`), the zone keys
(`ZONE_KEYS`), the `_env`/`_eval` forms (`VARIANTS`) and the process variables
(`PROCESS_VARIABLES`). That is enough to tell a declared key from an unknown one without a second
list.

## Outcome sought

- A key or table in a configuration file (system, user or local) that the schema does not declare,
  under a `CLOSED` table or at the root, logs one warning naming the key, the file it came from and,
  when there is one, the closest declared key ("did you mean `server.attempts`?").
- A `HYPERSHELL_*` variable that names no declared key under a closed table warns the same way,
  naming the variable. `PROCESS_VARIABLES` are exempt.
- The command still runs: an unknown key warns and is otherwise ignored, as today.
- No warning for `OPEN` tables (`database` provider arguments, `database.connect_args`,
  `database.pragmas`, `ssh.nodelist`), `[export]`, `[relay.zone.NAME]` entries that use
  `ZONE_KEYS`, a key's `_env`/`_eval` forms, or the scalar forms in `SCALAR_TABLES`.
- `hs config get` reports an unknown key "not found" (`src/hypershell/config.py`). `hs config set`
  writes one without a word, reading the value as untyped (`match_path`, `read_value`). Shaping
  decides whether `set` warns the same way before it writes.

## Non-goals

- A strict mode that refuses unknown keys. That changes behaviour for existing files, so it is a
  minor-release interface change (2.10.0 at the earliest), not a patch.
- Validating values. Value ranges are `issues/bounded-inputs.md`.

## Notes

- Warn rather than refuse, so it is safe in a 2.9.x patch: a file that worked still works.
- A file read by both 2.8 and 2.9 hosts may carry keys only one release knows. A key that 2.9
  removed or renamed should name its replacement rather than read as a typo.
- One warning per key per process. Commands that reload the configuration must not repeat it.
- Found by: maintainer question, 2026-11-22, after #113 gave the schema a full key table.
