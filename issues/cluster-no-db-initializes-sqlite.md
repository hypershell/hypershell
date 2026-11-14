---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `hs cluster --no-db` still initializes a SQLite database it never uses

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`--no-db` means the cluster's server holds every task in memory and never uses the database. On
start-up, though, `ClusterApp.__enter__` (`src/hypershell/cluster/__init__.py:555-562` on
`develop`, `f6a1d62`) runs `initdb()` whenever the provider is SQLite, before it looks at
`--no-db`:

```python
if DATABASE_DIALECT == 'sqlite' or self.auto_initdb:
    initdb()  # Auto-initialize if local sqlite provider
elif not self.in_memory:
    checkdb()
```

So `hs cluster --no-db` (and `hsx --no-db`) creates or upgrades the configured SQLite file, the
user's own site database by default, although the run never reads or writes it. A schema step that
refuses or fails on that file would end a run that does not need it. On PostgreSQL and TimescaleDB
the same call is already skipped under `--no-db`.

Reproduced on `develop` (`f6a1d62`), 2026-11-14: in a throwaway site,
`hs cluster in.txt --no-db -N1 -t 'echo {}' --no-tls` runs both tasks and leaves `task.db` with
the `client`, `source`, `task` and `zone` tables.

The server half of the same gap ("Hole C", `hs server --no-db` touching the database) was fixed by
`server-initdb` (#105, its R5). That cycle's GOAL kept `hs cluster` out of scope and named the
cluster half as deferred work.

## Direction

Under `--no-db`, `hs cluster` should neither initialize nor check the database, on any provider,
as `hs server --no-db` now does. `--initdb` and `--no-db` are already mutually exclusive.

## Related

- `spec/server-initdb/` (GOAL *Problem* and *Non-goals*; R5 for the server half).
- `spec/schema-upgrade-step/research/02-upgrade-seam.md` §5, where "Hole C" was first recorded.
- Found by: `server-initdb` shaping probe, 2026-11-13; filed at that seed's retirement.
