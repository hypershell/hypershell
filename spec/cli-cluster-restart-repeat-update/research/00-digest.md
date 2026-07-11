# Research digest — consolidated decisions

Synthesis of briefs [01](01-identity-fingerprint.md)–[09](09-pruning-adversarial-check.md) plus the
planner's first-hand read of `submit.py`, `server.py`, `data/model.py`, `data/core.py`, `cluster/*`,
`core/uuid.py`. Each row below is a **single locked decision**; PLAN.md builds from these. Where a brief
conflicts, this digest (and the maintainer decisions) win — briefs 01/02/03/07 carry supersession
banners.

## Human decisions (maintainer, across review rounds)

- **`hsx --restart --repeat` → REJECT as contradictory** (exit non-zero, like R10/R16).
- **Gate `--from-json` too.** The JSON spec is a source: `path = abspath(FILE)[+ '@' + node]`,
  `fingerprint = md5(FILE bytes)`, `count = len(records)`. This **removes** the current
  `--from-json`+`--restart` incompatibility (`cluster/__init__.py:341`). `--from-json -` exempt.
- **Reserved sources are real `Source` rows** (fixed constant UUIDs), not TEXT sentinels: `Task.source`
  stays a **native `UUID`**; presentation resolves the UUID → path/`<direct>`/`<stdin>`.
- **No BRIN / no TSDB-specific code — "lean on Timescale compression."** The orderable UUIDv7 `source`
  feeds the operator's compressed-chunk per-batch min/max; the model ships only the composite btree
  (partial, excluding reserved consts).
- **No forced `uuid7()`.** Keep `core/uuid.py` as-is (default `uuid4`; `uuid7` when the `uuid-utils`
  extra is present). Add a **`timescale`/`timescaledb` provider alias** in `data/core.py` that maps to
  the postgres backend and **gates the `uuid7` extra** (bail like missing `psycopg`); in that mode
  `uuid()` yields v7 automatically. Groundwork for future Timescale gates (hypertable auto-create) —
  a warning only in this feature.
- **Column is named `Task.fingerprint`** (not `identity` — too close to `id`), matching
  `Source.fingerprint`.

## Task fingerprint (R2) — brief 01 (formula unchanged; renamed `identity` → `fingerprint`)

- **Formula:** `fingerprint = md5(json.dumps({'args': raw_command, 'group': group, 'tags': tags_ex},
  sort_keys=True, separators=(',',':'), ensure_ascii=False)).hexdigest()` → 32-hex TEXT. `sort_keys`
  gives R2's order-independence; MD5 matches the R1 source fingerprint.
- **`raw_command` = pre-template args, comment-stripped:** line task → `split_argline(raw_line)[0]`;
  JSON record → `base = record.get('args','')` verbatim. Threaded to `Task.new` via a new `raw_args`
  kwarg; falls back to stored `args` when absent (direct/retry — harmless, exempt).
- **`tags_ex` = final tag dict minus `part`** (post-pop). Automatically excludes
  `group`/`cores`/`memory`/`timeout` (popped to columns) and the rotation-mutated `part` tag; leaves
  only true user tags. **All** tags participate (R2) → re-tagging ⇒ new fingerprint under `--update`.
- **Computed in `Task.compute_fingerprint` classmethod, called from `Task.new`** (invariant §1).
- **Retries copy the parent fingerprint + source:** `__schedule_next_failed_tasks` (`model.py:555`)
  passes `fingerprint=task.fingerprint` and `source=task.source` (a chain shares one fingerprint).

## Schema (R1, R3) — briefs 02/07/09 + maintainer

- **New `Source` entity** (mirrors `Client`): `id` `UUID` (pk, from `uuid()`), `path` `TEXT`,
  `fingerprint` `TEXT` (content md5), `task_count` `INTEGER`, `created` `DATETIME`. Own `columns` dict +
  `NotFound/NotDistinct/AlreadyExists` + `from_id`/`new`. **No `previous_id`** — lineage = *all sources
  sharing `path`*.
- **`Task.source` is the shared native `UUID` type** (`model.py:75`), nullable (historical = NULL). Only
  ever holds a real `Source.id` (or a reserved-const id) — native UUID is what carries the orderable
  column stats Timescale compression prunes on. `Task.fingerprint` is `TEXT`, nullable.
- **Reserved sources `<direct>`/`<stdin>` = REAL `Source` rows with fixed constant UUIDs** (module
  constants `DIRECT_SOURCE_ID`/`STDIN_SOURCE_ID`), lazily get-or-created by PK; their `path` holds the
  sentinel string. Fixed constants ⇒ idempotent by PK + statically excludable from the partial de-dup
  index. Gating exemption is decided at the **App layer** by submission mode.
- **Timescale groundwork, not a forced `uuid7()`** (`data/core.py`): add `timescale`/`timescaledb` →
  `postgresql+psycopg` in the `providers` map; treat as postgres wherever `provider == 'postgres'` is
  special-cased (`get_url` file-pop; `DATABASE_DIALECT` already routes non-sqlite → postgres). Gate the
  `uuid7` extra in timescale mode (mirror the turso/psycopg gate: `display_critical` + `sys.exit`), so
  the existing `uuid()` yields v7. Warn that hypertable auto-create is deferred. **`core/uuid.py`
  untouched.**
- **Wire plumbing:** add `'source'`/`'fingerprint'` to `Task.columns` (`model.py:281`) or they won't
  round-trip through `serialize_tasks`/`deserialize_tasks` or `server.py:436` writeback. Give `Source`
  its own `columns` dict. (They surface *raw* in machine formats — see Presentation.)
- **No migration/backfill for free:** `Entity.metadata.create_all` (`data/__init__.py:65`) creates the
  new table + columns on a **fresh** `initdb` and never ALTERs an existing `task` table. No Alembic.

## Indices & scale (R17) — brief 03/09 + maintainer

- `Index('index_source_lookup', Source.path, Source.fingerprint)` — btree, both dialects (R5/R6).
- `Index('index_tasks_source', Task.source, Task.fingerprint)` — composite btree, **partial excluding
  the two reserved constant source ids**. Serves R7 count-by-source (leading col) + exact de-dup seek +
  sqlite.
- **No BRIN in the object model (maintainer: "lean on Timescale compression").** A plain btree does
  **not** prune by min/max, and `enable_chunk_skipping` can't index `uuid`; the pruning lever at scale is
  TimescaleDB's implicit compressed-chunk per-batch min/max, made effective by the **orderable UUIDv7
  `source`** (guaranteed in timescale mode via the `uuid7` gate). R17 is satisfied by the btree alone
  regardless (cost scales with lineage matches + chunk count, **not** total rows).
- **Bare `hs submit` stays fast:** detection is an indexed lookup on the tiny `Source` table (one row
  per file ingest) + one per-source `COUNT` — never a scan of the trillion-row `task`. Added latency is
  dominated by reading+md5'ing the file.
- **De-dup = single small-`IN` query (strategy (a), locked):** lineage ids = `Source.lookup(path)` (a
  *handful* of rows), then `Task.fingerprints_for_sources(lineage_ids)` = `SELECT fingerprint FROM task
  WHERE source IN (:lineage)`. Small `IN` list (no batching / no `SQLITE_MAX_VARIABLE_NUMBER` issue); the
  Loader computes each task's fingerprint anyway and skips membership hits — **no App-layer
  recomputation** (avoids duplicating the stateful global-tag machine). **Brief-03's (c)/(d) and the
  150 MB note are dropped.**

## Submit-flow seam (R4, R7, R8, R9, R18) — brief 04 + planner plumbing analysis

- **The `Loader` FSM is the single stamping chokepoint** for both `hs submit` and `hsx`
  (`submit.py:164-198`). Source stamping + fingerprint + de-dup live there / in `Task.new`.
- **`GatedSource` wrapper (minimizes coupled-core churn):** the App wraps its source iterable in a small
  object carrying `source_id` + optional `skip_fingerprints`. It flows through
  `submit_from`→`SubmitThread`→`ServerThread`→cluster launchers **unchanged**; only `Loader.__init__`
  unwraps it and `SubmitThread/LiveSubmitThread.source_name` surface its label. Avoids threading kwargs
  through ~10 constructors (invariant §11). Bare-restart source stays a plain `[]`.
- **Count semantics:** a line is a task iff `bool(split_argline(template.expand(line.strip()))[0])` —
  **stateless per line**, so the upfront count pass needs **no** tag-state duplication. Factor this
  predicate into one helper shared by the count pass and the Loader. Skips: blank, comment-only,
  inline-tag-only lines.
- **Two streaming passes over a named file** (seekable): pass 1 = md5 + count; gate + create Source;
  pass 2 = existing Loader submits. `<stdin>` isn't seekable ⇒ exempt. `--from-json` records are already
  a list in memory ⇒ md5 the FILE, `count=len`.
- **Writeback order: create the `Source` row with the *expected* count BEFORE the tasks.** Makes R7
  meaningful — a crash mid-bundle leaves `live COUNT(source) < recorded`, which a later run detects and
  `--restart` resumes. R7 = `Source.task_count` vs `Task.count_for_source(id)` at detection time.
- Guard all source writes with `queue_config is None and not in_memory` (invariant §4).

## CLI gate matrix (R5–R16) — brief 05 (authoritative table there)

- **Exit code:** every loud refusal raises `cmdkit.cli.ArgumentError` → `exit_status.bad_argument` (2).
  Warnings (R7/R18) via `log.warning`, exit 0.
- **`hs submit`:** add `--repeat`/`--update` (no `--restart`); `check_arguments` rejecting `--update
  --repeat` (R10). DB path only; `-q`, `--no-db`, `<stdin>`, `<direct>` exempt.
- **`hsx`/`hs cluster`:** add `--repeat`/`--update`; reject `--update` alone (R13), `--update --repeat`
  (R16), **`--restart --repeat`** (human), `--update` with `--no-db`. `source`/`input_stream` become
  file-aware; bare `--restart` (no file) keeps `source==[]`. Keep `--forever`/`--no-db` + `--restart`
  refusals.
- **New flags are NOT forwarded to launched clients** (invariant §11); `restart_mode` reaches
  `ServerThread` only.
- **`hs server` out of scope** (advanced/direct entry); its ingested tasks carry NULL source.

## Presentation — resolve `Task.source` UUID → path/sentinel (brief 08)

- Adding `source`/`fingerprint` to `Task.columns` makes them appear **raw** in machine formats (`hs info
  -f json/yaml`, `-x`, `hs list --json/--csv` default all-fields, explicit-field) — **correct** there.
  They do **not** appear in the fixed `NORMAL_MODE_TEMPLATE` (`task.py:1285`).
- **Human resolution (normal view only):** `Source.paths_for_ids(ids) -> dict` (batch, tiny IN) +
  `format_source(path, *, relative=False)` in `core/pretty_print.py` (sentinels `^<.*>$` pass through;
  real paths absolute; `@node` json specs opaque). Add a resolved `source:` line to the normal template;
  `hs list` resolves one `paths_for_ids` per page (avoid N+1), `hs info` a single `from_id`. **Keep
  `fingerprint` out** of the normal view. Lands in **P6**; columns land in P1.

## Docs / completions / tests (R18, §12) — brief 06

- **Docs (`docs/_include/`):** `submit_{help,usage,desc}.rst`, `cluster_{help,usage,desc}.rst`. Build
  check `uv run sphinx-build docs docs/_build` (no *new* warnings).
- **Completions (`share/`):** bash `share/bash_completion.d/hs`; zsh `share/zsh/site-functions/_hs`; man
  pages `hs.1`/`hsx.1`. **No new `share/` files** ⇒ CI list + `pyproject.toml` mapping unchanged.
- **Tests:** new `tests/test_source.py` (unit: fingerprint order-independence/stability/exclusions;
  source md5+count helper; `format_source`). Integration across `test_submit.py`, `test_cluster.py`, new
  `test_restart.py`, `test_initdb.py` — one case per R5–R16 + R3 exempt + R7 warn + fresh-initdb, in
  `temp_site` with `create_taskfile_echo` + `assert_output` on R18 logs.

## Residual risks (carried to PLAN)

- **Timescale pruning is a timescale-mode property, not correctness.** Compressed per-batch min/max on
  the orderable UUIDv7 `source` (guaranteed by the `uuid7` gate in timescale mode; operator's compression
  policy). Non-timescale deployments use `uuid4`/no hypertable → btree seek (R17 still met). Fixed-const
  reserved ids cluster under `source`-ordered compression and are excluded from the partial index.
- **Timescale groundwork only:** provider alias + `uuid7` gate + warning; hypertable auto-create deferred.
- **De-dup memory** = one file-history's fingerprints (lineage). Fine for the requeue case; log the size.
- **R7 test determinism:** how to fabricate an "incomplete prior submission" — settle in the R7 phase.
- **`hs list --json/--csv` default columns grow** by two (additive) — confirm no downstream parser
  assumes a fixed column count.
