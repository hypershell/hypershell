# PLAN — Safe re-submission: `--restart` / `--repeat` / `--update` source gating

> **Status:** Draft for review · **Last updated:** 2026-07-11
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Give the database a **`source`** record per ingested named file (uuid, absolute path, content md5,
task count, timestamp) and stamp every task with its **`source`** (a native `UUID`) and a stable
**`fingerprint`** — a canonical, order-independent md5 of pre-template `args` + `group` + `tags` (named
`fingerprint`, mirroring `Source.fingerprint`; *not* `identity`, which reads too close to `id`).
Submission consults these — cheaply, via an indexed `Source` lookup — to proceed, de-dup, or refuse.
The schema is additive (fresh `hs initdb`, no backfill); the gate context rides **inside the `source`
iterable** (a `GatedSource` wrapper) so it reaches the one stamping chokepoint (`Loader`/`Task.new`)
without threading kwargs through the ~10 coupled-core constructors. Appetite is *big*, surface bounded:
DB-layer groundwork, two model additions, one submit-flow seam, two CLI matrices, docs/completions/tests.

## 2. Design

### Database layer (`src/hypershell/data/core.py`) — Timescale groundwork

- **New provider alias `timescale` / `timescaledb`** in the `providers` map (`data/core.py:173`) →
  `'postgresql+psycopg'` (TimescaleDB *is* PostgreSQL). Wherever `config.provider == 'postgres'` is
  special-cased (the `get_url` file-pop at `data/core.py:227`), accept the timescale aliases too;
  `DATABASE_DIALECT` (`data/__init__.py:50`) already routes any non-sqlite provider to `'postgres'`, and
  `set_sqlite_pragmas` is unaffected.
- **`uuid7`-extra dependency gate, active only in timescale mode** — mirrors the existing turso import
  gate (`data/core.py:207-212`) and psycopg gate: if `config.provider in ('timescale','timescaledb')`
  and `import uuid_utils` fails, `display_critical(...)` + `sys.exit(exit_status.runtime_error)`. This
  **guarantees the existing `uuid()` yields UUIDv7 in timescale mode** (it already prefers
  `uuid_utils.uuid7`, `core/uuid.py:8-11`), giving the orderable `source` column that TimescaleDB's
  compressed per-batch min/max prunes on. Also emit a one-line groundwork warning that Timescale-specific
  management (e.g. an automatic post-`create_all` hypertable hook) is **not** in this feature.
- **No change to `core/uuid.py`.** `Source.id` (and Task/Client ids) use the existing `uuid()`:
  UUIDv7 when the `uuid7` extra is present (guaranteed in timescale mode; the default in the dev/full
  install), plain `uuid4` otherwise — which is fine, because non-timescale deployments aren't hypertables
  and rely on the B-tree, not min/max pruning.

### Data model (`src/hypershell/data/model.py`)

- **New `Source` entity** (mirrors `Client`): `id` `UUID` (pk, `uuid()`), `path` `TEXT`, `fingerprint`
  `TEXT` (content md5 hex), `task_count` `INTEGER`, `created` `DATETIME`. Own `columns` dict, inner
  `NotFound/NotDistinct/AlreadyExists`, `from_id`, `new(path, fingerprint, task_count)`
  (`id=uuid()`, `created=now().astimezone()`), `reserved(<const-id>)` (lazy get-or-create),
  `paths_for_ids(ids)` (presentation), + the query classmethods below. **No lineage pointer column** —
  lineage = *all sources sharing `path`* (R9/R14).
- **Reserved sources are real rows.** Module constants `DIRECT_SOURCE_ID`/`STDIN_SOURCE_ID` (fixed
  well-known UUIDs) identify single lazily-created `Source` rows whose `path` holds `<direct>`/`<stdin>`.
  Fixed constants ⇒ idempotent get-or-create by PK **and** a statically-excludable set for the partial
  de-dup index. Gating exemption is decided at the **App layer** by submission mode, not by inspecting
  `Task.source`.
- **New `Task` columns** (append + add to `Task.columns` at `model.py:281` for wire round-trip):
  - `source` **shared `UUID`** type (native on postgres), nullable — holds a real `Source.id` (or a
    reserved-const id); NULL only for historical rows. Native UUID (not TEXT) is what carries the
    orderable column stats Timescale compression prunes on.
  - `fingerprint` `TEXT` nullable — the stable task fingerprint below (R2). NULL for historical rows.
- **`Task.compute_fingerprint(raw_command, group, tags) -> str`** classmethod:
  `md5(json.dumps({'args': raw_command, 'group': group, 'tags': {k:v for k,v in tags.items() if k!='part'}},
  sort_keys=True, separators=(',',':'), ensure_ascii=False)).hexdigest()`. Called from `Task.new` after
  tag/group assembly. `Task.new` gains `raw_args`, `fingerprint`, `source` kwargs;
  `raw_command = split_argline(raw_args)[0]` (line) or `raw_args` (JSON, `parse_inline=False`), falling
  back to `args` when `raw_args is None`. Retry minter `__schedule_next_failed_tasks` (`model.py:555`)
  passes `fingerprint=task.fingerprint` **and** `source=task.source` so a chain keeps one fingerprint +
  its source (R2/R3 for retries).
- **Query classmethods** (all state/query logic stays in the model, invariant §1):
  `Source.lookup(path) -> list[Source]` (same-path lineage, newest first),
  `Source.matching(path, fingerprint)`, `Task.count_for_source(id) -> int` (R7),
  `Task.fingerprints_for_sources(ids) -> set[str]` (R9/R12/R14 skip-set).
- **Indices (R17):** `Index('index_source_lookup', Source.path, Source.fingerprint)` (btree) and
  `Index('index_tasks_source', Task.source, Task.fingerprint)` — composite btree, **partial excluding
  `DIRECT_SOURCE_ID`/`STDIN_SOURCE_ID`** (`postgresql_where` / `sqlite_where`). **No BRIN / no
  TSDB-specific code** (maintainer decision): chunk pruning at scale comes from Timescale's compressed
  per-batch min/max on the orderable UUIDv7 `source`; the btree satisfies R17 on its own (cost scales
  with lineage matches + chunk count, not total rows).
- `Entity.metadata.create_all` picks these up on a fresh `initdb`; existing `task` tables are never
  ALTERed (no-backfill non-goal, no Alembic).

### Submit-flow seam (`src/hypershell/submit.py`, `src/hypershell/data/__init__.py`)

- **`GatedSource(iterable, source_id, skip_fingerprints=None, name=None)`** — small wrapper: `__iter__`
  delegates, exposes `.source_id`/`.skip_fingerprints`/`.name`. `Loader.__init__` unwraps it (reads
  `source_id`/`skip`, then `iter(inner)`); `SubmitThread`/`LiveSubmitThread.source_name` surface
  `.name`. Everything between (`submit_from`, `SubmitThread`, `ServerThread`, `LocalCluster`,
  `RemoteCluster`, `AutoScalingCluster`) passes `source` opaquely — **unchanged**.
- **`Loader`** (`submit.py:164-205`): for each built `Task`, set `task.source = self.source_id`, pass
  `raw_args` so the fingerprint is computed/stamped; if `self.skip and task.fingerprint in self.skip`,
  skip (don't `put_task`, don't increment `count`) — logging the running "M present / L new" tally.
- **Upfront read helper:** `source_fingerprint_and_count(path, template) -> (md5_hex, count)` — one
  streaming pass: `hashlib.md5(raw bytes)` + count lines where
  `bool(split_argline(template.expand(line.strip()))[0])`. The task-or-not predicate is factored into a
  shared helper used by both this pass and the `Loader`. `<stdin>` is not seekable ⇒ exempt.
- **`apply_source_gate(...)`** — shared decision function called by both apps (in `submit.py`, which
  `cluster/__init__.py` already imports). Inputs: resolved `path`, `fingerprint`, `count`, flags
  (`repeat`/`update`/`restart`). Returns the new `source_id` (fresh `Source` row, expected count written
  first) and `skip_fingerprints` (`None`, or `Task.fingerprints_for_sources(lineage_ids)` for de-dup);
  emits R18 logs + R7 count-warn; raises `ArgumentError` on refusal. Guarded by
  `queue_config is None and not in_memory`.
- **`SubmitApp`**: `check_source` captures `os.path.abspath(filepath)` for named files; stdin /
  single-command resolve the reserved source via `Source.reserved(STDIN_SOURCE_ID)` /
  `Source.reserved(DIRECT_SOURCE_ID)`. `run` builds the `GatedSource` from the gate result.
- **`ClusterApp`**: `source`/`input_stream` become file-aware; `run` builds the `GatedSource` and passes
  it as `source` to the launcher (opaque the rest of the way).

### CLI surface

- **`hs submit`**: add `--repeat`/`--update` (`store_true`). New `check_arguments`: `--update
  --repeat` → `ArgumentError` (R10). Gate matrix R5–R9 via `apply_source_gate`. `-q`/`--no-db`/stdin/
  single-cmd exempt.
- **`hsx` / `hs cluster`**: add `--repeat`/`--update`. `check_arguments`: `--update` without
  (`--restart`|`--repeat`) → R13; `--update --repeat` → R16; `--restart --repeat` → contradictory
  (**human decision**); `--update` with `--no-db` → refuse. `--restart` becomes file-aware (R12/R14);
  bare `--restart` (no file) unchanged. Keep existing `--forever`/`--no-db` + `--restart` refusals.
- **Refusals** → `ArgumentError` → `exit_status.bad_argument` (2), uniform. Warnings via `log.warning`.
- **`--from-json` is gated** (human decision): source `path = abspath(FILE)[+'@'+node]`, `fingerprint =
  md5(FILE bytes)`, `count = len(records)`; records are already in memory (no 2nd pass). The
  `--from-json`+`--restart` incompatibility (`cluster/__init__.py:341`) is **removed**. `--from-json -`
  (stdin JSON) stays exempt.

The authoritative refuse/warn/de-dup decision table (per prior-state × flags, both apps) is in
[`research/05-cli-gate-matrix.md`](research/05-cli-gate-matrix.md).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `Source` entity (uuid/path/fingerprint/task_count/created); row written before tasks with expected count. |
| R2 | `Task.fingerprint` + `Task.compute_fingerprint` (canonical md5 of pre-template `args`+`group`+`tags`−`part`); excludes uuid/attempt/timing/exit/template by construction. |
| R3 | `Task.source` (native UUID) stamped in `Loader`/`Task.new`; reserved `<direct>`/`<stdin>` are real `Source` rows (fixed-const ids) via `Source.reserved(...)`; both exempt (App-layer, by mode); retries propagate `source`. |
| R4 | `source_fingerprint_and_count` upfront read of named files (md5 + count); `<stdin>` streamed & exempt. |
| R5 | `apply_source_gate`: `Source.matching(path,fp)` hit + no flag → `ArgumentError` naming prior. |
| R6 | path seen (`Source.lookup`) + fp differs + no flag → `ArgumentError` suggesting `--update`. |
| R7 | detection compares `Source.task_count` vs `Task.count_for_source` → `log.warning` if fewer landed. |
| R8 | `--repeat` → new `Source`, `skip_fingerprints=None`, submit all. |
| R9 | `--update` → new `Source`, `skip_fingerprints = Task.fingerprints_for_sources(lineage)`; Loader skips present. |
| R10 | `hs submit` `check_arguments`: `--update`+`--repeat` → `ArgumentError`. |
| R11 | `hsx` no-flag path reuses `apply_source_gate` (== R5–R7). |
| R12 | `hsx --restart`: no refuse on seen; fp match → novel-only (skip-set) + rely on `revert_interrupted`; fp differs → `ArgumentError` suggest `--update`. |
| R13 | `hsx` `check_arguments`: `--update` without `--restart`/`--repeat` → `ArgumentError`. |
| R14 | `hsx --update --restart`: new `Source`, novel-only in same-path lineage. |
| R15 | `hsx --repeat`: new `Source`, submit all even on match; new `-t` tags flow through unchanged. |
| R16 | `hsx` `check_arguments`: `--update`+`--repeat` → `ArgumentError` (and `--restart`+`--repeat`). |
| R17 | `index_source_lookup` + partial composite btree `index_tasks_source`; detection hits tiny `Source` table + per-source COUNT; de-dup scoped to lineage (not total tasks); UUIDv7 `source` (timescale mode) lets Timescale compression prune chunks (no BRIN/TSDB code). |
| R18 | `apply_source_gate`/`Loader` log points: found N (md5) · prior source · incomplete-warn · M present/L new · refusal reasons. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
and again after this design.

- **§1 Task lifecycle** — `source`/`fingerprint` are additive and orthogonal to the nullable-column
  state predicates; no query changes those predicates. All new query/state logic lives in
  `Source`/`Task` classmethods. De-dup only *skips submission*; it never sets `schedule_time` without
  `completion_time`.
- **§3 Retry model** — the fingerprint excludes attempt/retry/uuid/`previous_id`/`next_id`, so a retry
  shares its parent's fingerprint; `fingerprint`/`source` are non-unique columns independent of the
  UNIQUE `previous_id`/`next_id` chain. Retry minter propagates both (change lands with the model change).
- **§4 Server modes** — every source write/gate guarded by `queue_config is None and not in_memory`
  (non-goal: no source tracking for `in_memory`; queue-mode writes no rows).
- **§6 Shutdown/sentinel ordering** — `GatedSource` wraps only the *source* iterable; the stream
  sentinel, bundle serialization, and thread-join order are untouched. Wrapper is never enqueued.
- **§9 Queue transport** — `source`/`fingerprint` added to `Task.columns` so JSON `to_json`/`from_json`
  round-trip over the remote queue; no wire-format or TLS change.
- **§11 Cluster** — new flags are submit/server-side and are **not** added to any client argv builder;
  the `GatedSource` seam specifically avoids replicating a kwarg across local/remote/ssh/autoscale.
- **§12 Conventions** — refusals reuse `exit_status.bad_argument`; docs `_include` snippets + `share/`
  completions updated in the CLI phases; md5/hashlib is stdlib (no new dep); the `timescale` provider
  gates the existing `uuid7` extra (no new *runtime* dep, EPEL floors untouched); tests tagged
  `@mark.unit`/`@mark.integration`.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `GatedSource` carries gate context *inside* the `source` object | Threading `source_id`+`skip` through `Loader`/`SubmitThread`/`ServerThread`/`LocalCluster`/`RemoteCluster`/`AutoScalingCluster`/`submit_from` = ~10 coupled-core signatures (the §11 replication footgun) | Explicit kwarg threading: wider, easy to miss one launcher, and mutates high-blast-radius `server.py` + all cluster classes for a value they only forward. The wrapper confines the change to `Loader`/`Task.new`. |
| De-dup skip-set = **all** fingerprints in the same-path lineage (strategy (a)) loaded once | Single fingerprint-computation site (Loader), R17-compliant (scales with lineage via `index_tasks_source`, not total tasks); lineage is a *small* set of sources ⇒ a single small-`IN` query | Batched anti-join (c) / temp-table (d) need App-layer fingerprint recomputation = duplicating the stateful global-tag machine (§1 risk); with a small lineage they buy nothing. |

Empty is the goal; these are deliberate and bounded, and none bends a CRITICAL invariant. (Reserved
sources as real UUID rows, `Task.source` as native `UUID`, and using the existing `uuid()` gated by the
`timescale` provider — rather than a forced hand-rolled `uuid7()` — are all *simplifications* per
maintainer review, not deviations.)

## 4. Rabbit holes (resolved)

- **Where is "pre-template args"?** Template expansion happens in the `Loader` *before* `Task.new`, and
  `Task.args` stores the expanded string → pre-template args isn't recoverable from the row. Resolved:
  thread the raw line via `raw_args` and compute the fingerprint in `Task.compute_fingerprint`
  ([`research/01`](research/01-identity-fingerprint.md)).
- **Which tags feed the fingerprint?** Final tag dict minus `part`; group/resource knobs auto-excluded
  by the existing pop. Re-tagging ⇒ new fingerprint (GOAL-accepted) ([`research/01`](research/01-identity-fingerprint.md)).
- **Reserved sources on postgres.** Resolved by making them **real `Source` rows with fixed-const
  UUIDs**, so `Task.source` stays a **native `UUID`** (no TEXT sentinel hack)
  ([`research/07`](research/07-uuidv7-source-ids.md), [`research/09`](research/09-pruning-adversarial-check.md)).
- **Fast at trillions of rows.** Detection touches the tiny `Source` table, not `task`; de-dup scoped
  to a small lineage. A plain btree does **not** min/max-prune — the lever is Timescale compressed
  per-batch min/max on the **orderable UUIDv7 `source`** (timescale mode, operator config, no BRIN/TSDB
  code); the btree alone still meets R17 ([`research/09`](research/09-pruning-adversarial-check.md)).
- **UUIDv7 without forcing it.** `uuid.uuid7()` is stdlib only in 3.14 and `uuid()` defaults to `uuid4`;
  resolved by a `timescale` provider alias that **gates the `uuid7` extra** (bail like psycopg), so
  `uuid()` yields v7 exactly where it matters — no forced hand-roll ([`research/07`](research/07-uuidv7-source-ids.md)).
- **Coupled-core plumbing.** `GatedSource` rides inside `source` ([`research/04`](research/04-submit-flow.md) + planner analysis of `server.py`/`cluster/*`).
- **Current `--restart` ignores the file** (`cluster/__init__.py:437`, `source==[]`). Made file-aware
  while preserving bare `--restart` DB-resume ([`research/05`](research/05-cli-gate-matrix.md)).

## 5. Risks & open questions

- **TimescaleDB pruning is a timescale-mode property, not correctness.** Chunk pruning of `WHERE source
  IN (lineage)` comes from **compressed per-batch min/max** on the orderable UUIDv7 `source` — which is
  *guaranteed* in `timescale` mode (the `uuid7` extra is gated) and provided by the operator's
  compression policy (HyperShell ships no TSDB code). A plain btree does **not** prune by min/max, and
  `enable_chunk_skipping` can't index `uuid`. In non-timescale deployments `source` may be `uuid4` and
  there's no hypertable — de-dup stays **correct** via the btree (R17 met — scales with lineage + chunk
  count, not total rows). Fixed-const reserved ids cluster under `source`-ordered compression and are
  excluded from the partial index.
- **Timescale groundwork only:** this feature adds the provider alias + `uuid7` gate + a warning; the
  automatic post-`create_all` hypertable hook (and other TSDB gates) are **deferred** to a future
  feature.
- **De-dup memory** = one file-history's fingerprints (small lineage). Fine for the requeue case;
  **log** the lineage/skip-set size so large loads are visible.
- **R7 test determinism:** how to construct an "incomplete prior submission" deterministically —
  settled in the phase that tests R7 (delete/cancel a subset, or interrupt a run).
- **`hs server FILE`** remains ungated (out of scope). Its tasks carry NULL source; a later `hs submit`
  of the same file won't detect them. Acceptable for the advanced/direct entry.
- **Presentation:** raw `source` UUID / `fingerprint` md5 appear in machine formats (json/csv/`-x`) —
  the human `normal` view resolves `source` → path/`<direct>`/`<stdin>` and hides `fingerprint` (P6).
- **`hs list --json/--csv` default columns grow by two** (additive) — confirm no downstream parser
  assumes a fixed column count (GOAL-level call, not a bug).
- **`--from-json` source key** uses the full `FILE[@node]` spec as `path` so different `@node` selections
  of one file are distinct sources (avoids a false R5 match); shown opaque (never relativized).

## 6. Verification strategy

Prove it by driving the real CLI in a `temp_site` (each phase's `verify:` in [`TECH.md`](TECH.md)):

- **Unit** (`tests/test_source.py`): the fingerprint is order-independent over tags, stable across
  template change and uuid, and differs on args/group/tag change; `source_fingerprint_and_count` ==
  expected on a crafted file (blank/comment/inline-tag-only lines excluded); `format_source` passes
  `<direct>`/`<stdin>` through and shows real paths.
- **Integration** (`temp_site`, `create_taskfile_echo`, `assert_output` on R18 logs): one case per
  R5–R16 + R3 exempt (`<direct>`/`<stdin>` resubmit freely) + R7 count-warn + fresh-`initdb` (source
  table/indices exist). Anchor flows:
  - `hs submit -f f.in` twice → 2nd refuses (R5); rewrite `f.in`, submit → refuse suggest `--update` (R6).
  - `hs submit -f f.in --repeat` → `hs list --count` doubles (R8); `--update` after edit → only novel
    added (R9); `--update --repeat` → non-zero (R10).
  - `hsx f.in --restart` twice → 2nd idempotent (R12); `hsx f.in --update` → non-zero (R13);
    `hsx f.in --update --restart` after edit → novel added + run (R14); `hsx f.in --repeat` → all
    resubmitted (R15); `--restart --repeat` → non-zero (contradictory).
  - `hs submit --from-json spec.json` twice → refuse; `hsx --from-json spec.json --restart` idempotent.
- **Presentation** (integration): after a gated submit, `hs list source` resolves to the path (or
  `<direct>`/`<stdin>`) in normal view while `hs list --json` / `-x source` show the raw UUID.
- **Docs/completions:** `uv run sphinx-build docs docs/_build` (no *new* warnings); `bash -n
  share/bash_completion.d/hs`; `zsh -n share/zsh/site-functions/_hs`.
- **Full sweep:** `uv run pytest -v` green on the supported matrix.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
