# Test fixtures

Data files used by the test suite. Nothing here is collected by pytest.

## `hypershell-2.8.3.db`

A SQLite database created by **hypershell 2.8.3** (from PyPI) through its own command line. The
upgrade tests (`tests/test_upgrade_cli.py`) open copies of it with the current build.

**Never open this file read-write.** Tests copy it into a temporary site first
(`tests.copy_fixture`) and read the committed file only in read-only mode.

### Contents

- `task`: 7 rows, all in group 0, each `tag` carrying the 2.8 `"part": 0` key.
  - 3 completed tasks: `echo done-0`, `echo done-1`, `echo done-2` (`kind:done`, `n:<N>`).
  - 1 failed chain: `sh -c 'exit 3'` (`kind:fail`), attempt 1 (exit 3, `retried`, `next_id` set)
    and attempt 2 (exit 3, `previous_id` set, not retried).
  - 2 pending tasks: `echo pending-0`, `echo pending-1` (`kind:pending`), never scheduled.
- `client`: 1 row.
- The 2.8.3 schema: no `source` table, no `source`, `fingerprint` or `part` column, and
  `UNIQUE (previous_id)` / `UNIQUE (next_id)` on `task`.

Every host column reads `fixture-host` and every timestamp is UTC, so the file carries no detail
of the machine that made it.

### Provenance

| Item | Value |
|------|-------|
| hypershell | 2.8.3 |
| Python | 3.12.13 |
| SQLAlchemy | 2.1.6 |
| cmdkit | 2.7.7 |
| Size | 40,960 bytes |
| sha256 | `f3681c0d22a51b784dfb9fe66cad4a199a714368f4334d1492d8e112ed909b01` |

Produced with [`make-2.8.3-db.sh`](make-2.8.3-db.sh):

```sh
uv venv --python 3.12 "$TMPDIR/env283"
uv pip install --python "$TMPDIR/env283/bin/python" hypershell==2.8.3
sh tests/fixtures/make-2.8.3-db.sh "$TMPDIR/env283/bin/python" "$TMPDIR/out"
cp "$TMPDIR/out/hypershell-2.8.3.db" tests/fixtures/
```

The script isolates every `HYPERSHELL_*` variable, binds a free local port, pins `TZ=UTC` and
`socket.gethostname`, runs `hsx` over four tasks with `--max-retries 1`, then submits two more.
It refuses to keep the file if the local hostname, username or output directory appears in it.

Ids are random (uuid4) and timestamps are wall-clock, so a regenerated file differs byte for byte.
Its schema and row shape (ids mapped to ordinals) are the same.
