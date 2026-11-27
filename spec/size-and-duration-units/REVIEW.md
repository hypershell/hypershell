# REVIEW — Sizes and durations written with common units are read as written

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** d2bff2e  ·  **Base:** origin/develop (`50e64a9`)  ·  **Date:** 2026-11-11
- **Verdict:** approved (one PLAUSIBLE finding for human triage)
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

Run by hand, with the reviewer also acting as orchestrator (no nested subagent): the correctness pass
read only `GOAL.md`, `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, `invariants.md`,
`review-rubric.md`, `AGENTS.md` and the runnable repo. `TECH.md` was read for frontmatter only.

## Verification run

- `uv run pytest -q tests/test_size_duration.py` → 228 passed.
- `uv run pytest -q -m unit` → 1077 passed, 3 skipped.
- Parser probe (60+ inputs: whitespace, signs, zero, huge values, `1.5GiB`, `0.5`, mixed case, `1h30`,
  `90m30s`, every Slurm form, empty and blank strings, `30m1h`, `1h 30m`, `1-`, `1:30:00:00`) →
  every R1–R3 form reads as specified; every R5 form raises `UnitError` naming the value and forms;
  no value is read by its leading part.
- `temp_site.sh`: `hs submit -W 1.5h -m 512M` on a file with inline `timeout:1:30:00 memory:16G`,
  `timeout:1h30m memory:1.5GiB`, `timeout:3600 memory:16GB` and a bare line → `hs list` shows
  5400/17179869184, 5400/1610612736, 3600/17179869184, 5400/536870912; `hs info` shows
  `timeout: 1:30:00`.
- `temp_site.sh`: a standalone `# HYPERSHELL: timeout:1:30:00 memory:2G` header applies 5400/2 GiB
  to the tasks that follow.
- `temp_site.sh`: refusals — `-W 1hr`, `-m 16X` exit 2; inline `timeout:1hr`, `memory:8GB_of_ram`,
  header `timeout:2hr` exit 5; each message names the value and the accepted forms.
- `temp_site.sh`: `HYPERSHELL_TASK_TIMEOUT=2h HYPERSHELL_TASK_MEMORY=2G hs submit` stores 7200 and
  2 GiB; `HYPERSHELL_TASK_TIMEOUT=2hr` exits 2 with the forms. A site `config.toml` with
  `task.timeout = "1:30:00"`, `task.memory = "16G"`, `client.timeout = "1h"`,
  `client.memory = "64GiB"` parses to 5400, 16 GiB, 3600, 64 GiB on submit, client and cluster.
- `temp_site.sh`: `hs cluster t.in -N1 --no-db -M 64G -W 90:00 -T 1-12` with an inline
  `memory:16G` task → both tasks run.
- Develop baseline (`git archive origin/develop`): inline `timeout:90m` stored as the text `'90m'`,
  `memory:16G` as 16 — the defect reproduces before the fix.
- Docs: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs <tmp>` →
  exit 0, no warnings.
- **2.8.6 cherry-pick:** scratch clone, `git checkout 2.8.6`, `git cherry-pick 5ad7c69 d2bff2e` →
  all source, test, help-snippet and completion hunks apply cleanly; the only conflicts are
  `spec/` files and `docs/whats_new.rst`, which do not exist on 2.8.6 (modify/delete; dropped).
  On the picked tree (version 2.8.6): `tests/test_size_duration.py` → 228 passed; full suite under
  Python 3.12 (`uv run --python 3.12 pytest -q -x`) → 679 passed, 1 skipped.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `core/types.py` `parse_bytes` fullmatch, `MEMORY_PATTERN` (5ad7c69); call sites unchanged | parser probe; `test_memory_option_parsed` over all 5 options; temp-site submit/cluster | ✅ |
| R2 | `parse_time` + `type=parse_time` on submit/server/cluster/client `-W`, cluster/client `-T` (d2bff2e) | parser probe; `test_timeout_option_parsed` over all 6 options; temp-site submit/cluster | ✅ |
| R3 | `TIME_CLOCK_PATTERN`, `TIME_DAYS_PATTERN`; bare integer via `TIME_SECONDS_PATTERN` | probe: `90:00`, `1:30:00` → 5400; `1-12`, `1-12:00`, `1-12:00:00` → 129600; `90` → 90 | ✅ |
| R4 | `data/model.py` `Task.new` converts `timeout`; `ensure_valid_tag` takes Slurm colons for `timeout`/`memory` | temp-site `hs list`/`hs info` show 5400 / `1:30:00` | ✅ |
| R5 | `UnitError` (ValueError + ArgumentTypeError) with value and forms | CLI exit 2 (options) / 5 (inline); probe of every listed refusal | ✅ |
| R6 | same | probe: `16GB`, `512MB`, `2KB`, `16 GB`, `1.5GB`, `16gb`, bare ints, inline `timeout:3600` unchanged | ✅ |
| R7 | `core/logging.py` unchanged; fullmatch makes `30 2 * * *` raise, caught as time-like | `test_rotate_policy_kind` (`30 2 * * *` time-like; `2G`, `2GB` size-like) | ✅ |
| R8 | CLI help strings, `docs/_include/*_help.rst`, `config_param_ref.rst`, `logging.rst`, server zsh completion | read diff; docs build `-W` clean | ✅ |
| R9 | `docs/whats_new.rst` *Not yet released* → Fixes | read: states `16G`/`16GiB` were read as bytes and stored tasks are not corrected; "also ships in 2.8.7" | ✅ |
| R10 | `tests/test_size_duration.py` (unit + integration, all tagged) | 228 passed on branch and on the 2.8.6 pick | ✅ |

Unmapped changes (possible scope creep): none. `ROADMAP.md` and `issues/size-and-duration-units.md`
are the seed; its Problem matches the code at the cited lines on `origin/develop`, and its status
(`adopted:size-and-duration-units`) matches.

## Findings

### [HIGH/PLAUSIBLE] No `versionchanged` marker on the changed prose
- **Where:** `docs/logging.rst:359-370` (size-like / time-like), `docs/_include/config_param_ref.rst`
  (`.rotate`, `client.timeout`, `client.memory`, `task.memory`, `task.timeout`), and the
  `.. include::` of each changed help snippet in `docs/cli/{submit,server,client,cluster}.rst`.
- **Failure scenario:** `AGENTS.md` § Docs / invariants §12 ask a user-visible behaviour change to
  carry `.. versionchanged::`; the diff adds none.
- **Evidence:** `git diff origin/develop...HEAD -- docs | grep -c versionchanged` → 0.
- **Why PLAUSIBLE, not CONFIRMED:** the change also ships on the stable line (2.8.7), and the
  convention keys markers to behaviour "the stable line lacks" in `X.Y.0` form; the precedent 2.8.6
  fix (`290a7e2`) carried no marker. `GOAL.md` leaves marker placement for a dual-shipped fix open.
  Human call: add `versionchanged:: 2.8.7` (or none) before publish.
- **Touches:** invariants §12 (would grade HIGH if ruled required).

### Observations (not findings)
- Signed or underscored integers (`-W +60`, `-W 1_000`) were accepted by `type=int` and are now
  refused with the forms. Not a value anyone writes; dropped under the refutation protocol.
- A naked inline `timeout` tag (`# HYPERSHELL: timeout`) was stored as `''` and is now refused;
  consistent with R5.
- The non-goal's follow-up seed `issues/duration-units-remaining-options.md` is not filed on the
  branch; GOAL names it but no R-ID requires it in this cycle.

## Human-gate triggers

- None: no CONFIRMED finding. (`data/model.py` changed, but no defect was reproduced there.)
