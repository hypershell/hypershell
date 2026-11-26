---
slug: range-docs-and-group-floor
title: "Numeric ranges out of the user docs; task groups start at 0"
kind: fix
appetite: small
status: in_progress
branch: fix/range-docs-and-group-floor
base: develop
current_phase: P1
last_updated: "2026-11-25"
phases:
  - id: P1
    name: "Tests first: a group below 0 is refused on every path (red on 42e2112f)"
    status: pending
    satisfies: [R4]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "test -z \"$(git diff --name-only 42e2112f -- . ':!spec' ':!tests')\" && ! uv run pytest -q -p no:cacheprovider tests/test_group_floor.py && test \"$(uv run pytest -q -p no:cacheprovider tests/test_group_floor.py 2>&1 | grep -cE '^(FAILED|ERROR) ')\" -ge 6"
  - id: P2
    name: "The floor: GROUP starts at 0; group docs say non-negative (R4, R5)"
    status: pending
    satisfies: [R4, R5]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -q -p no:cacheprovider tests/test_group_floor.py tests/test_bounded_table.py tests/test_bounded_parsers.py tests/test_bounded_options.py tests/test_update_group.py tests/test_group_integer.py tests/test_group_order.py && ! grep -rn '32-bit range' docs/_include docs/cli AGENTS.md .agents/factory/invariants.md && grep -q 'non-negative' docs/_include/submit_help.rst && .agents/factory/bin/temp_site.sh sh -c \"uv run hs submit -g -1 echo a; test \\$? -eq 2 && uv run hs submit -g 0 echo a && uv run hs list -g -1; test \\$? -eq 2\""
  - id: P3
    name: "Figures out of the docs and man pages; the guard (R1, R2, R3, R6)"
    status: pending
    satisfies: [R1, R2, R3, R6]
    depends_on: [P2]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "! grep -rln 'Range:' docs/_include docs/cli docs/api docs/*.rst share/man/man1 && ! grep -rn 'advice_text\\|def text' src/hypershell/core/config_schema.py && uv run pytest -q -p no:cacheprovider tests/test_bounded_docs.py tests/test_update_numbers.py tests/test_config_schema.py && UV_PROJECT_ENVIRONMENT=\"${TMPDIR:-/tmp}/docs-venv-rdg\" sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs \"${TMPDIR:-/tmp}/docs-html-rdg\""
  - id: P4
    name: "Gate: the whole suite serially, the PostgreSQL group and update files, the docs"
    status: pending
    satisfies: [R1, R2, R3, R4, R5, R6]
    depends_on: [P3]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "test -n \"$HS_TEST_POSTGRES\" && env -u HS_TEST_POSTGRES sh spec/update-client-retry/drive/bound.sh 5400 uv run pytest -q -p no:cacheprovider && sh spec/update-client-retry/drive/bound.sh 3600 uv run pytest -q -p no:cacheprovider tests/test_group_floor.py tests/test_update_group.py tests/test_group_integer.py tests/test_update_numbers.py tests/test_bounded_table.py"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — Numeric ranges out of the user docs; task groups start at 0

The **context engine and finite-state machine** for this cycle. The YAML frontmatter above is the
resume ground-truth (`uv run python .agents/factory/bin/next_phase.py spec/range-docs-and-group-floor/TECH.md`).

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). No `research/` (lean plan).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; checklist
  [`invariants.md`](../../.agents/factory/invariants.md) §1, §10, §12.
- One phase per `hs-build` invocation; one atomic signed commit with the code and the `TECH.md` state
  change, subject `[fix] Build range-docs-and-group-floor P<n>: …`; **no `Co-Authored-By` trailer**.
- **Tests first.** P1 adds only `tests/test_group_floor.py`, red on `42e2112f`. P2 changes the
  existing tests whose bound moves (PLAN §4) in the commit that moves it.
- **Docs land with their phase.** Markers carry no `:pr:` role (added at release). Docs name
  top-level commands only; no internal or cluster names; no key on a command line.
- pytest runs serially. PostgreSQL cases need `HS_TEST_POSTGRES` exported and the sandbox off.
- Set `UV_CACHE_DIR` to a per-run directory under `$TMPDIR`.

---

## Phase P1 — Tests first: a group below 0 is refused on every path
**Satisfies:** R4 · **Depends on:** —

- [ ] `tests/test_group_floor.py` (new, SPDX header): `-1` (and `-2147483648`) refused, with the
      message naming the range `from 0` and exit status as today's out-of-range group, by
      - `hs submit -g -1` (exit 2);
      - an inline `# HYPERSHELL: group:-1` line in a submitted file (exit 5, nothing submitted);
      - `hs submit -t group:-1` (exit 2);
      - a `--from-json` record with `"group": -1` (exit 5);
      - `hs update group=-1 -f` and `hs update ID group -1` (exit 2, row unchanged);
      - `hs list -g -1` (exit 2);
      - `parse_group('-1')` and `ensure_group(-1)` raising `TaskGroupError` with `out_of_range`.
      Plus guards (green on base): `0` accepted on each path; the default is 0.
- [ ] Nothing outside `tests/` and `spec/` changes.
- **Touches:** `tests/test_group_floor.py`.

## Phase P2 — The floor
**Satisfies:** R4, R5 · **Depends on:** P1

- [ ] `core/config_schema.py`: `GROUP` low bound 0, `why` per PLAN §2.1.
- [ ] `data/model.py`: `GROUP_MIN` docstring (code unchanged).
- [ ] Existing tests whose bound moves: `test_bounded_table.py:131`, `test_bounded_parsers.py:46`,
      `test_bounded_options.py:164,166`, `test_update_group.py:51`, `test_group_order.py:268,416`
      (PLAN §4).
- [ ] Docs (PLAN §2.2): `submit_help.rst` `-g`, `task_update_desc.rst`, `task_update_help.rst`,
      `docs/cli/task_update.rst:63`, the group markers on the `hs submit` and `hs update` pages.
- [ ] `AGENTS.md:276`, `invariants.md:117`: the floor, in lockstep.

## Phase P3 — Figures out of the docs and man pages; the guard
**Satisfies:** R1, R2, R3, R6 · **Depends on:** P2

- [ ] Remove every `Range:` and figure-bearing advisory sentence (PLAN §2.3 file list).
- [ ] Reword the markers and passages that point at the removed lines (PLAN §2.3).
- [ ] `config_schema.py`: delete `Range.text` and `Range.advice_text`; `test_update_numbers.py`
      reads `.bounds`.
- [ ] `tests/test_bounded_docs.py`: the guard (PLAN §2.4); figure-free tests kept.
- [ ] Rebuild the man pages (PLAN §2.5), version unchanged.

## Phase P4 — Gate
**Satisfies:** R1–R6 · **Depends on:** P3

- [ ] Run the frontmatter verify (sandbox off, `HS_TEST_POSTGRES` exported). Expected environment
      failures on this host (upgrade templates, TimescaleDB) are listed in the commit body, as in
      prior cycles. Record counts and time.
- **Touches:** `spec/range-docs-and-group-floor/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md`).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance, `--touch`; one `[fix]` commit; report.
