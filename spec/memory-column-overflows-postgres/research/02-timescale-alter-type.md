# 02 — Does TimescaleDB refuse `ALTER COLUMN … TYPE` with the columnstore on?

External question (GOAL Problem marks it unverified). Answered from TimescaleDB's own source at the
release tags, read 2026-11-23 from `raw.githubusercontent.com/timescale/timescaledb/<tag>/…` and the
GitHub issue/PR records. No TimescaleDB server was available locally.

## Finding

**It depends on the release.**

- **2.24.0 and later (every CI image: 2.24.0 floor, 2.31.0 Community, 2.31.0 Apache-only): allowed
  while no chunk is compressed.** `src/process_utility.c`,
  `check_alter_table_allowed_on_ht_with_compression`, at tags `2.24.0` and `2.31.0`:

  ```c
  case AT_AlterColumnType:
      /* Allow AT_AlterColumnType when no compressed chunks exist */
      if (!ts_hypertable_has_compressed_chunks(ht))
          continue;
      ereport(ERROR, (errcode(ERRCODE_FEATURE_NOT_SUPPORTED),
              errmsg("operation not supported on hypertables with compressed chunks")));
  ```

  Added by timescale/timescaledb PR #8849 "Allow ALTER COLUMN TYPE when no compressed chunks"
  (merged 2025-11-08, fixes issue #8840), first released in 2.24.0 (CHANGELOG `## 2.24.0
  (2025-12-03)`: "#8840 Allow `ALTER COLUMN TYPE` when compression is enabled but no compressed
  chunks exist"). The PR's regression test (`tsl/test/sql/compression_ddl.sql`) changes the type of
  a **segmentby** column and a plain column with compression enabled and no compressed chunk,
  then compresses successfully, and shows the refusal once a chunk is compressed.

- **2.22.x and 2.23.x (inside HyperShell's `MIN_VERSION` 2.22.0, not in CI): refused whenever the
  columnstore is enabled**, compressed chunks or not. At tag `2.23.0` the same function has no
  `AT_AlterColumnType` case; it falls to `default:` → `ERROR: operation not supported on
  hypertables that have columnstore enabled` (`ERRCODE_FEATURE_NOT_SUPPORTED`, SQLSTATE `0A000`).

- **Any release with a compressed chunk: refused** (message above). This is checked by
  TimescaleDB itself inside the `ALTER`, so a chunk compressed by the policy between our probe and
  our statement still fails safely (the transaction rolls back).

- **Apache-only build:** no columnstore, so the check returns early; a plain `ALTER` works.

- The column type check proper (`process_alter_column_type_start`) refuses only hash-partitioned
  columns, custom-partitioning columns, columns with chunk-skipping stats on a non-integer target,
  and granular-refresh columns. `memory` is none of these.

## Turning the columnstore off (the dance on 2.22/2.23)

`tsl/src/compression/create.c`, `disable_compression`, at `2.23.0` and `2.31.0`: refuses with
"cannot disable columnstore on hypertable with columnstore chunks" when any chunk is compressed;
otherwise drops the internal compressed hypertable (2.23) or unsets compression (2.31) and deletes
the compression settings. It takes `AccessExclusiveLock` on the hypertable and does **not** require
or remove a columnstore policy. HyperShell never adds a policy below `POLICY_VERSION` 2.25.0, so on
the releases that need the dance a policy exists only if the operator added one; it is left alone
and works again once the columnstore is re-enabled.

`ALTER TABLE task SET (timescaledb.enable_columnstore = false)` uses the same option names the
converge step already sends (`timescaledb.enable_columnstore`, valid from 2.22.0).

## Probe for the plan

`timescaledb_information.chunks.is_compressed` exists from 2.22.0 through 2.31.0
(`sql/views.sql`, `srcch.status & 1 = 1`); `timescaledb_information.hypertables.compression_enabled`
likewise. Both are readable only when the extension exists in the database, so the probe first
checks `pg_extension`.

## Consequence for the design

On every CI image the "widen directly" branch of Q1 option A applies, with the columnstore left on.
The "turn it off, widen, converge turns it back on" branch is needed only on 2.22.x/2.23.x
Community servers. It can be exercised in CI only by forcing the release gate (a module constant)
below the server's version on the 2.24.0 and 2.31.0 Community entries; the statements it sends
work on those releases too (disable is allowed with no compressed chunk).
