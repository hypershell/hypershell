---
slug: timestamp-display-offsets
title: One display rule for task timestamps on every backend
kind: fix
appetite: small
status: in_progress
branch: fix/timestamp-display-offsets
base: develop
current_phase: P2
last_updated: '2026-11-27'
phases:
- id: P1
  name: Local-zone display with offset in every hs info / hs list / hs wait path
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_timestamp_display.py
- id: P2
  name: 'Docs: state the rule under versionchanged 2.9.0'
  status: pending
  satisfies:
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: true
  hill: downhill
  verify: uv run pytest -q tests/test_timestamp_display.py -k docs
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — One display rule for task timestamps on every backend

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md).
- **Authoritative design:** [`PLAN.md`](PLAN.md).

## P1 — Local-zone display with offset

- [x] `to_display_type` in `core/pretty_print.py` (exported); `format_json` builds on it.
- [x] `display_json(task)` in `task.py`; `print_field` and `print_formatted` use it.
- [x] `hs list` json and csv use `to_display_type`.
- [x] `hs wait` completion log uses `to_display_type`.
- [x] `tests/test_timestamp_display.py`: unit tests and CLI tests on `db_site` (every format, both
      zones, empty stamp, wire unchanged).

## P2 — Docs

- [ ] `docs/cli/task_info.rst` and `docs/cli/task_search.rst`: the rule under
      `.. versionchanged:: 2.9.0`.
- [ ] A docs test that reads the marker; the docs build passes with `-W`.
