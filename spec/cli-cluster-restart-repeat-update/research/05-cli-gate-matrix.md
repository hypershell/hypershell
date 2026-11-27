# 05 — CLI flag / gate decision matrix (both entry points)

Scope: the full flag surface and refusal/de-dup gate for `hs submit` (SubmitApp) and `hsx`/`hs cluster`
(ClusterApp), covering GOAL R5–R16. Grounded in current source; recommends the argument + validation
changes and the authoritative decision table. Read-only research — no edits made.

## Entry-point argument surfaces today

**`hs submit`** (`src/hypershell/submit.py:993`): NO `--restart`. Relevant flags: positional `task_args`,
`-f/--task-file` (`:1004`), `--from-json` (`:1005`), `-q/--queue` (`:1035`), `-g/--group` (`:1025`),
`-t/--tag` (`:1052`). Validation is inline (no `check_arguments`): `check_source` (`:1124`) picks
stdin/file/single-cmd mode; `check_database` (`:1106`) refuses in-memory sqlite. Queue-mode (`-q`) submits
via `LiveSubmitThread` straight to the server, **bypassing the DB** (`run`, `:1063-1073`).

**`hsx`/`hs cluster`** (`src/hypershell/cluster/__init__.py:122`): HAS `--restart` (`restart_mode`, `:171`).
Positional `filepath` (`:129`), `--from-json` (`:132`), `--forever` (`:168`), `--no-db` (`in_memory`,
`:158`). All validation is centralized in `check_arguments` (`:335`), run from `__enter__` (`:445`).

Both apps use `**get_shared_exception_mapping` (submit `:1054`, cluster `:257`).

## How refusals map to exit codes (verified)

cmdkit `Application.main` catches `ArgumentError` → returns **`exit_status.bad_argument` (== 2)** and
logs it critical; `ConfigurationError` → `bad_config` (3) via the shared mapping
(`core/exceptions.py:135`); generic `RuntimeError` → `runtime_error` (5). `exit_status` values:
`success=0, usage=1, bad_argument=2, bad_config=3, keyboard_interrupt=4, runtime_error=5`.

**Recommendation:** raise `cmdkit.cli.ArgumentError` for every loud refusal (contradictory/ambiguous flag
combos AND DB-state gate refusals). It is already wired, prints an actionable critical message, and yields
a single consistent non-zero code (`bad_argument`=2), satisfying invariant §12 "reuse `exit_status`
constants, don't invent literals." (Semantic nit: R5/R6/R12-differs are runtime DB-state refusals, not
strictly argument errors — but `bad_argument`/2 is the cleanest reuse and keeps all refusals uniform. If a
distinct code is wanted for "source already exists," use `runtime_error`/5 via a dedicated exception in the
mapping; flagged as an open question below.)

## Current `--restart` behavior (file is IGNORED today) — exact

- `--restart` sets `restart_mode` (`cluster/__init__.py:171`).
- `check_arguments:344`: `elif self.filepath is None and not self.restart_mode: self.filepath = '-'` — with
  `--restart`, filepath is NOT defaulted to stdin.
- `input_stream` (`:422-427`): `if self.restart_mode or self.from_json: return None`.
- `source` (`:434-441`): `if self.restart_mode: return []` → **empty source; the file is silently
  discarded**. If a user passes `hsx file.txt --restart` today, `file.txt` is ignored entirely.
- `restart_mode` flows to `ServerThread` (`server.py:151` `startup_phase = not restart_mode`; `:179`
  `Task.revert_interrupted()`), so restart today = revert interrupted + schedule whatever pending rows
  already exist in the DB. (Contrast: `hs server` *refuses* FILE+`--restart` at `server.py:1380`; ClusterApp
  does not refuse — it just ignores.)

**New behavior:** `--restart` becomes **file-aware**. `source`/`input_stream` must stop hard-returning
`[]`/`None` when a filepath is present. Preserve the legacy **bare** `hsx --restart` (no file) as a pure DB
resume (`source == []`), but when a file is given, read it, compute source fingerprint + per-task identities
(R2), and yield only novel task lines (R12/R14). The revert-interrupted flow (`server.py:179`) is unchanged
(non-goal: no retry/revert change).

## Authoritative decision table

Prior state for a **named file** at path `P` with content fingerprint `F` (vs the `SOURCE` table):
`none` = no source row for `P`; `match` = row with `path==P AND md5==F`; `differs` = row with `path==P AND
md5!=F`. "novel-only" = submit only tasks whose identity fingerprint (R2: order-independent hash of
pre-expansion `args`+`group`+`tags`) is absent from the **same-path (`P`) source lineage**. R7 count-warn
(warn if landed task count < recorded source count) is an informational log (R18) emitted whenever a prior
`P` source exists — orthogonal to the submit/refuse decision; it fires in the match/differs rows and in the
`--restart`/`--update` paths.

### `hs submit` (no `--restart`)

| flags | none | match (P,F seen) | differs (P seen, F changed) |
|---|---|---|---|
| (no flag) | submit all, new source | **REFUSE** (2), identify prior — **R5** (+count-warn R7) | **REFUSE** (2), suggest `--update` — **R6** |
| `--repeat` | new source, submit all | new source, submit all — **R8** | new source, submit all — **R8** |
| `--update` | new source, submit all (nothing to skip) | new source, **novel-only** (≈0 new) — **R9** | new source, **novel-only** in P-lineage — **R9** |
| `--update --repeat` | **REFUSE** (2) contradictory — **R10** | REFUSE (2) — R10 | REFUSE (2) — R10 |

### `hsx` / `hs cluster` (has `--restart`)

| flags | none | match (P,F seen) | differs (P seen, F changed) |
|---|---|---|---|
| (no flag) | submit all, new source | **REFUSE** (2), identify prior — **R11=R5** (+R7) | **REFUSE** (2), suggest `--update` — **R11=R6** |
| `--restart` | new source, submit all (nothing prior) | **novel-only**; rely on revert-interrupted — **R12** | **ALERT + REFUSE** (2), suggest `--update` — **R12** |
| `--update` (no `--restart`/`--repeat`) | **REFUSE** (2) ambiguous — **R13** | REFUSE (2) — R13 | REFUSE (2) — R13 |
| `--update --restart` | new source, submit all | new source, novel-only — **R14** | new source, **novel-only** in P-lineage — **R14** |
| `--repeat` | new source, submit all | new source, submit all even if match; MAY add tags — **R15** | new source, submit all — **R15** |
| `--update --repeat` | **REFUSE** (2) contradictory — **R16** | REFUSE (2) — R16 | REFUSE (2) — R16 |

Note R12/match "novel-only" against the same-path lineage yields exactly "the tasks that never landed,"
which is the requeue-Slurm outcome; the revert flow re-runs anything mid-flight (no double work).

## Two plan-time assumptions — SETTLED

- **(a) `--update` de-dup scope = same-path (`P`) source lineage** for BOTH `hs submit --update` (R9) and
  `hsx --update --restart` (R14). Confirmed consistent with GOAL Clarifications and R9's explicit wording;
  requires an identity-fingerprint lookup filtered to sources sharing path `P` (R17 index). Both forms use
  the identical predicate.
- **(b) tags participate in identity (R2)** → re-tagging a line between file versions makes it a **new**
  task under `--update` (re-submitted). Confirmed as intended (the cost of letting `--repeat` re-tag a
  phase). Task identity excludes UUID/attempt/timing/exit_status/template.

## RECOMMENDATION

**SubmitApp** (`submit.py`): add `--repeat` (`store_true`, `dest='repeat_mode'`, default False) and
`--update` (`store_true`, `dest='update_mode'`). No `--restart`. Add a `check_arguments` (or extend
`check_source`) that: rejects `--update --repeat` (R10, `ArgumentError`→2); wires the DB-state gate (R5/R6
refuse, R7 warn, R8/R9 submit) in the DB path only. **Gating applies only to the DB submit path** — `-q`
queue-mode (`LiveSubmitThread`, no DB) and in-memory sqlite (`check_database:1106`) have no source table, so
they are exempt (analogous to the `in_memory` non-goal); `<stdin>`/single-cmd (`<direct>`) are exempt by R3.

**ClusterApp** (`cluster/__init__.py`): add `--repeat` (`dest='repeat_mode'`) and `--update`
(`dest='update_mode'`). In `check_arguments` (`:335`): reject `--update` without (`--restart` or `--repeat`)
(R13, →2); reject `--update --repeat` (R16, →2); reject `--update` with `--no-db` (in_memory has no lineage);
treat `--repeat` with `--no-db` as redundant (warn). Make `source`/`input_stream` (`:422-441`) file-aware:
bare `--restart` (no filepath) keeps `source==[]` (legacy DB resume); `--restart`/`--update`/`--repeat` with a
filepath must read + fingerprint the file and yield the gated task subset. Existing rejections to keep:
`--forever`+`--restart` (`:361`), `--from-json`+`--restart` (`:341`), `--no-db`+`--restart` (`:359`).

**Exit codes:** all refusals via `ArgumentError` → `exit_status.bad_argument` (2). Warnings (R7/R18) via
`log.warning`, exit 0.

**Not forwarded to clients (invariant §11 confirmed):** `--restart`/`--repeat`/`--update` are submit/server
-side only. Client argv builders (`ssh.py:296-298`, `remote.py:303-305`, `remote.py:833`) construct the
`client` subcommand and carry none of these; `restart_mode` reaches `ServerThread` (`server.py:151`), never
the client launcher. New flags follow the same path — do not add them to any client argv builder.

## Open questions

- **`--restart --repeat` (hsx)** is not enumerated in R11–R16. Recommend rejecting as contradictory (resume
  vs. fresh full run) for safety; alternative is "repeat wins." Needs a decision.
- **DB-state refusal exit code:** uniform `bad_argument`(2) recommended, but R5/R6/R12-differs are runtime
  conditions — confirm whether a distinct code (e.g. `runtime_error`/5 via a dedicated `SubmitRefused`
  exception in the mapping) is preferred for scripting/telemetry.
- **`--from-json` participation:** a `--from-json` SPEC references a named file but is currently
  incompatible with `--restart` (`:341`). Does source gating apply to `--from-json` sources, or are they
  exempt for now? GOAL centers on the plain taskfile; recommend deferring/exempting `--from-json` unless the
  plan says otherwise.
- **`hs submit -f -` / stdin:** confirm stdin (`check_source:1136-1144`) maps to the reserved `<stdin>`
  source and is exempt (R3/R4) even when `--update`/`--repeat` are also passed (recommend: flags are no-ops
  on stdin, possibly warn).
