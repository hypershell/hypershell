---
slug: size-and-duration-units
title: Sizes and durations written with common units are read as written
kind: fix
appetite: small
status: in_progress
branch: fix/size-and-duration-units
base: develop
current_phase: P2
last_updated: '2026-11-11'
phases:
- id: P1
  name: 'Sizes: strict parse_bytes end to end (options, inline memory, rotation policy,
    docs)'
  status: done
  satisfies:
  - R1
  - R5
  - R6
  - R7
  - R8
  - R9
  - R10
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_size_duration.py -k "size or memory or rotate"
    && .agents/factory/bin/temp_site.sh sh -c "uv run hs submit -m 16G echo a && !
    uv run hs submit -m 16X echo b" && uv run --python 3.12 --group docs sphinx-build
    -W --keep-going -q -E -b html docs docs/_build/html
- id: P2
  name: 'Durations: strict parse_time on six options and the inline timeout tag, docs,
    cherry-pick check'
  status: pending
  satisfies:
  - R2
  - R3
  - R4
  - R5
  - R6
  - R8
  - R10
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -v tests/test_size_duration.py tests/test_submit.py tests/test_cluster_launch.py
    && .agents/factory/bin/temp_site.sh sh -c "echo ''echo a # HYPERSHELL: timeout:1:30:00
    memory:16G'' > t.in && uv run hs submit -f t.in -W 1h30m && uv run hs list id
    timeout memory && ! uv run hs submit -W 1hr echo b" && uv run --python 3.12 --group
    docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html && ! git
    merge-tree --write-tree --name-only --no-messages --merge-base="$(git log -1 --format=%H
    --grep=''Plan size-and-duration-units'')" 2.8.6 HEAD | tail -n +2 | grep -v -e
    ''^$'' -e ''^docs/whats_new.rst$'' -e ''^spec/'''
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Sizes and durations written with common units are read as written

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run --no-project --with pyyaml python .agents/factory/bin/next_phase.py spec/size-and-duration-units/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `set_phase.py`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§2 has the five plan-stage decisions and the
  2.8.6 drift).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: `false` here — both phases touch the coupled core.
- `hammerable`: `false` — correctness phases; scope-hammering must never cut them.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; footguns in
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §3, §10, §11, §12.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build size-and-duration-units P<n>: …`. **No `Co-Authored-By` trailer.**
- A CLI change updates its `docs/_include/*_help.rst` snippet and the zsh completion description **in
  the same commit**, plus its prose (`config_param_ref.rst`, `logging.rst`) and the `whats_new` entry.
  Do not edit `share/man/` (rebuilt at release). No `versionchanged` marker (PLAN decision 5).
- Docs and examples name only top-level commands (never `hs task …`) and never pass a key with `-k`.
- **Cherry-pick discipline (2.8.7).** The squash commit is cherry-picked onto `2.8.6`. Keep edits to
  the parsers, their call sites, `tests/test_size_duration.py` (new file — do not add to
  `tests/test_submit.py` or `tests/test_logging.py`), and the docs below. In `data/model.py` leave the
  line `other['timeout'] = tag.pop('timeout', other.get('timeout', None))` **byte-identical** and put
  the timeout conversion in lines inserted above it. In `submit.py`'s `APP_HELP` edit only the `-m`
  and `-W` lines. Tests use only helpers both lines have (`tests.main`, `temp_site`,
  `create_taskfile`) and no develop-only `Task.new` parameters.
- Exit codes come from the existing exception mapping; no integer literals.

---

## Phase P1 — Sizes: strict `parse_bytes` end to end
**Satisfies:** R1, R5, R6, R7, R8, R9, R10 (sizes) · **Depends on:** —
**Goal:** every memory option, the inline `memory:` tag and the rotation policy read `16G`, `16GiB`,
`1.5GiB` as powers of 1024, refuse `8,000MB`/`8GB of ram`/`16X`/`16GBB` naming the forms, keep every
value accepted today, and treat `30 2 * * *` as a time policy.

- [x] `core/types.py`: add `UnitError(ValueError, argparse.ArgumentTypeError)` to `__all__`; rewrite
      `parse_bytes` as a stripped, case-insensitive full match of `\d+(\.\d+)?\s*` + optional
      `[KMGT](B|iB)?` or `B`; result `int(float(num) * scale)` (floor, today's arithmetic); refusal
      raises `UnitError` naming the value and the forms. Replace `MEMORY_PATTERN`; keep `MEMORY_SCALES`.
- [x] `data/model.py` `ensure_valid_tag`: in the `if isinstance(value, str):` block, after the
      naked-tag `return`, a `memory` value is checked with `parse_bytes` and skips the charset regex.
- [x] `data/model.py` `Task.new`: the memory conversion re-raises a `UnitError` as a `RuntimeError`
      naming the value and the forms (precedent: `split_argline`'s "Failed to parse inline tags").
- [x] `core/logging.py`: no edit — confirm `initialize_logging`'s `except ValueError` branch makes
      `30 2 * * *` time-based.
- [x] Help: memory lines in `APP_HELP` of `submit.py`, `server.py`, `client.py`, `cluster/__init__.py`
      and `docs/_include/{submit,server,client,cluster}_help.rst` (incl. the inline `memory:` example)
      state `K`, `M`, `G`, `T` with or without `B`/`iB` are powers of 1024. `config_param_ref.rst`
      (`task.memory`, `client.memory`, `logging.file.rotate`) and `docs/logging.rst` rotate prose.
      zsh `_hs` "Memory in bytes" descriptions.
- [x] `docs/whats_new.rst`: *Not yet released → Fixes* entry below the #92 one, carrying R9 ("a size
      written with a short or binary unit (`16G`, `16GiB`) was read before the fix as that many bytes;
      tasks already stored that way are not corrected") and ending "This also ships in 2.8.7."
- [x] `tests/test_size_duration.py` (new): `@mark.unit` parametrized `test_size_*` accept table (R1,
      R6: `16GB`, `512MB`, `2KB`, `16 GB`, `1.5GB`, `16gb`, `1024`, `16G`, `16g`, `16GiB`, `512M`,
      `1.5GiB`) and refusal table (R5) with the forms in the message; `test_memory_*` for each memory
      option's parsed value and refusal message, `ensure_valid_tag`, and `@mark.integration` inline
      `memory:16G` stored as 17179869184 and a bad inline `memory:` exiting 5 with the value named;
      `test_rotate_*` for `parse_bytes('30 2 * * *')` refused and a subprocess CLI run under
      `HYPERSHELL_LOGGING_FILE_ROTATE` `30 2 * * *` (time-like) and `2G` (size-like).
- **Verify:** `uv run pytest -v tests/test_size_duration.py -k "size or memory or rotate"`, a
  `temp_site` drive (`hs submit -m 16G` succeeds, `-m 16X` fails), and the Sphinx `-W` build.
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/data/model.py`, the four `APP_HELP`
  blocks, `docs/_include/*_help.rst`, `docs/_include/config_param_ref.rst`, `docs/logging.rst`,
  `docs/whats_new.rst`, `share/zsh/site-functions/_hs`, `tests/test_size_duration.py`.

## Phase P2 — Durations: strict `parse_time` on six options and the inline `timeout:` tag
**Satisfies:** R2, R3, R4, R5, R6, R8, R10 (durations) · **Depends on:** P1
**Goal:** the task and client timeouts and the inline `timeout:` tag read `1h30m`, `1.5h`, `1d12h`,
`1:30:00`, `90:00`, `1-12` as seconds, keep a bare integer as seconds, refuse `5MB`/`1hr`/`2hours`/`1w`
naming the forms, and the change still cherry-picks onto `2.8.6`.

- [ ] `core/types.py`: rewrite `parse_time` — stripped, case-insensitive full match of a bare number
      (seconds), or `[N d][N h][N m][N s]` (≥1, each once, in order, fractional N), or Slurm `MM:SS`,
      `HH:MM:SS`, `D-HH`, `D-HH:MM`, `D-HH:MM:SS` (integer fields); result `math.ceil` to `int`;
      refusal raises `UnitError` naming the value and the forms. Drop `W` and `TIME_PATTERN`.
- [ ] `type=int` → `type=parse_time` on `submit.py -W`, `server.py -W`, `client.py -T`/`-W`,
      `cluster/__init__.py -W`/`-T`; add `parse_time` to each existing `core.types` import line.
- [ ] `data/model.py` `ensure_valid_tag`: a `timeout` value is checked with `parse_time` and skips the
      charset regex (next to the P1 memory check).
- [ ] `data/model.py` `Task.new`: insert lines **above** the untouched timeout pop that read the string
      timeout from `tag` (else `other`), convert it with `parse_time` into `tag['timeout']`, and
      re-raise a `UnitError` as a `RuntimeError` naming the value and the forms. Ints/floats pass.
- [ ] Help: timeout lines in the four `APP_HELP` blocks and `_include/*_help.rst` (incl. the inline
      `timeout:` example) state the duration forms; `config_param_ref.rst` `task.timeout`,
      `client.timeout`; zsh `_hs` "… in seconds" descriptions. Extend the P1 `whats_new` entry with
      the duration forms.
- [ ] `tests/test_size_duration.py`: `@mark.unit` parametrized `test_duration_*` accept table (R2, R3,
      R6: `45s`, `90m`, `1h30m`, `1d12h`, `1.5h`, `90`, `90:00`, `1:30:00`, `1-12`, `1-12:00`,
      `1-12:00:00`, `1H30M`, `0.5s` → 1) and refusal table (R5: `5MB`, `1hr`, `2hours`, `1w`, `30m1h`)
      with the forms in the message; `test_timeout_*` for each of the six options' parsed value and
      refusal, `ensure_valid_tag` accepting `timeout:1:30:00`; `@mark.integration` inline
      `timeout:1h30m` and `timeout:1:30:00` stored as 5400, `-W 1.5h` stored as 5400,
      `HYPERSHELL_TASK_TIMEOUT=1h30m` reaching a submitted task as 5400, and a bad inline `timeout:1hr`
      exiting 5 with the value named (assert exit and message, not the partial count).
- **Verify:** the new test file plus `tests/test_submit.py` and `tests/test_cluster_launch.py`
  (launched-client argv round-trip), a `temp_site` drive (inline `timeout:1:30:00 memory:16G` with
  `-W 1h30m` submits and lists; `-W 1hr` fails), the Sphinx `-W` build, and the cherry-pick dry run:
  `git merge-tree --merge-base=<plan commit> 2.8.6 HEAD` must report no conflict outside
  `docs/whats_new.rst` and `spec/`.
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/{submit,server,client}.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/data/model.py`, `docs/_include/*_help.rst`,
  `docs/_include/config_param_ref.rst`, `docs/whats_new.rst`, `share/zsh/site-functions/_hs`,
  `tests/test_size_duration.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP and escalate only on a
   **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.

At publish, the squash commit body carries the R9 sentence (it becomes the 2.8.7 release note; master
has no `whats_new.rst`). The 2.8 cherry-pick drops `docs/whats_new.rst` and `spec/`, then runs
`tests/test_size_duration.py` on the 2.8 branch.
