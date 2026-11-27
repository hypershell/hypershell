# TS1 research 01 — TimescaleDB SQL surface and version gates (re-checked 2026-11-02)

Everything below was checked against the 2.31.0 tag source, the CHANGELOG at that tag, and the
official docs. Facts marked *(unverified)* were not confirmed against a primary source.

## Sources

- R: GitHub releases API — https://api.github.com/repos/timescale/timescaledb/releases
- C: CHANGELOG at tag 2.31.0 — https://github.com/timescale/timescaledb/blob/2.31.0/CHANGELOG.md
- S-ddl: https://github.com/timescale/timescaledb/blob/2.31.0/sql/ddl_api.sql
- S-pol: https://github.com/timescale/timescaledb/blob/2.31.0/sql/policy_api.sql
- S-polc: https://github.com/timescale/timescaledb/blob/2.31.0/tsl/src/bgw_policy/compression_api.c
- S-polkeys: https://github.com/timescale/timescaledb/blob/2.31.0/tsl/src/bgw_policy/policies_v2.h
- S-polint: https://github.com/timescale/timescaledb/blob/2.24.0/sql/policy_internal.sql and the same file at 2.25.0
- S-views: https://github.com/timescale/timescaledb/blob/2.31.0/sql/views.sql (also at 2.22.0, 2.24.0, 2.25.0)
- S-dim: https://github.com/timescale/timescaledb/blob/2.31.0/src/dimension.c
- S-ht: https://github.com/timescale/timescaledb/blob/2.31.0/src/hypertable.c
- S-utils: https://github.com/timescale/timescaledb/blob/2.31.0/src/utils.c
- S-uuid: https://github.com/timescale/timescaledb/blob/2.31.0/sql/uuidv7.sql (identical function list at 2.22.0)
- S-with: https://github.com/timescale/timescaledb/blob/2.31.0/src/with_clause/alter_table_with_clause.c (and at 2.22.0)
- S-create: https://github.com/timescale/timescaledb/blob/2.31.0/tsl/src/compression/create.c (and at 2.22.0)
- S-pu: https://github.com/timescale/timescaledb/blob/2.31.0/src/process_utility.c
- S-xm: https://github.com/timescale/timescaledb/blob/2.31.0/src/cross_module_fn.c
- S-lic: https://github.com/timescale/timescaledb/blob/2.31.0/src/license_guc.h
- S-ext: https://github.com/timescale/timescaledb/blob/2.31.0/src/extension_utils.c
- S-ctl: https://github.com/timescale/timescaledb/blob/2.31.0/timescaledb.control.in (and at 2.22.0)
- S-job: https://github.com/timescale/timescaledb/blob/2.31.0/sql/job_api.sql
- PR9045: https://github.com/timescale/timescaledb/pull/9045
- D-ch: https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_hypertable/
- D-acp: https://www.tigerdata.com/docs/reference/timescaledb/hypercore/add_columnstore_policy/
- D-alt: https://www.tigerdata.com/docs/reference/timescaledb/hypercore/alter_table/
- D-scti: https://www.tigerdata.com/docs/reference/timescaledb/hypertables/set_chunk_time_interval/
- D-ed: https://www.tigerdata.com/docs/about/latest/timescaledb-editions

## 1. Current release and changes since 2.27.2

- Latest is **2.31.0, released 2026-10-20** (R, C). Since 2.27.2: 2.28.0–2.28.3, 2.29.0–2.29.2, 2.30.0–2.30.2, 2.31.0.
- **Supported PostgreSQL: 16, 17, 18.** 2.29.0 dropped PG15 (#10041) (C). The CMake gate still says 15–18 (stale). PG15 users are capped at 2.28.x.
- Nothing since 2.27.2 changes UUID partitioning or the API shape HyperShell uses. Relevant additions: 2.29.0 adds a `config_merge` parameter to `alter_job()` (#10225) and a `recompress_unordered` policy option (#10226). 2.29.0 also stops creating a separate catalog entry for compressed hypertables (#10204), which is internal and leaves the views unchanged. 2.31.0 adds `move_to_columnstore()` (#10632). 2.27.2 drops the refresh-policy check when adding a columnstore policy (#9895) (C).
- Earlier UUID items re-checked (C):
  - 2.22.0: UUIDv7 partitioning (#8505) and the uuidv7 helpers (#8385); UUID compression shipped off by default (`timescaledb.enable_uuid_compression`).
  - 2.22.1: `CREATE TABLE WITH` fix for UUIDv7 (#8657).
  - 2.23.0: UUID compression on by default (#8798); show/drop_chunks on UUIDv7 (#8746).
  - 2.23.1: fixed decoding of UUIDv7 timestamp microseconds (#8875).
  - 2.24.0: `time_bucket` on UUIDv7 (#8908), caggs (#8939), chunks view shows ranges as timestamps (#8786).
  - 2.25.0: "Fix missing UUID check in compression policy" (#9045) and retention policies on UUIDv7 (#9102).
- **The #9045 fix matters for us.** Before 2.25.0, the job body `policy_compression` dispatches on dimension type with a PL/pgSQL `CASE` that lacks `UUID` and has no `ELSE`. So `add_columnstore_policy` *succeeds*, but every job run on a UUID hypertable fails (PR9045: "resulting in an error when running the policy"). The 2.24.0→2.25.0 diff adds only `'UUID'::regtype` to that `CASE` (S-polint). That points to PL/pgSQL's `case not found` error, SQLSTATE 20000 *(inferred from source, not observed)*.

## 2. `create_hypertable` (generic form)

- Signature (S-ddl): `create_hypertable(relation REGCLASS, dimension _timescaledb_internal.dimension_info, create_default_indexes BOOLEAN = TRUE, if_not_exists BOOLEAN = FALSE, migrate_data BOOLEAN = FALSE) RETURNS TABLE(hypertable_id INT, created BOOL)`.
- `by_range(column_name NAME, partition_interval ANYELEMENT = NULL::bigint, partition_func regproc = NULL)` (S-ddl).
- Calling it on an existing hypertable (S-ht):
  - `if_not_exists => true` raises a NOTICE, `table "task" is already a hypertable, skipping`, and returns `created = false`. D-ch says "warning", but the source says NOTICE.
  - Without it, it raises ERROR `table "task" is already a hypertable` (SQLSTATE TS errcode `ERRCODE_TS_HYPERTABLE_EXISTS`).
- On a non-empty table without `migrate_data` it raises ERROR `table "task" is not empty`, SQLSTATE 0A000. The hint is `You can migrate data by specifying 'migrate_data => true' when calling this function.` (S-ht). D-ch warns that migration can lock the table for a long time.
- **An INTERVAL is accepted for a UUID column** (S-dim): the `INTERVALOID` branch allows `IS_TIMESTAMP_TYPE || IS_UUID_TYPE` and stores the interval as microseconds. An integer microseconds value is also accepted. D-ch's own example is `by_range('id', INTERVAL '1 month')`.
- Rows with a non-v7 UUID in the partition column raise ERROR `<uuid> is not a version 7 UUID`, SQLSTATE 0A000 (`ERRCODE_FEATURE_NOT_SUPPORTED`). The detail is `UUID "time" partitioning columns only support version 7 UUIDs.` (S-utils). This also applies during `migrate_data`.
- Unique and primary-key indexes must include the partition column (D-ch). `task`'s PK is `(id)`, so it qualifies.
- `create_default_indexes => true` may add an `(id DESC)` index beside the PK *(whether it skips when a PK on `id` exists is unverified)*. Probe `pg_indexes` after creation, or pass `false`.

## 3. Vocabulary

- 2.18.0 deprecated the `compress*` names (C, deprecation table):
  - `timescaledb.compress` → `timescaledb.enable_columnstore`
  - `compress_segmentby`/`compress_orderby` → `segmentby`/`orderby`
  - `add_compression_policy`/`remove_compression_policy` → `add_columnstore_policy`/`remove_columnstore_policy`
  - `hypertable_compression_settings` → `hypertable_columnstore_settings`
  - `compression_settings` → `columnstore_settings`
- **Nothing is removed as of 2.31.0.** Both spellings are aliases in the ALTER TABLE parser at 2.22.0 and at 2.31.0: `{"compress","columnstore","enable_columnstore"}`, `{"compress_segmentby","segmentby","segment_by"}` and `{"compress_orderby","orderby","order_by"}` (S-with). Removal is promised for "the next major release".
- `add_columnstore_policy` is a **PROCEDURE**: `hypertable REGCLASS, after "any" = NULL, if_not_exists BOOL = false, schedule_interval INTERVAL = NULL, initial_start TIMESTAMPTZ = NULL, timezone TEXT = NULL, created_before INTERVAL = NULL`. It is `LANGUAGE C` on the same C entry point as `add_compression_policy`, `ts_policy_compression_add`, which is a FUNCTION returning INTEGER (S-pol). Both exist at 2.22.0.
- `CALL` can run inside a transaction block as long as the procedure does no transaction control. The C entry point does none, so it is safe in our DDL transaction *(source reading, not tested)*.
- **With a UUID partition column, `after` must be an INTERVAL.** `validate_compress_after_type` demands `INTERVALOID` for every non-integer partitioning type and otherwise raises `unsupported compress_after argument type, expected type : interval` (0A000) (S-polc).
- **Use the columnstore vocabulary.** It has existed since 2.18, so it covers 2.22–2.31, and it is the documented one (D-acp, D-alt).

## 4. Introspection (all exist at 2.22.0 and at 2.31.0 per S-views)

- (a) `timescaledb_information.hypertables`: `hypertable_schema, hypertable_name, owner, num_dimensions, num_chunks, compression_enabled, tablespaces, primary_dimension, primary_dimension_type`.
- (b) `timescaledb_information.dimensions`: `hypertable_schema, hypertable_name, dimension_number, column_name, column_type, dimension_type, time_interval, integer_interval, integer_now_func, num_partitions`.
  - **The UUID interval display differs by version.** From 2.25.0, `uuid` is in the view's time-type list, so `time_interval` is an INTERVAL and `integer_interval` is NULL. In 2.22–2.24, `time_interval` is NULL and `integer_interval` holds microseconds as a bigint (diffed S-views at 2.24.0 and 2.25.0). `dimension_type` reports `'Time'` in both.
- (c) `timescaledb_information.hypertable_columnstore_settings`, a view over `hypertable_compression_settings`. Its columns are `hypertable regclass, segmentby text` (comma-joined) and `orderby text`, plus `compress_interval_length` and, in recent versions, `index`.
  - `orderby` renders as `id DESC`, with `NULLS LAST` appended only when it differs from the default.
  - The per-column views are `compression_settings` and `columnstore_settings`. `compression_settings` is deprecated and slated for removal (C 2.24.0), so do not use it.
- (d) `timescaledb_information.jobs`: `job_id, application_name, schedule_interval, max_runtime, max_retries, retry_period, proc_schema, proc_name, owner, scheduled, fixed_schedule, config, next_start, initial_start, hypertable_schema, hypertable_name, check_schema, check_name`.
  - The policy row has `proc_schema='_timescaledb_functions'` and `proc_name='policy_compression'`.
  - Its config keys are `hypertable_id` and `compress_after`; `compress_created_before` replaces `compress_after` when `created_before` was used (S-polkeys).
  - The interval is stored as text *(the exact rendering, likely `28 days`, is unverified)*, so always compare with `(config->>'compress_after')::interval`.
- (e) `timescaledb_information.job_stats`: `last_run_status` ∈ {`Success`,`Failed`,NULL}, `job_status` ∈ {`Running`,`Paused`,`Scheduled`}, plus `last_run_started_at, last_successful_finish, last_run_duration, next_start, total_runs, total_successes, total_failures`.

## 5. Drift handling

- **Chunk interval:** `SELECT set_chunk_time_interval('task', INTERVAL '1 week')` accepts an INTERVAL for UUID through the same validator (S-dim). It affects only new chunks (D-scti).
- **segmentby/orderby:** `ALTER TABLE task SET (timescaledb.segmentby=…, timescaledb.orderby=…)` is allowed when compressed chunks exist, at both 2.22.0 and 2.31.0. "The new settings apply only to the chunks that have not yet been converted" (D-alt).
  - Recent versions emit NOTICE `updated compression settings will only apply to future compressions` (S-create). That NOTICE is absent at 2.22–2.25.
  - Recompress existing chunks with `compress_chunk(chunk, recompress => true)`.
  - Disabling columnstore while columnstore chunks exist errors: `cannot disable columnstore on hypertable with columnstore chunks`.
- **Policy:** with `if_not_exists => true`, identical arguments give NOTICE `columnstore policy already exists for hypertable "task", skipping`. **Different arguments give a WARNING and no change:** `columnstore policy already exists for hypertable "task"`, hint `Remove the existing policy before adding a new one.` (S-polc). Without `if_not_exists`, the call raises ERROR `columnstore policy already exists for hypertable or continuous aggregate "task"`.
  - To fix drift, run `CALL remove_columnstore_policy('task', if_exists => true)` and then `add_columnstore_policy`.
  - Alternatively, use `alter_job(job_id, config => …)`. Its signature is in S-job, and `config_merge` exists only from 2.29.0.

## 6. Edition and defaults

- `current_setting('timescaledb.license', true)` returns `'timescale'` or `'apache'` (S-lic, D-ed). The boot default is `apache` in Apache-only builds and `timescale` otherwise (S-lic). The GUC is PGC_SUSET.
- Under Apache (S-xm):
  - ALTER TABLE … SET (timescaledb.enable_columnstore…) fails with ERROR `functionality not supported under the current "apache" license. Learn more at https://tsdb.co/pdbir1r3`, SQLSTATE 0A000.
  - `add_columnstore_policy` fails with ERROR `function "add_columnstore_policy" is not supported under the current "apache" license`, SQLSTATE 0A000, hint `Upgrade your license to 'timescale' to use this free community feature.`
  - `create_hypertable` with UUID partitioning is Apache code and works.
- **Columnstore is not enabled automatically by `create_hypertable()`.**
  - Only `CREATE TABLE … WITH (tsdb.hypertable …)` enables it (S-pu). There it defaults to true unless the edition is Apache, and since 2.23.0 it also creates a policy automatically (#8588).
  - HyperShell's path must therefore `ALTER TABLE` explicitly. Under Apache, it skips columnstore and reports that.

## 7. UUIDv7 helpers

- Defined in `sql/uuidv7.sql` as `@extschema@.*`, which is the extension's schema (`public` by default). All landed in 2.22.0 (#8385) (S-uuid, C):
  - `generate_uuidv7()`
  - `to_uuidv7(timestamptz)`, which is VOLATILE and has random bits
  - `to_uuidv7_boundary(timestamptz)`, IMMUTABLE with random bits zeroed, for range predicates
  - `uuid_timestamp(uuid)`, millisecond precision
  - `uuid_timestamp_micros(uuid)`
  - `uuid_version(uuid)`
- `time_bucket(interval, uuid …)` returning timestamptz landed in 2.24.0.

## 8. Trusted

- `trusted = true` is in `timescaledb.control.in` at both 2.22.0 and 2.31.0 (S-ctl).
- Without `shared_preload_libraries`, loading raises FATAL `extension "timescaledb" must be preloaded`, hinted `Please preload the timescaledb library via shared_preload_libraries…` (S-ext).

## Recommendation

**Use the columnstore vocabulary only.** Gates: below 2.22.0, refuse UUID partitioning. From 2.22.0 to 2.24.x, convert and enable columnstore, but **do not add the policy**: report "compression policy needs TimescaleDB ≥ 2.25.0" (#9045). From 2.25.0 on, apply everything. Under the `apache` edition, convert only and report that columnstore is unavailable.

Probes (read-only):
```sql
SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';            -- NULL → not installed
SELECT current_setting('timescaledb.license', true);                            -- 'timescale' | 'apache'
SELECT compression_enabled FROM timescaledb_information.hypertables
 WHERE hypertable_schema = current_schema() AND hypertable_name = 'task';      -- no row → plain table
SELECT column_name, column_type,
       COALESCE(time_interval, integer_interval * INTERVAL '1 microsecond') AS chunk_interval
  FROM timescaledb_information.dimensions
 WHERE hypertable_name = 'task' AND dimension_number = 1;                       -- 2.22–2.24 fill integer_interval
SELECT segmentby, orderby FROM timescaledb_information.hypertable_columnstore_settings
 WHERE hypertable = 'task'::regclass;                                           -- expect 'client_id', 'id DESC'
SELECT j.job_id, (j.config->>'compress_after')::interval AS after, s.last_run_status, s.job_status
  FROM timescaledb_information.jobs j
  LEFT JOIN timescaledb_information.job_stats s USING (job_id)
 WHERE j.proc_name = 'policy_compression' AND j.hypertable_name = 'task';
```

Apply (each guarded by its probe; all valid inside one transaction):
```sql
SELECT * FROM create_hypertable('task', by_range('id', INTERVAL '1 week'),
                                if_not_exists => true, migrate_data => true);
ALTER TABLE task SET (timescaledb.enable_columnstore = true,
                      timescaledb.segmentby = 'client_id',
                      timescaledb.orderby  = 'id DESC');          -- only if not enabled or settings differ
CALL add_columnstore_policy('task', after => INTERVAL '4 weeks', if_not_exists => true);   -- ≥2.25.0
```

Drift: compare the probes against the targets and report each mismatch with its value.
- Chunk interval: fix with `set_chunk_time_interval`; it applies to new chunks only.
- Settings: fix with `ALTER TABLE … SET`; it applies to future compressions only.
- Policy: fix with `CALL remove_columnstore_policy('task', if_exists => true)` and then add again. Never rely on `if_not_exists` to update, because with different arguments it only WARNs.
- Run status: surface `last_run_status = 'Failed'` honestly.
