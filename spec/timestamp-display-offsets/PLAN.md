# PLAN — timestamp-display-offsets

Lean plan (`kind: fix`, `appetite: small`; no fan-out). The change is display-only and touches no
high-blast-radius file; the root cause is known (research 03 §2 of `update-typed-columns`).

## Summary

Add one display conversion, `to_display_type`, beside `format_json` in
`src/hypershell/core/pretty_print.py`: a `datetime` becomes `value.astimezone().isoformat(sep=' ')`
(a naive value is taken as local wall time, an aware one is converted to the local zone), and any
other value goes through `to_json_type` unchanged. `format_json` builds on it. Every display path in
`src/hypershell/task.py` that today calls `to_json_type` or `Task.to_json()` for output calls it
instead. `to_json_type` and `Task.to_json()` — the wire — are untouched.

## Design

- **`core/pretty_print.py`.** `to_display_type(value)`; `format_json` calls it in place of
  `to_json_type`. Should `astimezone()` fail on an instant at the edge of the calendar
  (`OverflowError`, `ValueError`, `OSError`), it falls back to the stored value's own ISO form, so a
  display never raises. (`hs update` already refuses instants within a day of either end, so this is
  a guard, not a path.) Exported in `__all__`.
- **`task.py`, `hs info`.** A helper `display_json(task)` = `{k: to_display_type(v) for k, v in
  task.to_dict().items()}`, the display twin of `Task.to_json()`. `print_field` (`-x FIELD`) and
  `print_formatted` (json/yaml/toml) read from it. The normal and card views go through
  `prepare_display`, which already uses `format_json` and needs no change.
- **`task.py`, `hs list`.** table and plain already use `format_json`; json and csv switch their
  `to_json_type` calls to `to_display_type`; normal and card go through `prepare_display`.
- **`task.py`, `hs wait`.** The completion log formats `to_display_type(task.completion_time)`.

## Requirement → design map

| R | Design |
|---|---|
| R1 | `to_display_type` → local zone, offset, `sep=' '`, on whatever the backend returns |
| R2 | `display_json` (info json/yaml/toml/-x), `format_json` (info normal/card, list table/plain/normal/card), `to_display_type` (list json/csv, wait log) |
| R3 | `astimezone()` reads the process's local zone, which follows `TZ` |
| R4 | `astimezone()` on a naive value assumes local wall time |
| R5 | `None` and other non-datetimes pass through `to_json_type` as today |
| R6 | `to_json_type`, `Task.to_json()`, `relay/store.py`, server and client untouched; a test pins `to_json_type` |
| R7 | `docs/cli/task_info.rst`, `docs/cli/task_search.rst`: a paragraph under `versionchanged:: 2.9.0` |

## Verification strategy

- Unit: `to_display_type` on naive, aware (another zone), `None`, non-datetime; `to_json_type`
  unchanged; edge fallback.
- CLI, on SQLite and (with `HS_TEST_POSTGRES`) PostgreSQL via `db_site`: submit a task, set
  `submit_time` with `hs update` to a fixed instant, then read it under `TZ=America/New_York` with the
  PostgreSQL session zone forced to UTC (`PGTZ=UTC`): every format shows `2026-01-01 09:00:00-05:00`;
  under `TZ=UTC`, `2026-01-01 14:00:00+00:00` on PostgreSQL. An unscheduled task's `schedule_time`
  shows as today (`null`/empty per format).
- Docs build with `-W`.

## Deviation justification

None. No invariant bent (display only; wire, schema and storage unchanged).
