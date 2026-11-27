# GOAL — One display rule for task timestamps on every backend

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** timestamp-display-offsets
- **kind:** fix
- **appetite:** small
- **target:** 2.9.0 (moved from 2.9.x by the maintainer, 2026-11-27, so the output change lands in
  the release rather than a patch)

## Problem

`hs info` and `hs list` print a task's timestamps one way on PostgreSQL and another on SQLite.
PostgreSQL prints the time with an offset, in the server session's time zone
(`2026-01-01 09:00:00-05:00`; under `PGTZ=UTC`, `2026-01-01 14:00:00+00:00`). SQLite prints no
offset (`2026-01-01 09:00:00`). The instant agrees; the display does not. Even in CI, where both
zones are UTC, the two differ by the `+00:00` suffix. Every stamp shows the split today, `submitted:`
included. On PostgreSQL the shown wall time follows the database server's session zone, not the
reader's, so a reader in one zone can see times in another without being told why.

Display builds on `to_json_type` (`src/hypershell/core/types.py`), which is `value.isoformat(sep=' ')`
of whatever the backend returns: SQLite returns a naive value (the writer's local wall time, its
offset dropped on store), PostgreSQL an aware value in its session zone. `to_json_type` and
`Task.to_json()` are also the wire format (server, client, relay store), so the display cannot be
fixed there.

Found by `update-typed-columns`' plan research (`spec/update-typed-columns/research/03-datetime.md`
§2) and deferred from its plan gate (Q1, 2026-11-24). **Pre-existing.**

## Outcome / vision

A reader sees every task timestamp in their own local time zone, with its offset, the same on SQLite
and PostgreSQL and in every output format: `2026-01-01 09:00:00-05:00`. Under `TZ=UTC` the same task
reads `2026-01-01 14:00:00+00:00` on both backends.

## Acceptance criteria (the contract)

- **R1** — `hs info` and `hs list` SHALL show each task timestamp (`submit_time`, `schedule_time`,
  `start_time`, `completion_time`) as ISO 8601 with a space separator, in the reading process's local
  time zone, with its UTC offset (`YYYY-MM-DD HH:MM:SS[.ffffff]±HH:MM`), identically on SQLite and
  PostgreSQL whatever the PostgreSQL session time zone.
- **R2** — The rule SHALL apply in every output format: `hs info` in its normal, card, json and yaml
  formats and with `-x FIELD`; `hs list` in its table, plain, normal, card, json and csv
  formats; and the completion time `hs wait` logs.
- **R3** — WHEN the reader's time zone is set by `TZ`, the display SHALL follow it.
- **R4** — A stamp stored without an offset (SQLite) SHALL be read as local wall time, the time its
  writer recorded, and shown with the reader's local offset for that instant.
- **R5** — An empty timestamp SHALL show exactly as it does today.
- **R6** — The stored values and the wire format SHALL NOT change: `to_json_type`, `Task.to_json()`
  and everything the server, clients and relays exchange stay byte-for-byte as before.
- **R7** — The user docs for `hs info` and `hs list` SHALL state the rule, under a
  `versionchanged:: 2.9.0` marker.

## Non-goals (no-gos)

- The precision of a shown time (microseconds appear when present, as today).
- How `hs update`, `--where`, `--after`/`--before` or any other option *parses* a time.
- Client, heartbeat or other non-task tables, and log record timestamps in general.
- Converting or migrating stored values; storing SQLite stamps with an offset.
- A configuration key or option for the display zone or format.

## Clarifications

- **Q:** Which display rule? — **A:** The reader's local zone with its offset, in every format. It is
  unambiguous across a daylight-saving change, round-trips into `hs update`'s ISO 8601 grammar, keeps
  json and csv output machine-readable, and matches what a PostgreSQL site in the local zone shows
  today. Chosen by the foreman under the maintainer's go to run this cycle before 2.9.0 (2026-11-27);
  the alternative (local zone, no offset) was rejected because it is ambiguous in the repeated hour.
- **Q:** R2 named a toml format for `hs info`; it has none (`-f` takes normal, card, json, yaml). —
  **A:** Dropped from R2; no other change (foreman, 2026-11-27, at build).
- **Q:** Release? — **A:** 2.9.0, ahead of the release, so the output change is not made in a patch
  (maintainer, 2026-11-27).

## Related materials

- Seed: `issues/timestamp-display-offsets.md`
- `spec/update-typed-columns/research/03-datetime.md` §1–§2; `spec/update-typed-columns/PLAN.md` Q1
- Display paths: `src/hypershell/core/pretty_print.py` (`format_json`), `src/hypershell/task.py`
  (`TaskInfoApp.print_field`/`print_formatted`, `prepare_display`, `TaskSearchApp.print_*`, the
  `hs wait` completion log)
