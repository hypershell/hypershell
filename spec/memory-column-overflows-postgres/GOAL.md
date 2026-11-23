# GOAL — On PostgreSQL, a task can declare 2 GiB of memory or more

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** memory-column-overflows-postgres
- **kind:** fix
- **appetite:** small  ·  *one column, one in-place widening, its docs and tests. The TimescaleDB
  half is the risk: if `hs-plan` finds the hypertable cannot be widened without the columnstore
  dance in Clarifications Q1 option A and that dance outgrows a small appetite, it offers the split
  recorded there rather than growing this cycle.*
- **target:** 2.9.0  ·  *for 2.9.0a10; the seed's target.*

**Ruled at the GOAL gate** (2026-11-23). Approved by the foreman, gate judge under the maintainer's
mandate, with the maintainer's own rulings on Q1 and Q2.

- **Q1 (maintainer, 2026-11-23):** option A. Widen a `timescale`-provider database in place: directly
  where TimescaleDB allows it; where the columnstore blocks it and no chunk is compressed, turn the
  columnstore off, widen, and let the converge step turn it back on; where any chunk is compressed,
  refuse with an actionable `SchemaUpgradeError`, schema unchanged. If A outgrows a small appetite,
  the cycle ships B and files A as a follow-up seed.
- **Q2 (maintainer, 2026-11-23):** option A. 2.9.0 only; no 2.8 change.
- **Q1 amended at the plan gate (maintainer, 2026-11-23):** TimescaleDB 2.22.x/2.23.x with the
  columnstore enabled is refused, not toggled (Clarifications, "Q1 amended"; R7).
- The contract is locked; `hs-plan` designs against it.

## Problem

A task's memory is a byte count (`hs submit -m` and an inline `memory:` tag both go through
`parse_bytes`), stored in `task.memory`, a plain `INTEGER` column
(`src/hypershell/data/model.py:436` on `develop` `7c85c83c`, `:250` on `master`). On SQLite that holds
64 bits. On PostgreSQL, and so on the `timescale` provider, it is 32 bits: the largest value is
2,147,483,647 bytes, just under 2 GiB. A task that declares more is refused by the database at
insert. HPC tasks commonly need several to hundreds of GB, so on a server database the `memory`
resource is unusable for exactly the tasks that need it. Every submitter that sets memory hits it:
`hs submit`, `hypershell-mcp`'s `submit_tasks` (a strict expected failure in its PostgreSQL test run,
2026-11-21), and `nf-hypershell`'s pass-through of Nextflow's `memory` directive.

Reproduced on `develop` `7c85c83c` against local PostgreSQL 18, `postgres` provider, fresh database
after `hs initdb --yes` (2026-11-23):

- `hs submit -m 1GB one.txt` submits; the row holds `1073741824`.
- `hs submit -m 4GB four.txt` logs `DataError: (psycopg.errors.NumericValueOutOfRange) integer out
  of range` for `memory: 4294967296`, then **hangs** (killed by a 20 s timeout).
- Afterwards the database holds one task and **two** source rows: the source for `four.txt` was
  committed though none of its tasks was, so a retry is refused as already submitted.
- `information_schema` reports `task.memory` as `integer`.

**Pre-existing**: the column is `INTEGER` on every release since 2.8 and on every 2.9.0 pre-release
through 2.9.0a9.

The in-place upgrade step (`src/hypershell/data/upgrade.py`) "only adds what the models declare and
the database lacks … It never drops, renames, or retypes a table or column", with one enumerated
exception (`RETIRED_UNIQUE`, PostgreSQL only). The user docs promise the same
(`docs/_include/database.rst`: "only adds what is missing … and never drops or rewrites existing
tasks"; `docs/_include/database_alt.rst`), as does `AGENTS.md` § Data layer specifics. So changing
the model alone fixes new databases and leaves every existing one broken.

**Which existing databases need an in-place step.**

- **SQLite** (any release): none. Its `INTEGER` already holds 64 bits.
- **PostgreSQL, `postgres` provider, created by 2.8.x or any 2.9.0 pre-release (2.9.0a1–a9)**: yes.
  The column is `integer` and must be widened in place on first use, as other schema changes are.
- **PostgreSQL, `timescale` provider (2.9.0a3–a9 only; the provider first shipped in 2.9.0a3,
  2026-11-04)**: yes, and harder. `task` is a hypertable, and on a Community-edition server the
  converge step enables the columnstore on it (default `segmentby = client_id`, policy after four
  weeks). TimescaleDB restricts `ALTER COLUMN … TYPE` on a hypertable with the columnstore enabled,
  not only on one with converted chunks; this is **unverified here** (no TimescaleDB server in the
  probe) and `hs-plan` confirms it on the CI's TimescaleDB images (2.24.0 floor, 2.31.0 Community,
  2.31.0 Apache-only). An Apache-only server has a hypertable with no columnstore. With the default
  policy (four weeks) and the provider three weeks old, no alpha database is expected to hold a
  converted chunk yet, but a user can set `compress_after` lower.

None of the other integer columns is at risk in practice: `timeout`, `waited` and `duration` are
seconds, `group` is held to PostgreSQL's 32-bit range on purpose (`ensure_group`), `part` and
`source.task_count` are counters, and `cores`, `exit_status` and `attempt` are `SMALLINT` on purpose.
`memory_max` and `cores_max` are `FLOAT`.

## Outcome / vision

On every provider a task can declare as much memory as a node has, and a database created by an
earlier release is widened to match the first time this release opens it, with its rows intact and
the long table rewrite announced in the log. A database already current costs nothing extra.

## Acceptance criteria (the contract)

- **R1** — WHEN a database is created by this release on any provider (`sqlite`, `postgres`,
  `timescale`), `task.memory` SHALL hold any byte count up to at least 2^63 − 1. On SQLite the
  created schema SHALL be unchanged from today's.
- **R2** — WHEN a task declares more than 2 GiB of memory (`hs submit -m 4GB`, or an inline
  `memory: 4GB` tag) on any provider, the task SHALL be stored with that byte count,
  `hs list memory` and `hs info ID -x memory` SHALL show `4294967296`, the task SHALL run to
  completion on a client that has that much memory, and a retry of it SHALL carry the same count.
- **R3** — WHEN this release first uses a `postgres`-provider database created by an earlier release
  (2.8.x, or 2.9.0a1–a9), the schema-upgrade step SHALL widen `task.memory` to hold R1's range in
  place, keeping every row and every stored value, so that R2 holds on that database afterwards.
- **R4** — WHEN the step widens the column, it SHALL log at INFO, before the table rewrite begins,
  that it is rewriting `task` to widen `task.memory` and that this can take a while on a large
  table, and SHALL log the completed change at INFO as it does other changes.
- **R5** — WHEN `task.memory` already holds R1's range (a database created by this release, or one
  widened by hand), the step SHALL make no change for it, log nothing for it above `trace`, and the
  current-schema path SHALL still take no lock.
- **R6** — IF the widening fails or waits past the step's lock ceiling, THEN the step SHALL roll
  back with the schema and rows unchanged and raise `SchemaUpgradeError` with an actionable message,
  as other changes do; and WHEN two processes first use the same old database at once, the column
  SHALL be widened once and both SHALL proceed.
- **R7** — WHEN this release first uses a `timescale`-provider database created by 2.9.0a3–a9, the
  step SHALL widen the hypertable's `task.memory` in place, directly, wherever TimescaleDB allows it
  (Q1 option A as amended at the plan gate). IF TimescaleDB refuses it, THEN the step SHALL refuse
  with an actionable `SchemaUpgradeError`, the schema unchanged, and SHALL never toggle the
  columnstore. That covers TimescaleDB 2.22.x/2.23.x with the columnstore enabled, whose error names
  the manual steps or upgrading TimescaleDB to 2.24.0 or later, and any release with a compressed
  chunk, whose error names the manual steps. A `postgres`-provider database whose `task` is such a
  hypertable gets the same outcome. The hypertable SHALL keep its columnstore settings and policy
  across the widening.
- **R8** — The user docs on the in-place upgrade (`docs/_include/database.rst`,
  `docs/_include/database_alt.rst`) and `AGENTS.md` § Data layer specifics SHALL state that the step
  also widens `task.memory` on PostgreSQL, rewriting the `task` table under its exclusive lock, marked
  `.. versionchanged:: 2.9.0`; `upgrade.py`'s module docstring SHALL name the exception.
- **R9** — Tests SHALL cover R2 on a server database and R3–R6 against the legacy PostgreSQL
  templates (`hs283`, `hs290a1`) in the existing `HS_TEST_POSTGRES`-gated suite, which CI runs on
  PostgreSQL 13 and 18 and on the TimescaleDB entries; R7 on the TimescaleDB entries; and R2 on
  SQLite in the default suite.

## Non-goals (no-gos)

- **A general type-migration facility.** The step gains one enumerated widening (`task.memory`,
  PostgreSQL only), alongside `RETIRED_UNIQUE`; it does not diff or retype other columns.
- **Bounds on the memory value.** A count beyond what a 64-bit column holds is absurd input
  (`review-rubric.md`: "Absurd input is not a finding"); its range belongs to
  `issues/bounded-inputs.md`, the central-range cycle for 2.9.0. No per-option bound here.
- **The hang after a refused insert.** Any other `DataError` still kills the committer and hangs the
  run; that is `issues/database-locked-during-ingest-hangs.md`. This cycle removes the memory
  trigger only.
- **The source row committed for a submission that stored nothing.** That is the all-or-nothing gap
  in `issues/ingest-robustness.md`.
- **A 2.8.x patch release** carrying the widening (Q2, option A).
- **Changes in other repositories.** `hypershell-mcp`'s strict expected failure (`memory_overflows`
  in `tests/conftest.py`) will start passing against a server on this release; flipping it is that
  repository's change. `nf-hypershell` needs nothing.
- **SQLite.** No schema or upgrade change.
- **Other integer columns.** None is at risk (Problem).

## Clarifications

- **Q (seed status):** the seed is `status: unshaped`, so its draft R-IDs were input, not a contract.
  Shaped here by the `hs-feature` step on the foreman's brief (2026-11-23); every criterion above is
  for the maintainer's sign-off.
- **Q (seed drift, 2026-11-23):** the seed's `data/model.py:349` is now `:436` on `develop`
  `7c85c83c` (`:250` on `master` holds). Its R4 fallback ("if the suite has one … until then, tested
  as SQL against a PostgreSQL dialect") is moot: the `HS_TEST_POSTGRES`-gated suite already exists
  (`tests/test_upgrade_postgres.py`, `tests/test_timescale.py`, `tests/test_server_initdb.py`) and
  CI's `postgres` job runs it on PostgreSQL 13 and 18 and three TimescaleDB images, so R9 asks for
  real-server tests. Its TimescaleDB note asks about compressed chunks; the likelier block is the
  columnstore being enabled at all (Problem), unverified.
- **Q1 — how a `timescale`-provider database from 2.9.0a3–a9 is widened (R7). Resolved
  2026-11-23** (maintainer): **option A**. Widen directly where TimescaleDB allows it; where the
  columnstore blocks it and no chunk is compressed, turn the columnstore off, widen, and let the
  converge step turn it back on; where any chunk is compressed, refuse with an actionable
  `SchemaUpgradeError` and leave the schema unchanged. If A outgrows a small appetite, the cycle
  ships B (widen directly, else refuse and document the manual steps) and files A as a follow-up
  seed. The options as shaped:
  - **A (recommended):** widen in place. Where TimescaleDB allows `ALTER COLUMN … TYPE` on the
    hypertable as it stands (an Apache-only server, or a release that permits it), do it directly.
    Where the enabled columnstore blocks it and no chunk has been converted, turn the columnstore off,
    widen, and let the converge step that already follows the upgrade turn it back on with the
    configured settings and policy. Where a chunk has been converted, refuse with
    `SchemaUpgradeError`, schema unchanged, naming the manual steps (convert the chunks back, then
    start again). Rationale: the provider is three weeks old and the default policy converts after
    four, so in practice every alpha database takes the automatic path, and no user data is
    decompressed without the user's say. If `hs-plan` finds this outgrows a small appetite, split:
    this cycle ships R1–R6, R8–R9 and option B for R7, and option A becomes a follow-up seed.
  - **B:** widen only where TimescaleDB allows it directly; otherwise refuse with an actionable
    error and document the manual steps. Smaller; a Community-edition alpha user does a manual step.
  - **C:** no in-place path for the `timescale` provider: it only shipped in pre-releases, so refuse
    with an error telling the user to recreate the database or widen by hand.
- **Q2 — does 2.8.x get a patch? Resolved 2026-11-23** (maintainer): **option A**. 2.9.0 only,
  with no 2.8 change. A 2.8 user on PostgreSQL has the same limit. The options as shaped:
  - **A (recommended):** no. 2.9.0 only. A retyping migration that rewrites the `task` table is a
    large step for a patch release, and R5 already makes a database widened by hand safe for 2.9.0.
  - **B:** 2.9.0 only, and the docs also give 2.8 PostgreSQL users the one manual statement that
    widens the column (2.8 reads and writes a widened column unchanged), so they need not wait.
    One more paragraph of docs.
  - **C:** also backport the widening to a 2.8.x patch.
- **Q1 amended. Resolved 2026-11-23** (maintainer, at the plan gate, answering PLAN §5): on
  TimescaleDB 2.22.x/2.23.x with the columnstore enabled, **refuse** with an actionable
  `SchemaUpgradeError` that gives the manual steps or says to upgrade TimescaleDB to 2.24.0 or later,
  the schema unchanged. There is no columnstore off/on toggling anywhere in the upgrade path, no
  `converges=` flag, and no converge call from `checkdb`. TimescaleDB 2.24.0 and later (columnstore
  on, nothing compressed) widen directly, and any release with a compressed chunk is refused, as
  planned. The `postgres` provider follows the same rule: it refuses where TimescaleDB would refuse.
  This supersedes Q1 option A for the 2.22/2.23 case only; R7 is reworded to match.

## Related materials

- Seed: [`issues/memory-column-overflows-postgres.md`](../../issues/memory-column-overflows-postgres.md)
- Source: `src/hypershell/data/model.py` (`INTEGER`, `SMALL_INTEGER`, `Task.memory`),
  `src/hypershell/data/upgrade.py` (`_plan`, `RETIRED_UNIQUE`, `_apply_transactional`, the lock
  slices), `src/hypershell/data/timescale.py` (`converge`, `Settings`),
  `src/hypershell/data/__init__.py` (`initdb`, `checkdb`, `upgradedb`)
- Tests: `tests/test_upgrade_postgres.py`, `tests/test_timescale.py`, `tests/test_server_initdb.py`,
  `tests/test_data/test_upgrade.py`, `tests/fixtures/make-legacy-postgres.sh`,
  `.github/workflows/tests.yml` (`postgres` job)
- Docs: `docs/_include/database.rst`, `docs/_include/database_alt.rst`; `AGENTS.md` § Data layer
  specifics; `.agents/factory/invariants.md` (§1, §3 `Task.retry_columns` carries `memory`)
- Related seeds: `issues/database-locked-during-ingest-hangs.md`, `issues/ingest-robustness.md`,
  `issues/bounded-inputs.md`, `issues/server-db-test-suite.md`
- Elsewhere: `hypershell/hypershell-mcp` (`tests/conftest.py`, `memory_overflows`)
