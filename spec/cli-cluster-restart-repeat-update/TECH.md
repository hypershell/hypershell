---
slug: cli-cluster-restart-repeat-update
title: 'Safe re-submission: --restart / --repeat / --update source gating'
kind: feature
appetite: big
status: in_progress
branch: feature/cli-cluster-restart-repeat-update
base: develop
current_phase: P3
last_updated: '2026-07-11'
phases:
- id: P1
  name: Schema + fingerprint core (Source entity, Task.source/fingerprint, timescale
    groundwork, indices)
  status: done
  satisfies:
  - R1
  - R2
  - R17
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v -m unit tests/test_source.py
- id: P2
  name: Submit-flow stamping seam (GatedSource, Loader stamp+dedup mechanism, upfront
    read/count)
  status: done
  satisfies:
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v -m integration -k source_stamp
- id: P3
  name: hs submit gate matrix (R5-R10) + count-warn + logging + submit docs/completions
  status: done
  satisfies:
  - R5
  - R6
  - R7
  - R8
  - R9
  - R10
  - R18
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_submit.py -k gate
- id: P4
  name: hsx gate matrix + file-aware restart (R11-R16) + cluster docs/completions
  status: done
  satisfies:
  - R11
  - R12
  - R13
  - R14
  - R15
  - R16
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_restart.py
- id: P5
  name: Gate --from-json in both apps (human decision) + remove --from-json/--restart
    incompat
  status: done
  satisfies:
  - R4
  - R5
  - R8
  - R9
  depends_on:
  - P3
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v -k from_json_gate
- id: P6
  name: Presentation (source display) + man pages + full sphinx build + full pytest
    sweep
  status: done
  satisfies:
  - R18
  depends_on:
  - P4
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v && uv run sphinx-build docs docs/_build
review:
  last_reviewed_commit: 93476f1
  verdict: changes-requested
  blocked_reason: 'R17: count/dedup full-scan in data/model.py (partial-index predicate
    mismatch); R7: false-positive incomplete warning after dedup in submit.py'
---
# TECH.md — Safe re-submission: `--restart` / `--repeat` / `--update` source gating

The **context engine and finite-state machine** for building this feature. Frontmatter above is the
resume ground-truth (`uv run python .agents/factory/bin/next_phase.py
spec/cli-cluster-restart-repeat-update/TECH.md`); the per-phase checklists are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`09`.

## Conventions (apply to every phase)

- Invariants + commit/style rules come from [`AGENTS.md`](../../AGENTS.md); footgun checklist in
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md).
- One phase per `hs-build` invocation; one atomic commit with **both** code and the `TECH.md` state
  change; branch subjects `WIP: P<n> — …` (squashed at `hs-publish`, where the same-commit docs rule
  resolves). **No `Co-Authored-By` trailer.**
- Guard every source write / gate with `queue_config is None and not in_memory` (invariant §4).
- Refusals raise `cmdkit.cli.ArgumentError` → `exit_status.bad_argument` (2); never invent literals.
- The task fingerprint column is **`Task.fingerprint`** (not `identity` — too close to `id`), matching
  `Source.fingerprint`. All new tests tagged `@mark.unit` or `@mark.integration`.

---

## Phase P1 — Schema + fingerprint core + Timescale groundwork
**Satisfies:** R1, R2, R17 · **Depends on:** —
**Goal:** the DB substrate — a `Source` entity, `Task.source`/`Task.fingerprint` columns, indices, the
fingerprint computation, and the `timescale` provider alias/gate — landing on a fresh `hs initdb`, with
unit-tested fingerprint semantics. No submit behavior change yet (columns populate only when a
source/raw_args is supplied).

- [x] **Timescale groundwork (`data/core.py`):** add `'timescale'`/`'timescaledb'` → `'postgresql+psycopg'`
  to `providers` (`:173`); accept the aliases wherever `config.provider == 'postgres'` is special-cased
  (`get_url` file-pop, `:227`). Add a **`uuid7`-extra gate** (mirror the turso gate `:207-212`): if
  provider ∈ timescale aliases and `import uuid_utils` fails → `display_critical(...)` +
  `sys.exit(exit_status.runtime_error)`. Emit a one-line groundwork warning that Timescale hypertable
  management (post-`create_all` hook) is not yet implemented. **Do not touch `core/uuid.py`.**
- [x] Module constants `DIRECT_SOURCE_ID` / `STDIN_SOURCE_ID` (fixed well-known UUIDs) in `data/model.py`.
- [x] Add **`Source`** entity (mirror `Client`): `id` `UUID` pk (value from `uuid()`), `path` `TEXT`,
  `fingerprint` `TEXT`, `task_count` `INTEGER`, `created` `DATETIME`; `columns` dict; inner
  `NotFound/NotDistinct/AlreadyExists`; `from_id`; `new(path, fingerprint, task_count)` →
  `id=uuid()`, `created=datetime.now().astimezone()`. No lineage-pointer column (lineage = same `path`).
- [x] Add `Task.source` (shared **`UUID`** type, nullable — native on postgres, holds a real
  `Source.id`/reserved-const id) and `Task.fingerprint` (`TEXT`, nullable); append both to `Task.columns`
  (`model.py:281`) for wire round-trip. (Accept raw UUID/md5 auto-appearing in `hs list --json/csv` /
  `-x`; human-readable resolution is P6.)
- [x] `Task.compute_fingerprint(raw_command, group, tags) -> str` (md5 of canonical JSON of
  `{'args','group','tags'}` with `sort_keys`, `part` dropped from tags). Add `raw_args`, `fingerprint`,
  `source` kwargs to `Task.new`; derive `raw_command` (`split_argline(raw_args)[0]` for line,
  `raw_args` for JSON, fallback `args`); compute+set `fingerprint` when not supplied; pass `source`.
- [x] Propagate on retry: `__schedule_next_failed_tasks` (`model.py:555`) passes
  `fingerprint=task.fingerprint` **and** `source=task.source`.
- [x] Classmethods: `Source.lookup(path)`, `Source.matching(path, fingerprint)`,
  `Source.reserved(const_id)` (lazy get-or-create by PK; `path` = sentinel), `Source.paths_for_ids(ids)`
  (presentation), `Task.count_for_source(id)`, `Task.fingerprints_for_sources(ids) -> set[str]`.
- [x] Indices: `index_source_lookup(Source.path, Source.fingerprint)`;
  `index_tasks_source(Task.source, Task.fingerprint)` — **full covering, non-partial** (revised in F1
  remediation: a partial predicate can't be honored for parameter-bound `source` lookups → silent full
  scan). **No BRIN** (lean on Timescale compression).
- [x] New `tests/test_source.py` (unit): fingerprint order-independent over tag order; stable across
  template change + differing uuid/attempt; differs on args/group/tag change; `part`/resource knobs
  excluded; fresh `initdb` yields the `source` table + indices (SQLAlchemy inspect).
- **Verify:** `uv run pytest -v -m unit tests/test_source.py`.
- **Touches:** `src/hypershell/data/core.py`, `src/hypershell/data/model.py`, `tests/test_source.py`.
- **Remediation (review cycle 1 — F1, R17):** `index_tasks_source` was made **non-partial**. The
  partial `WHERE source NOT IN (<direct>,<stdin>)` predicate is only honored when a query repeats it
  with literals, but `count_for_source`/`fingerprints_for_sources` bind `source` as a parameter — which
  neither SQLite nor PostgreSQL can prove satisfies a literal partial predicate, so the lookups
  silently degraded to a full table `SCAN` (verified via `EXPLAIN QUERY PLAN` at 300k rows). A full
  covering `(source, fingerprint)` index is used with plain bound parameters on every engine (the
  reserved-source rows it also carries are a marginal cost — named-file sources dominate the target
  workload). Added a structural test (index is full, not partial) + a query-plan regression test that
  asserts the lookups reach the index, not a scan.

## Phase P2 — Submit-flow stamping seam
**Satisfies:** R3, R4 · **Depends on:** P1
**Goal:** named-file submissions create a `Source` row and stamp every task with `source` +
`fingerprint`, via a seam that also supports de-dup — without threading kwargs through the cluster core.
Reserved `<direct>`/`<stdin>` stamped (real rows) and exempt. No refuse/repeat/update decisions yet.

- [x] Add **`GatedSource(iterable, source_id, skip_fingerprints=None, name=None)`** in `submit.py`
  (`__iter__` delegates; exposes `.source_id`/`.skip_fingerprints`/`.name`).
- [x] `Loader.__init__` unwraps `GatedSource` (store `source_id`/`skip`, then `iter(inner)`). In
  `load_line_task`/`load_json_task` pass `raw_args`; set `task.source = self.source_id`; after build, if
  `self.skip and task.fingerprint in self.skip` → skip (`GET`, no `put_task`, no `count++`), logging the
  running present/new tally (`Loader.skip_task`; tally logged in `finalize` when de-dup is active).
  `SubmitThread`/`LiveSubmitThread.source_name` surface `GatedSource.name`.
- [x] `source_fingerprint_and_count(path, template) -> (md5_hex, count)`. **Amended (review):** *two*
  streaming reads, not one — `hashlib.md5` over raw bytes **plus** a separate **text-mode** count read so
  the count uses the Loader's own universal-newline decoding (a single binary pass split only on `\n` and
  mis-counted CR-only / mixed-newline files → wrong `task_count`). Count uses the shared `line_is_task`
  predicate. Non-seekable named inputs (process substitution / FIFO / piped `/dev/stdin`) are **not**
  routed here (a re-read would drain them) — `prepare_source` streams them as `<stdin>` instead.
- [x] `SubmitApp.check_source`: capture `os.path.abspath(filepath)` for named files. For stdin /
  single-command, resolve the reserved source id via `Source.reserved(STDIN_SOURCE_ID)` /
  `Source.reserved(DIRECT_SOURCE_ID)` (get-or-create). `submit_one` stamps
  `source=Source.reserved(DIRECT_SOURCE_ID)`. **Amended (review):** the seekable/non-seekable split lives
  in the new `prepare_source` (non-seekable named input → reserved `<stdin>`, exempt & streamed).
- [x] Minimal wiring so a plain `hs submit -f FILE` (no new flags) creates a `Source` row (expected
  count written **before** tasks; guarded by DB-mode) and hands a `GatedSource(source_id=<uuid>)` to
  `submit_from`. `<stdin>` → `GatedSource(source_id=Source.reserved(STDIN_SOURCE_ID))`.
- [x] Integration test (`test_submit.py` or `test_source.py`): after `hs submit -f f.in`, tasks carry
  a non-null `source`/`fingerprint`; a `source` row exists with `task_count` == parsed task count;
  blank/comment/inline-tag-only lines excluded from the count. Plus two review-driven regression tests:
  non-seekable input streams all tasks (not drained); CR-only newline count matches submitted tasks.
- **Verify:** `uv run pytest -v -m integration -k source_stamp`.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/data/model.py` (helpers if needed),
  `tests/test_submit.py`.

## Phase P3 — `hs submit` gate matrix
**Satisfies:** R5, R6, R7, R8, R9, R10, R18 · **Depends on:** P2
**Goal:** `hs submit` enforces the plain-file matrix and logs its reasoning.

- [x] Add `--repeat`/`--update` (`store_true`) to `SubmitApp`; add `check_arguments` rejecting
  `--update`+`--repeat` (R10). Register in usage/help interface strings.
- [x] Implement shared **`apply_source_gate(path, fingerprint, count, *, repeat, update, restart=False)`**
  in `submit.py`: returns `(source_id, skip_fingerprints)` and raises `ArgumentError` on refusal.
  Decisions: no-flag + match → refuse naming prior (R5); no-flag + path-seen-fp-differs → refuse suggest
  `--update` (R6); count-warn when `Task.count_for_source(prior) < prior.task_count` (R7); `--repeat` →
  new source, `skip=None` (R8); `--update` → new source,
  `skip=Task.fingerprints_for_sources(Source.lookup(path))` (R9). Create the `Source` row (expected count
  first). Emit R18 logs (found N + md5, prior source, present/new, refusal reason).
- [x] `SubmitApp.run` calls `apply_source_gate` for named files (DB path only), wraps source in
  `GatedSource`. Exempt: `-q`, `--no-db`/in-memory sqlite, `<stdin>`, `<direct>`.
- [x] Docs + completions (submit): `docs/_include/submit_{help,usage,desc}.rst`;
  `share/bash_completion.d/hs` (`_hs_submit`), `share/zsh/site-functions/_hs` (`_hs_submit`) —
  add `--repeat`/`--update` (+ zsh mutual-exclusion pair). Keep interface strings + snippets in lockstep.
- [x] Integration tests (`test_submit.py`): R5 refuse, R6 suggest-update, R7 count-warn (mechanism:
  cancel/delete a subset then re-detect), R8 doubles, R9 novel-only, R10 non-zero; `assert_output` on
  the R18 logs; R3 exempt (`hs submit 'echo x'` twice both succeed; `seq 4 | hs submit -f -` twice both
  succeed).
- **Verify:** `uv run pytest -v tests/test_submit.py -k gate`.
- **Touches:** `src/hypershell/submit.py`, `docs/_include/submit_*.rst`,
  `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`, `tests/test_submit.py`,
  `tests/test_groups.py` (R6 now refuses re-submitting changed content at a seen path — two batches
  given distinct source filenames). **Build note:** `apply_source_gate` implements the full shared
  matrix now, including the `restart` branches (R12/R14) that P4 wires into `ClusterApp`.

## Phase P4 — `hsx` gate matrix + file-aware restart
**Satisfies:** R11, R12, R13, R14, R15, R16 · **Depends on:** P3
**Goal:** `hsx`/`hs cluster` enforces the full matrix; `--restart` becomes file-aware and idempotent.

- [x] Add `--repeat`/`--update` to `ClusterApp`. `check_arguments`: `--update` without
  (`--restart`|`--repeat`) → R13; `--update`+`--repeat` → R16; **`--restart`+`--repeat` → contradictory**
  (human decision); `--update` with `--no-db` → refuse. Keep `--forever`+`--restart`,
  `--no-db`+`--restart` refusals.
- [x] Make `ClusterApp.source`/`input_stream` (`cluster/__init__.py:422-441`) file-aware: bare
  `--restart` (no filepath) → `source==[]` (legacy DB resume, unchanged); with a filepath under
  restart/update/repeat → read+fingerprint and yield a `GatedSource` per `apply_source_gate` (restart
  semantics: fp match → `skip=lineage fingerprints` (R12) relying on `revert_interrupted`; fp differs →
  refuse suggest `--update`; `--update --restart` → new source + novel-only (R14); `--repeat` → new
  source, all (R15)). No-flag hsx file reuses R5–R7 (R11). New `ClusterApp.prepare_source` mirrors
  `SubmitApp.prepare_source`; the upfront count uses `DEFAULT_TEMPLATE` (the server ingests raw lines —
  it expands the user `--template` client-side, so the count must mirror that split, not `--template`).
- [x] **Amendment (build): fix a pre-existing scheduler start-race in `server.py`** (see build note).
- [x] Confirm new flags are **not** added to any client argv builder (`remote.py`, `ssh.py`) — verified
  clean (only the existing server-side `restart_mode` plumbing is present).
- [x] Docs + completions (cluster): `docs/_include/cluster_{help,usage,desc}.rst`; `_hs_cluster` in
  bash + zsh completions. Revise the `--restart` narrative (detection/dedup semantics).
- [x] New `tests/test_restart.py` (integration): R11 refuse; R12 idempotent restart (run twice; changed
  fp → refuse); R13 ambiguous; R14 update+restart adds novel + runs; R15 repeat resubmits all;
  `--restart --repeat` non-zero; + a scheduler-race regression (new file runs against an all-completed DB).
- **Verify:** `uv run pytest -v tests/test_restart.py` — 9 passed. Full suite 343 passed; docs build clean.
- **Touches:** `src/hypershell/cluster/__init__.py`, `src/hypershell/server.py`,
  `docs/_include/cluster_*.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`,
  `tests/test_restart.py`.
- **Build note (amendment):** wiring a file-aware `--restart`/`--repeat`/`--update` into the cluster
  gives the `ServerThread` **both** a live submitter *and* `restart_mode` — a combination the prior code
  never produced (bare `--restart` had `source==[]`, no submitter). This exposed a pre-existing race:
  `Scheduler.start()`/`load_bundle()` finalize as soon as the DB shows `total>0, remaining==0`, which is
  exactly how a database of prior **completed** tasks reads right up until the submitter commits its
  first novel row — so the scheduler could stop before R12/R14/R15 (and even a plain new-file `hsx`)
  ever ran their tasks (confirmed by CLI: 4 novel tasks submitted, 0 run). Fix: `Scheduler` now takes a
  `submitter` reference and a `submission_complete()` guard (`submitter is None or not is_alive()`); both
  early-exit points defer while ingestion is in flight. Bounded, additive, and covered by the new
  regression test; no lifecycle-predicate or retry change (invariants §1/§3 intact).

## Phase P5 — Gate `--from-json` (human decision)
**Satisfies:** R4, R5, R8, R9 (as applied to `--from-json`) · **Depends on:** P3, P4
**Goal:** extend the source gate to `--from-json` in both apps and remove its `--restart` incompatibility.

- [x] `--from-json` source key: `path = abspath(FILE)[+ '@' + node]` (new `json_source_key`),
  `fingerprint = md5(FILE bytes)`, `count = len(records)`. **Amended (build):** md5 captured in a new
  `load_json_source(spec) -> (records, md5)` (one byte read → md5 + `json.loads`, no 2nd pass);
  `load_json_tasks` kept as a thin wrapper. `--from-json -` (stdin JSON) → `json_source_key`=None, exempt.
- [x] Feed the JSON source through `apply_source_gate` in `SubmitApp.prepare_json_source` and
  `ClusterApp.prepare_json_source`; wrap the records list in a `GatedSource` (dedup filters records by
  fingerprint — JSON fingerprint uses `base` args, `parse_inline=False`, per research/01). Stdin JSON and
  `--no-db`/non-persistent runs bypass the gate (reserved `<stdin>` source stamped where applicable).
- [x] Remove the `--from-json`+`--restart` refusal (was `cluster/__init__.py:352-353`); `hsx --from-json …
  --restart` / `--update --restart` / `--repeat` now flow through the same matrix (R11-R15).
- [x] Docs: note `--from-json` participates in gating (`submit_desc.rst` + `cluster_desc.rst`).
- [x] Integration tests (`tests/test_submit_json.py`): `hs submit --from-json` twice → refuse (R5);
  `--repeat` → doubles (R8); edited spec + `--update` → novel-only (R9); `hsx --from-json … --restart`
  idempotent (R12, also proves the removed incompat).
- **Verify:** `uv run pytest -v -k from_json_gate` — 4 passed. JSON+restart+submit suites 73 passed;
  cross-tool CLI drive confirmed (same md5 via `hs submit` and `hsx`; restart deduped to 0 new).
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/cluster/__init__.py`,
  `docs/_include/*_desc.rst`, `tests/`.
- **Ridealong fix (post-P5, separate `[fix]` commit):** `--from-json` was **never** listed in the
  shell completions — a pre-existing gap (the flag predates this feature; P3/P4 added `--repeat`/
  `--update` but not the already-missing `--from-json`), surfaced while closing the P5 surface. Fixed
  now, riding along with the feature: `--from-json` added to `_hs_submit` and `_hs_cluster` in both
  `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs` (offered in the option list, completes
  a file path/SPEC, mutually exclusive with `-f`/positional FILE per the CLI). Verified `bash -n`/`zsh -n`
  clean + functional bash-completion drive. **The man-page instance of the same gap is folded into P6.**

## Phase P6 — Presentation + man pages + full build + full sweep
**Satisfies:** R18 · **Depends on:** P4, P5
**Goal:** resolve `source` for humans, finish the same-commit surface, prove the whole feature green.

- [x] Presentation: `format_source(path, *, relative=False)` in `core/pretty_print.py` (sentinels
  `^<.*>$` pass through; real paths absolute; `@node` json specs opaque — never relativize); added a
  resolved `source:` line to `NORMAL_MODE_TEMPLATE` (after `group`); resolve via a new module helper
  `resolve_source(source, source_map=None)` used by module `print_normal` (single `Source.from_id`,
  degrades to raw id on `NotFound`) and `TaskSearchApp.print_normal` (one batched `Source.paths_for_ids`
  per page via the `source_map` arg — avoids N+1). **`fingerprint` kept out** of the normal template
  (reachable via `-x`/json/csv). Machine formats keep the raw UUID. CLI-verified: named file → abspath,
  `<direct>`/`<stdin>` sentinels pass through, `-x source`/explicit-field stay raw.
- [x] Regenerated man pages from source via the Sphinx **man builder** (`sphinx-build -b man`, from
  `docs/manual.rst` → the P3–P5-updated `_include` snippets) rather than hand-editing roff — the
  committed pages were stale (zero mentions of `--repeat`/`--update`/`--from-json`). All three shipped +
  CI-asserted pages regenerated (`hs.1`, `hyper-shell.1`; `hsx.1` kept a byte-copy of `hs.1` per the
  existing convention — no `hsx` entry in `conf.py`). New flags + revised `--restart` narrative + the
  pre-existing-missing `--from-json` now present. CI list + `pyproject.toml` mapping unchanged (no new
  files); mandoc lint clean (only pre-existing >80-byte STYLE notes).
- [x] `uv run sphinx-build docs docs/_build` — build succeeded; **only** the pre-existing
  `task_submit.rst`/`manual.rst` toctree warnings (no new warnings).
- [x] `bash -n share/bash_completion.d/hs`; `zsh -n share/zsh/site-functions/_hs` — both clean;
  `--from-json`/`--repeat`/`--update` still present (ridealong + P3/P4), nothing regressed.
- [x] Full `uv run pytest -v` green; added edge cases: `format_source` unit test + two presentation
  integration tests (normal-view resolves single+batched, machine formats stay raw, `<direct>` sentinel)
  in `test_source.py`, and `--update` on an unseen path == submit-all (R9 edge) in `test_submit.py`.
- **Verify:** `uv run pytest -v && uv run sphinx-build docs docs/_build` — 351 passed; docs build clean
  (2 pre-existing warnings only).
- **Touches:** `src/hypershell/core/pretty_print.py`, `src/hypershell/task.py`,
  `share/man/man1/hs.1`, `share/man/man1/hsx.1`, `share/man/man1/hyper-shell.1`,
  `tests/test_source.py`, `tests/test_submit.py`.
- **Build note (amendment):** man pages are **generated artifacts** (Sphinx man builder, mapped in
  `docs/conf.py:126`), not hand-maintained roff — the correct P6 action was to regenerate from the RST
  source that P3–P5 already updated. Extended the checklist's `hs.1`/`hsx.1` to also cover the equally-
  stale, equally-shipped `hyper-shell.1` (leaving it would ship an inconsistent legacy man page).
  Research 08's "update `_include` normal-output examples" had **no applicable target**: no `_include`
  snippet renders an `hs info` normal block (only dated blog release posts do, which are historical
  records and must not be retro-edited).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative; `current_phase`
   reconciled).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]`; consult `PLAN.md` / `research/` for detail.
4. Run the phase `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (`set_phase.py`; note in commit body). STOP + escalate only on a
   **`GOAL.md` contradiction**.
6. Mark phase `done`, advance `current_phase`, `--touch`; one `WIP:` commit; stop and report.
