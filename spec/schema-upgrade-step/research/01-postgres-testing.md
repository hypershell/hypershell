# 01 — PostgreSQL testing

## Recommendation

Add a separate `postgres` job to `.github/workflows/tests.yml`. It runs a GitHub Actions service container, with a `matrix.include` of images (`postgres:13` as the floor, `postgres:18` as current), one Python (3.14), and `uv run --extra postgres` so the job uses `psycopg[binary]` and never depends on the runner's libpq. Before pytest, one step installs `hypershell[postgres]==2.8.3` into an isolated venv and uses 2.8.3's own CLI to build a template database `hs283` (completed, failed-and-retried, and pending tasks). A second step uses `hypershell==2.9.0a1` (on PyPI) to build `hs290a1`, the half-upgraded case. Two env vars gate the PostgreSQL tests: `HS_TEST_POSTGRES` holds an admin URL, and `HS_TEST_POSTGRES_TEMPLATES` lists the template names. They skip when the vars are unset, so the local suite and the existing `tests` job stay SQLite-only. Each test gets its own `CREATE DATABASE … TEMPLATE …`, dropped `WITH (FORCE)` at teardown. A later `timescale` cycle adds one matrix row (`timescale/timescaledb:…`) to the same job. **No PostgreSQL evidence exists until this job runs on a pushed branch. Nothing here was run against a live server.**

## Evidence

- CI today runs SQLite only: there is one `tests` job, no `services:`, and it runs `uv run --python … pytest -v` (`.github/workflows/tests.yml:19-38`). Actions are SHA-pinned: checkout `9c091bb…` # v7.0.0 (`:27`), setup-uv `11f9893…` # v8.3.2 (`:32`).
- The engine is built at import time (`src/hypershell/data/core.py:266-267`). `schema` is popped from config (`:199`). SQLite-only `check_same_thread` goes into `connect_args` when the provider is `sqlite`/`turso` (`:211-214`). The preloaded default `file` is dropped only for `postgres` and the timescale aliases (`:248`).
- **Latent bug (affects test env, not this cycle's scope):** `provider = "postgresql"` fails at import with `ConfigurationError: Cannot provide 'file' if not SQLite`, because `build_preloads` always injects `database.file` (`src/hypershell/core/config.py:280-283`) and `:248` does not strip it for `postgresql`. Reproduced on 2.8.3 and develop without a server. **Tests must use `provider=postgres`.** Worth an `issues/` seed.
- Config env vars come from `Environ(prefix='HYPERSHELL').expand()` (`core/config.py:208-210`). The accepted names are `HYPERSHELL_DATABASE_PROVIDER`, `_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_DATABASE`, and `_SCHEMA`. `_URL` also exists, but it leaves `provider` at `sqlite`, which breaks `DATABASE_DIALECT` and `connect_args`, so don't use it. A `_PASSWORD_ENV` indirection also works (`data/core.py:165-170` strips `_env`/`_eval`). Verified that 2.8.3 resolves `provider=postgres` + host/port/user/password/database/schema to `postgresql+psycopg://…/hs283`, with `Task.__table__.schema` set.
- `tests/conftest.py:31-34` **scrubs every `HYPERSHELL_*` var** at import and before each test. A gate named `HYPERSHELL_TEST_*` would be erased, so the gate must use another prefix (or be captured before the scrub). `temp_site` sets `HYPERSHELL_DATABASE_FILE` (`:54`), which is fatal with `provider=postgres`, so PostgreSQL tests need their own `pg_site` fixture. CLI tests shell out with `env=os.environ` (`tests/__init__.py:28-35`).
- Markers: `--strict-markers` with `unit`/`integration`/`parameterize` (`pyproject.toml:141-147`). Gating is therefore a `skipif`/`pytest.skip`, not a new marker.
- Drivers: the dev group has pure `psycopg>=3.1` (`pyproject.toml:66`), and the `postgres` extra is `psycopg[binary]>=3.1` (`:52`). `uv.lock` has psycopg-binary 3.3.4 manylinux x86_64 wheels for cp311–cp314.
- 2.8.3 (PyPI) ships a `postgres` extra (`psycopg[binary]>=3.1`) plus `postgres-system`/`postgres-c`. It installs cleanly with current psycopg 3.3.6 and SQLAlchemy 2.1.6 (the floor is `sqlalchemy>=2.0.50`) and declares `previous_id`/`next_id` `unique=True`. Its `hs initdb` on PostgreSQL **needs `--yes`** in CI: without a TTY it raises `Non-interactive prompt cannot confirm (see --yes)` (2.8.3 `data/__init__.py` `check_arguments`). 2.8.3 `hsx` supports `--initdb` and `-r NUM` (max retries).
- PostgreSQL versions (postgresql.org/support/versioning, fetched 2026-11-01): 14 reaches EOL 2026-11-12, 15–18 are supported, and 19 is at RC1. Upstream 13 went EOL in 2025-11, but it is still the default on a major enterprise Linux release, so it stays plausible on HPC sites (from memory; confirm before treating it as the floor). The features the step needs: `ADD COLUMN IF NOT EXISTS` ≥ 9.6, `CREATE INDEX IF NOT EXISTS` ≥ 9.5, metadata-only `ADD COLUMN … NOT NULL DEFAULT` ≥ 11, `DROP DATABASE … WITH (FORCE)` ≥ 13.
- Service-container shape (health-cmd `pg_isready`, `ports: 5432:5432`, `POSTGRES_PASSWORD`): docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers.

## Proposed CI job

```yaml
  postgres:
    name: PostgreSQL (${{ matrix.name }})
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        include:
          - { name: pg13, image: 'postgres:13', provider: postgres }   # floor (upstream EOL; distro default)
          - { name: pg18, image: 'postgres:18', provider: postgres }   # current
          # timescale cycle adds: { name: timescale, image: 'timescale/timescaledb:<x.y.z>-pg17', provider: timescale }
    services:
      postgres:
        image: ${{ matrix.image }}
        env:
          POSTGRES_PASSWORD: postgres
        ports: [ '5432:5432' ]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      HS_TEST_POSTGRES: postgresql+psycopg://postgres:postgres@localhost:5432/postgres
      HS_TEST_POSTGRES_PROVIDER: ${{ matrix.provider }}
      HS_TEST_POSTGRES_TEMPLATES: hs283,hs290a1
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Build 2.8.3 and 2.9.0a1 template databases
        run: tests/fixtures/make_legacy_postgres.sh   # see next section; uses docker exec for admin SQL
        env:
          PG_CONTAINER: ${{ job.services.postgres.id }}
      - name: Run PostgreSQL tests
        run: uv run --python 3.14 --extra postgres pytest -v tests/test_data/test_upgrade_postgres.py
```

Notes: `services.<id>.image` accepts the `matrix` context. Admin SQL goes through `docker exec "$PG_CONTAINER" psql -U postgres -c …`, so the job never relies on the runner image having a psql client. Images are tag-pinned. Digest-pinning (`postgres:18@sha256:…`) would match the SHA-pin style, but check whether Dependabot can keep a digest in workflow `services:` current before adopting it. The job runs only the PostgreSQL module: running the whole suite would only repeat the SQLite tests, since they ignore the gate. `timescale` R9 ("full suite on timescale") will need a provider-switchable `site` fixture. That cycle's job, not this one's.

## Test gating & isolation

- **Gate:** `HS_TEST_POSTGRES` (admin URL, non-`HYPERSHELL_` prefix so the conftest scrub leaves it alone). A module-level `pytestmark = [mark.integration, mark.skipif(not os.getenv('HS_TEST_POSTGRES'), reason=…)]`. The R9 tests also skip if their template name is missing from `HS_TEST_POSTGRES_TEMPLATES`.
- **`pg_database` fixture (function scope):** opens an admin engine with `isolation_level='AUTOCOMMIT'` (CREATE/DROP DATABASE cannot run in a transaction) and runs `CREATE DATABASE t_<hex> [TEMPLATE hs283|hs290a1]`. It yields, disposes, then runs `DROP DATABASE … WITH (FORCE)`. **`pg_site`** sets `HYPERSHELL_SITE` (tmp, so exception logs land somewhere), `HYPERSHELL_DATABASE_PROVIDER=postgres` (never `postgresql`), and `_HOST/_PORT/_USER/_PASSWORD/_DATABASE` from `make_url(HS_TEST_POSTGRES)`. It does **not** set `_FILE`. Assertions reflect through a separate `create_engine` on the test database, never through the import-time `hypershell.data.core.engine`.
- **Per-database, not per-schema.** PostgreSQL has no `CREATE SCHEMA … TEMPLATE`, so a per-test schema would mean re-driving 2.8.3 for every test, and the 2.8.3 venv would have to live inside pytest. `CREATE DATABASE … TEMPLATE` copies a 2.8.3-built database in well under a second. Its one constraint, no open connections to the template, holds: the template builders exit before pytest, and the admin engine connects to `postgres`. Concurrent creates from one template only take a share lock, so they should be safe under `-n auto`, but CI should confirm that. Keep **one** test that sets `HYPERSHELL_DATABASE_SCHEMA=alt` in a fresh database (after `CREATE SCHEMA alt`) to prove the step reflects with `schema=` (`checkdb` already does at `data/__init__.py:84`, and `Entity.__table_args__` threads it at `model.py:115-117`).
- **Design hint for the plan:** make the step a function of `(engine, schema)` so unit tests can call it in-process on a throwaway engine for either dialect. The CLI tests then cover R1's command paths.

## 2.8.3-on-PG fixture step

`tests/fixtures/make_legacy_postgres.sh` (bash; a tracked script, so `hs-review` can re-drive it):

1. `uv venv "$RUNNER_TEMP/hs283" && uv pip install --python "$RUNNER_TEMP/hs283" 'hypershell[postgres]==2.8.3'` (an isolated venv, not `uvx`, because both `hs` and `hsx` are needed).
2. `docker exec "$PG_CONTAINER" psql -U postgres -c 'CREATE DATABASE hs283'`.
3. With `HYPERSHELL_CONFIG_FILE=''`, `HYPERSHELL_SITE=$RUNNER_TEMP/site283`, `HYPERSHELL_DATABASE_PROVIDER=postgres` and host/port/user/password set, plus `HYPERSHELL_DATABASE_DATABASE=hs283`: run `hs initdb --yes`, then `hsx tasks.in -N 2 -r 1` where `tasks.in` holds `echo ok` lines and a `false` (so a failed→retried→failed chain exists, filling `previous_id`/`next_id`), then `hs submit 'echo pending'` to leave a pending row.
4. Half-upgraded: `CREATE DATABASE hs290a1 TEMPLATE hs283`, then `hypershell[postgres]==2.9.0a1` in a second venv runs `hs initdb --yes`. On PostgreSQL, 2.9.0a1's `ensuredb` only runs `checkdb`, so `initdb` is what creates `source` while `task` keeps its 2.8 columns. That is exactly the R1 half-upgraded state.
5. All processes exit before pytest, so the templates are connection-free.

The same script body, with `HYPERSHELL_DATABASE_FILE`, can generate the SQLite fixture. Whether SQLite's copy is generated in CI or checked in is the plan's call.

## Coverage map

| R-ID | SQLite | PostgreSQL (CI job) |
|---|---|---|
| R1 upgrade before first query (untouched + half-upgraded) | yes | yes: also the `checkdb`-only path (no `--initdb`) that SQLite never takes |
| R2 nothing dropped/rewritten; NULL/`0` on old rows | yes | yes: `part` via `NOT NULL DEFAULT 0` on populated rows; `tag` JSONB intact |
| R3 idempotent, quiet, concurrent | yes (file lock) | yes: two concurrent CLI processes; advisory lock / transactional DDL. Racy by nature, so assert both exit 0 and the schema converges |
| R4 model-driven; actionable error, schema unchanged | yes (with caveat below) | yes: transactional rollback of partial ALTERs |
| R5 fresh DB: no chain UNIQUE, plain indexes | yes | yes |
| R6 drop `task_previous_id_key` / `task_next_id_key`, add indexes | n/a | **PG only** |
| R7 forward-only, constraints kept | **SQLite only** | n/a |
| R8 invariant text | doc review | doc review |
| R9 2.8.3 DB end to end (submit, hsx with retry, list) | yes | yes |

What only PostgreSQL reveals: transactional DDL and advisory-lock behaviour; real constraint names (`task_previous_id_key`, PostgreSQL's `<table>_<col>_key` default; reflect them with `get_unique_constraints`, never hardcode); the native `UUID` type for the added `task.source` (assert `information_schema.columns.data_type = 'uuid'`, which catches DDL compiled for the wrong dialect); `JSONB`; and `ADD COLUMN … NOT NULL DEFAULT` on populated tables. SQLite-side caveat for R4: Python's `sqlite3` in legacy transaction mode does not open a transaction before DDL, so "schema unchanged on failure" needs an explicit `BEGIN` there. PostgreSQL won't surface that.

## Open risks / needs CI

- **External wait:** no local PostgreSQL. Every PostgreSQL claim above is unverified until the job runs on a pushed branch (PR to `develop`). Plan the build so the job lands early.
- The `postgres:13` floor is a judgement call (upstream EOL, distro default). The alternative is `15` (the oldest with upstream support after 2026-11-12, and the timescale floor). Add `19` when it goes GA.
- Concurrent `CREATE DATABASE … TEMPLATE` under xdist, the R3 concurrency test's timing, and 2.8.3 `hsx` behaviour against a service container are all unconfirmed.
- The 2.8.3 venv resolves the newest SQLAlchemy (2.1.x). A real 2.8.3 user may have 2.0.x, though the DDL shape should match. Optionally constrain `sqlalchemy<2.1` in step 1.
- Under a future `provider=timescale` row, `data/core.py:232` logs a WARNING at import, which would trip R3's "nothing above DEBUG" assertions. That's for the timescale cycle to handle.
- `provider=postgresql` is broken (Evidence). Out of scope here, but seed it.
