# TS1 research 02: CI image, editions, and DDL on a compressed hypertable

Researched 2026-11-02 against TimescaleDB 2.31.0 (source at tag `2.31.0`) and the `latest` branch of
the docs repo. "Source:" means read in the code, not exercised; "unverified" means not checked at all.

## Sources

- [S1] Docker Hub tag API: `https://hub.docker.com/v2/repositories/timescale/timescaledb/tags/<tag>`
- [S2] Releases: https://github.com/timescale/timescaledb/releases/tag/2.31.0 (2026-10-20, not a prerelease)
- [S3] Image repo: https://github.com/timescale/timescaledb-docker (`Dockerfile`, `Makefile`,
  `docker-entrypoint-initdb.d/000_install_timescaledb.sh`, `001_timescaledb_tune.sh`, `README.md`)
- [S4] timescaledb-tune: https://github.com/timescale/timescaledb-tune (`pkg/pgtune/misc.go`, `parallel.go`)
- [S5] Source 2.31.0: https://github.com/timescale/timescaledb/tree/2.31.0 (`src/guc.c`, `src/license_guc.{c,h}`,
  `src/process_utility.c`, `src/indexing.c`, `src/loader/{loader.c,bgw_launcher.c,bgw_counter.c}`,
  `sql/pre_install/tables.sql`)
- [S6] Docs (tigerdata.com/docs now 308-redirects to the docs repo): https://github.com/timescale/docs/blob/latest/
  `use-timescale/compression/modify-a-schema.md`, `use-timescale/schema-management/alter.md`,
  `use-timescale/hypercore/modify-data-in-hypercore.md`, `api/hypertable/create_index.md`,
  `use-timescale/hypertables/hypertable-crud.md`, `about/timescaledb-editions.md`,
  `self-hosted/configuration/about-configuration.md`, `_troubleshooting/self-hosted/background-worker-failed-start.md`
- [S7] GitHub workflow syntax: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- [S8] Docker reference grammar: https://github.com/distribution/reference/blob/main/reference.go
- [S9] Tag-plus-digest service pin in use: https://github.com/zed-industries/zed/pull/58964

## Answers

1. **Tags and digests.** The latest release is 2.31.0 [S2]. Its tags were all pushed 2026-10-20 [S1]. Each
   digest is an OCI image index covering amd64, arm64, 386, arm/v7 and arm/v6:

   | tag | index digest | linux/amd64 digest |
   |-|-|-|
   | `2.31.0-pg17` | `sha256:b56a3f51991df1f79d0c1a0841cad5c5fa8058fdd6a3ee6886394a6d4c174538` | `sha256:e2d08262d7eb179f32630feacfe1866e23b659f76ce4216507b3b1079e3a4e15` |
   | `2.31.0-pg18` | `sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579` | `sha256:26e20a5e5a7ab6bec7ff217a52ffe21c9f4799603816718b2a573e7be839d8b4` |
   | `2.31.0-pg17-oss` | `sha256:668ee2d521ed472045f74c9e16f01318ab73120754ff8ba4cf1d76f00a26307a` | (not fetched) |
   | `2.31.0-pg18-oss` | `sha256:cc00b0ede968b0c6e55212bd55a9a195726afe5a9e19dcf8af49a13334e92ff3` | `sha256:f940dcdf06d838297efdb8626c48eb651c10d264624857d7948bca737746d53b` |

   `-oss` tags still ship for every current version and for pg16 through pg18 [S1]. PG15 tags stop at
   2.28.3. `latest-pg18` currently has the same digest as `2.31.0-pg18`, but it moves with each release, so
   the workflow should never use it. To re-resolve a digest, query the [S1] endpoint (its `digest` field
   is the index digest) or run `docker buildx imagetools inspect timescale/timescaledb:<tag>`.

2. **What the image initialises.** `000_install_timescaledb.sh` runs `CREATE EXTENSION IF NOT EXISTS
   timescaledb CASCADE` in `postgres` and in `template1`, and also in `$POSTGRES_DB` when that is set
   [S3]. As a result, **every database later created from `template1` already has the extension**,
   including the legacy fixture `hs283`. `hs290a1` is copied from `hs283` and inherits it. The script also
   writes `timescaledb.telemetry_level=basic`; with `TIMESCALEDB_TELEMETRY=off` it writes `off` and
   unschedules job 1 [S3]. `001_timescaledb_tune.sh` runs `timescaledb-tune --yes` against
   `postgresql.conf` unless `NO_TS_TUNE` is non-empty. The `TS_TUNE_*` variables override what it
   detects [S3]. The Dockerfile patches `postgresql.conf.sample` to
   `shared_preload_libraries = 'timescaledb,…'`, so the library is preloaded [S3].
   - `timescaledb.max_background_workers` defaults to 16, both compiled in and as tune's default.
   - Tune sets `max_worker_processes = 3 + max_bg_workers + cpus` [S4][S5].
   - `NO_TS_TUNE` leaves PostgreSQL's own `max_worker_processes` default of 8 in place, which also limits
     the TimescaleDB workers. Keep tune on.

3. **Editions.** `TS_LICENSE_DEFAULT` is `timescale` unless the build defines `APACHE_ONLY` [S5]. The
   `-oss` images are built with `-DAPACHE_ONLY=1`, and the build deletes `timescaledb-tsl-*.so` [S3].
   As a result the default image is Community and `-oss` is Apache-only.
   - `timescaledb.license` is defined as `PGC_SUSET`. Its check hook, however, accepts a value only from
     `PGC_S_FILE`, `PGC_S_ARGV` or `PGC_S_DEFAULT`. Anything else fails with "Cannot change a license in
     a running session" [S5].
   - **`SET` and `ALTER DATABASE … SET timescaledb.license` are therefore rejected**, so one database
     cannot simulate Apache inside the Community image (source; not exercised).
   - In the Community image, the only route is `ALTER SYSTEM` (a file source) followed by a container
     restart, which affects the whole server (unverified). A GitHub service container cannot take extra
     `postgres -c` arguments: its keys are `image`, `credentials`, `env`, `ports`, `volumes` and
     `options` [S7].
   - Compression and its policy functions are ❌ in the Apache column of the editions table [S6].

4. **DDL on a hypertable with columnstore data.** This answer comes from the 2.31.0 source [S5] and the docs [S6].
   - **Allowed operations.** `check_alter_table_allowed_on_ht_with_compression` whitelists `AddColumn`,
     `ColumnDefault`, `DropColumn`, `DropConstraint`, `AddIndex`, `AddConstraint`,
     `SetNotNull`/`DropNotNull`, and others. Any other subcommand fails with "operation not supported on
     hypertables that have columnstore enabled". `ALTER COLUMN … TYPE` fails once any chunk is
     compressed [S5]. The docs give the same list: a nullable column, a column with `NOT NULL DEFAULT`
     (2.6 and later), rename, and drop (not on an orderby or segmentby column). A type change is not
     allowed [S6].
   - **Rules for `ADD COLUMN` once columnstore is enabled** (`check_altertable_add_column_for_compressed`):
     - A `DEFAULT` must reduce to a `Const` after `eval_const_expressions`. Otherwise the statement fails
       with "cannot add column with non-constant default expression".
     - `NOT NULL` without a default is refused.
     - `CHECK` and explicit `NULL` are allowed.
     - Other column constraints (for example `REFERENCES`) are refused. Identity columns are refused.
     - `PRIMARY KEY` and `UNIQUE` pass this check but then fail, because a single-column key cannot
       include the partition column.
   - **Fit with `upgrade.py`.** These rules match `upgrade.py`'s `_refusal`, which allows only nullable
     columns or columns with a `CONSTANT_DEFAULT`, refuses unique and foreign-key columns, and requires a
     default with NOT NULL. One residual case remains: a constant default whose coercion is not
     constant-folded, such as a literal cast to a domain type. That case would pass HyperShell's check
     and fail in TimescaleDB (unverified edge).
   - **`CREATE INDEX`** (non-unique) on the parent recurses to the chunks and is not blocked by compressed
     chunks. Only UNIQUE and PK indexes get the extra compressed-data validation [S5].
   - **`IF NOT EXISTS`** is honoured: an index that already exists returns early [S5].
   - `CONCURRENTLY` is refused on hypertables.
   - **`WITH (timescaledb.transaction_per_chunk)`:**
     - It calls `PreventInTransactionBlock`, so it cannot run inside the single transaction of the schema
       step [S5].
     - It is refused for UNIQUE and PK indexes [S5][S6].
     - A failure partway through leaves an invalid parent index [S6].
   - **`DROP CONSTRAINT`** is whitelisted [S5]. The retired `UNIQUE(previous_id|next_id)` cannot exist on
     a hypertable anyway, because unique indexes must include the partition column, so it is always gone
     before conversion.
   - **Reflection.** The hypertable root is an ordinary `relkind='r'` table, and its chunks live in
     `_timescaledb_internal`. As a result, `has_table`, `get_columns` and `get_indexes('task')` on the
     root behave like plain PostgreSQL (by catalog semantics; not exercised).
   - `create_hypertable` adds no default index when a single-column index on the partition column
     already exists, such as the `id` primary key [S5 `indexing.c`]. The reflected index set therefore
     stays the model's.

5. **Database-wide `VACUUM`.** TimescaleDB intercepts a `VACUUM` that names no table. It builds the
   relation list itself from `pg_class` (tables, matviews, partitioned tables) after a per-relation
   permission check, so chunks and compressed-chunk tables are included as ordinary relations. Only
   `VACUUM FULL` adds a columnstore rebuild step. No error path is specific to columnstore [S5
   `process_vacuum`]. Relations the role may not vacuum are skipped, as in core PostgreSQL. HyperShell's
   autocommit `VACUUM` is fine as long as the role owns the tables or is a superuser.

6. **Background workers.**
   - The launcher takes one worker. It starts one scheduler per database that allows connections and
     is not `datistemplate`, and each running job takes one more worker. All of these count against
     `timescaledb.max_background_workers`, which is `PGC_POSTMASTER` with a default of 16 [S5].
   - `max_worker_processes` must cover the TimescaleDB workers plus `max_parallel_workers` [S6
     about-configuration].
   - **When workers run out:** the launcher logs "TimescaleDB background worker limit of %d exceeded"
     once, at LOG level, and the database's scheduler does not start, so its policies never run
     [S5 `bgw_launcher.c`]. Jobs log "failed to start a background worker" [S6 troubleshooting].
   - CI budget: launcher + `postgres` + `hs283` + `hs290a1` + throwaways is roughly 5 to 8 of the 16.
     Fixtures that leak databases could exhaust it.
   - `CREATE DATABASE … TEMPLATE x` makes the loader restart `x`'s scheduler first, so a connected
     scheduler does not block the copy [S5 `loader.c`].
   - Docs to cite: `self-hosted/configuration/about-configuration.md` (#workers) and the troubleshooting
     page.

7. **`DROP TABLE` (via `truncatedb`).** The docs say all chunks are deleted [S6 hypertable-crud]. Policy
   jobs live in `_timescaledb_catalog.bgw_job` with `FOREIGN KEY (hypertable_id) REFERENCES
   _timescaledb_catalog.hypertable(id) ON DELETE CASCADE` [S5 `tables.sql`], so dropping the hypertable
   removes its policy rows and leaves no orphaned jobs. `drop_all` waits on locks held by a policy job
   that is running at the time (normal PostgreSQL behaviour).

8. **Pinning in GitHub Actions.** The docs only say "Docker Hub image name or a registry name", and they
   say an empty string skips the service. Digests are not mentioned [S7]. The runner passes the string to
   Docker, whose grammar is `name [":" tag] ["@" digest]` [S8], so both `image@sha256:…` and
   `image:tag@sha256:…` are valid. When both are given, the digest wins. The tag-plus-digest form is used
   for a `postgres` service in a merged workflow [S9]. Both forms are valid by the grammar, but GitHub's
   docs do not state it.

## Recommendation

- **Image pin.** Use `timescale/timescaledb:2.31.0-pg18@sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579`.
  This is the index digest: it is multi-arch, and pg18 matches the plain `pg18` entry. If the plan
  prefers the placeholder's pg17, use
  `timescale/timescaledb:2.31.0-pg17@sha256:b56a3f51991df1f79d0c1a0841cad5c5fa8058fdd6a3ee6886394a6d4c174538`.
- **Service env.** Keep tune on and set `TIMESCALEDB_TELEMETRY: 'off'`. Add `TS_TUNE_MAX_BG_WORKERS` only
  if the tests create many databases.
- **Apache tier: practical.** Add a matrix entry pinned to
  `timescale/timescaledb:2.31.0-pg18-oss@sha256:cc00b0ede968b0c6e55212bd55a9a195726afe5a9e19dcf8af49a13334e92ff3`.
  The license is `apache` and the TSL library is absent, so this is a real Apache server rather than a
  simulation. A per-database `SET` of the license is impossible, so the Apache tier needs its own server
  either way.
- **Fixtures.** The fixture databases inherit the extension from `template1`. Extension-present tests
  must not assume a database without the extension, and the extension-absent tier must run on plain
  `postgres:*` images (or drop the extension first).
- **DDL the schema step must keep on a compressed hypertable:**
  1. Add columns only as nullable, or with a default that constant-folds, optionally `NOT NULL DEFAULT
     <const>`. Never add identity, generated, FK, unique or PK columns.
  2. Create non-unique indexes only, with plain `CREATE INDEX IF NOT EXISTS`. Never use `CONCURRENTLY`,
     and never use `transaction_per_chunk` inside the step's transaction.
  3. Never `ALTER COLUMN … TYPE` or `SET STORAGE`, never drop an orderby or segmentby column, and never
     toggle row-level security.
  4. Run every UNIQUE drop before conversion; the step's `RETIRED_UNIQUE` drop already does.
  5. The hypertable pass runs after `upgrade_schema`, never before.
