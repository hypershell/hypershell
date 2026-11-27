# 05 — The 2.8.3 fixture and red-first

## Recommendation

Use option **(c)**. Commit one small SQLite file made by real hypershell 2.8.3, the generator that
made it, and a provenance note. Add an **opt-in** test that regenerates the file from PyPI
`hypershell==2.8.3` and compares its DDL and row shape with the committed copy. Default runs and CI
stay offline and deterministic, and `hs-review` can re-drive the generator by hand.

- **Where.** Create a new `tests/fixtures/` directory holding `hypershell-2.8.3.db`,
  `make-2.8.3-db.sh` and `README.md` (the provenance note). `tests/test_data/` is the wrong place:
  it holds the unit tests for `hypershell.data`, not data files. Nothing named `test_*.py` goes in
  `tests/fixtures/`, so pytest collects nothing from it.
- **Size and packaging.** The file is 40,960 bytes (10 pages of 4 KiB, no freelist) and 1.9 KB
  gzipped. The sdist includes `/tests` (`[tool.hatch.build.targets.sdist]`), so the file ships in
  the sdist. The wheel ships only `src/hypershell` plus `share/`, so it is not in the wheel. There
  is no `.gitattributes`, and git detects the file as binary. There is no REUSE tooling, so the
  file needs no SPDX sidecar.
- **Copy, never mutate.** Tests run
  `shutil.copyfile(FIXTURE, Path(os.environ['HYPERSHELL_DATABASE_FILE']))`, which writes
  `temp_site/local.db` after `temp_site` has set the environment. The committed file is never
  opened read-write. The snapshot helper opens it as `file:…?mode=ro`.
- **Half-upgraded state (R1).** Derive it in the test from the copy by applying the frozen 2.9.0a1
  DDL below. Do not commit a second `.db`. The opt-in test can also prove that PyPI `2.9.0a1`
  still produces exactly that DDL.
- **Option (b) rejected as the default.** Generating at test time puts PyPI on the test path. A
  cold install takes about 1.2 s and 48 MB here, and is slower on CI. It fails offline. The
  generator needs a Python that 2.8.3 supports, so it should be pinned to 3.12 instead of each
  3.11–3.14 matrix leg.

## Generator recipe

This recipe was run three times, from two different environments: a reused venv and a cold
`uv pip install hypershell==2.8.3`. Each run took about 6 s. Every run gave equal `sqlite_master`
DDL and an equal row shape (compared with ids remapped to ordinals). Setup:

```sh
UV_CACHE_DIR="$TMPDIR/uv-cache" uv venv --python 3.12 "$TMPDIR/env283"
UV_CACHE_DIR="$TMPDIR/uv-cache" uv pip install --python "$TMPDIR/env283/bin/python" hypershell==2.8.3
sh make-2.8.3-db.sh "$TMPDIR/env283/bin/python" "$TMPDIR/out"   # -> $TMPDIR/out/hypershell-2.8.3.db
```

`make-2.8.3-db.sh`, verbatim as run:

```sh
#!/bin/sh
# Generate a SQLite database with hypershell==2.8.3's own code paths.
# Usage: make-2.8.3-db.sh <python-with-hypershell-2.8.3> <out-dir>
# socket.gethostname is pinned so no developer hostname lands in the file.
set -eu
PY="$1"; OUT="$2"
mkdir -p "$OUT"; cd "$OUT"
HYPERSHELL_CONFIG_FILE=''
HYPERSHELL_SITE="$OUT"
HYPERSHELL_DATABASE_FILE="$OUT/hypershell-2.8.3.db"
HYPERSHELL_SERVER_PORT="$("$PY" -c 'import socket;s=socket.socket();s.bind(("localhost",0));print(s.getsockname()[1])')"
TZ=UTC; export TZ HYPERSHELL_CONFIG_FILE HYPERSHELL_SITE HYPERSHELL_DATABASE_FILE HYPERSHELL_SERVER_PORT
"$PY" -c 'import hypershell; assert hypershell.__version__ == "2.8.3", hypershell.__version__'
hs283() {  # $1 = main | main_x ; rest = CLI args
  entry="$1"; shift
  "$PY" -c 'import socket, sys
socket.gethostname = lambda: "fixture-host"
import hypershell
sys.argv[0] = "hs"
sys.exit(getattr(hypershell, sys.argv.pop(1))())' "$entry" "$@"
}
cat > run.in <<'IN'
echo done-0  # HYPERSHELL: kind:done n:0
echo done-1  # HYPERSHELL: kind:done n:1
echo done-2  # HYPERSHELL: kind:done n:2
sh -c 'exit 3'  # HYPERSHELL: kind:fail
IN
# 1. three completed tasks + one failure retried once (attempt 1 retried=1 -> attempt 2 failed)
hs283 main_x run.in -N2 -b1 -w1 --max-retries 1 --no-tls -o out.txt -e err.txt
# 2. still-pending tasks, submitted after the run
hs283 main submit -t kind:pending n:0 -- echo pending-0
hs283 main submit -t kind:pending n:1 -- echo pending-1
```

**The script pins the hostname and the timezone.** 2.8.3 writes `socket.gethostname()` into
`task.submit_host`, `server_host`, `client_host` and `client.host`. A naive run left the
developer's hostname in the file nine times. With the patch it appears zero times, and every one
of those fields reads `fixture-host`. This works because local `hsx` runs its client in-process.
`TZ=UTC` keeps the generating machine's local time out of the naive timestamps.

**2.8.3 CLI facts.**

- `-w` is an integer: `-w0.1` is rejected with "invalid int value".
- `--no-tls` exists on both `hsx` and `hs submit`. No auth flag is needed for a local `hsx`.
- `hs submit` takes `-t TAG...` and needs `--` before the command.
- `hs list --all` does not exist in 2.8.3.
- `hsx` exits 0 even when tasks fail.
- SQLite auto-initializes on first use.
- The environment that made the file: Python 3.12.13, SQLAlchemy 2.1.6, cmdkit 2.7.7.

## Observed 2.8.3 schema & rows

```sql
CREATE TABLE client (id TEXT NOT NULL, host TEXT NOT NULL, server_id TEXT NOT NULL, server_host TEXT NOT NULL,
  connected_at DATETIME, disconnected_at DATETIME, evicted BOOLEAN NOT NULL, PRIMARY KEY (id))
CREATE TABLE task (id TEXT NOT NULL, "group" INTEGER NOT NULL, args TEXT NOT NULL, submit_id TEXT NOT NULL,
  submit_time DATETIME NOT NULL, submit_host TEXT, cores INTEGER, memory INTEGER, cores_max FLOAT,
  memory_max FLOAT, timeout INTEGER, server_id TEXT, server_host TEXT, schedule_time DATETIME, client_id TEXT,
  client_host TEXT, command TEXT, start_time DATETIME, completion_time DATETIME, exit_status INTEGER,
  outpath TEXT, errpath TEXT, csvpath TEXT, attempt INTEGER NOT NULL, retried BOOLEAN NOT NULL,
  waited INTEGER, duration INTEGER, previous_id TEXT, next_id TEXT, tag JSON NOT NULL,
  PRIMARY KEY (id), UNIQUE (previous_id), UNIQUE (next_id))
CREATE INDEX client_disconnected_at ON client (disconnected_at)
CREATE INDEX index_tasks_retries ON task (exit_status, retried)
CREATE INDEX index_tasks_unscheduled ON task ("group", schedule_time)
-- plus sqlite_autoindex_client_1, sqlite_autoindex_task_1..3 (PK + the two UNIQUEs)
```

- The schema has no `source` table and no `source`, `fingerprint` or `part` column.
- `PRAGMA user_version` = 0 and `journal_mode` = delete.
- The `task` table has 7 rows; the `client` table has 1. The `task` rows are:

| args | attempt | retried | exit | chain | tag (raw) |
|------|---------|---------|------|-------|-----------|
| `echo done-0..2` (×3) | 1 | 0 | 0 | none | `{"kind": "done", "n": 0, "part": 0}` |
| `sh -c 'exit 3'` | 1 | 1 | 3 | `next_id` = attempt 2 | `{"kind": "fail", "part": 0}` |
| `sh -c 'exit 3'` | 2 | 0 | 3 | `previous_id` = attempt 1 | `{"kind": "fail", "part": 0}` |
| `echo pending-0/1` (×2) | 1 | 0 | NULL | unscheduled | `{"kind": "pending", "n": 0, "part": 0}` |

All rows are in group 0. Ids are uuid4. The `n` tag value is stored as an int.

## Run-step invocation under develop

Run `hsx --restart -r 2 -N2 -b1 -w1 --no-tls`. A bare `--restart` with no FILE is the pure
database resume: `cluster/__init__.py` yields an empty source, and `Scheduler.submitter` is `None`,
so `submission_complete()` is true. The scheduler reverts interrupted rows and runs what is
unfinished.

- **Why `-r 2`.** The scheduler uses `attempts = max_retries + 1`, and `select_failed` filters on
  `attempt < attempts`. The stored attempt-2 failure is therefore retried only when `-r` is at
  least 2. With `-r 1`, no retry happens and a test that does not check for one passes silently.
- **Order.** Non-eager mode (`__next_not_eager`) schedules the new pending rows first and the
  failed row once `select_new` comes up short. The halt test (`count_remaining() == 0`) fires only
  after attempt 3 completes.
- **Group gating.** `increment_group` reports a group not viable only when no rows were selected
  and `remaining == 0` was not hit. That never happens here, because the halt fires first.
- `hs server --restart -r 2` with `hs client` is the two-process equivalent. Feeding `/dev/null` as
  FILE drags in source gating; avoid it.

**Validation.** I took a copy of the fixture after the base build had half-upgraded it, added the
three columns by hand as a stand-in for the fix (`source TEXT`, `fingerprint TEXT`,
`part INTEGER NOT NULL DEFAULT 0`, plus `index_tasks_source`), and ran develop against it. The
SQLite UNIQUE constraints were left in place, as R7 requires.

- `hs submit -t kind:new -- echo new-0` exited 0.
- `hsx --restart -r 2 …` exited 0 after about 4 s. Its stdout was `pending-0`, `pending-1` and
  `new-0`, and it logged `Non-zero exit status (3)` for the new attempt 3.
- `hs list --count` printed 9. `hs list --remaining --count` printed 0.
- The attempt-2 row now reads `retried=1, next_id=<attempt3>`. Attempt 3 has
  `previous_id=<attempt2>` and inherits the tag `{"kind": "fail", "part": 0}`. It has
  `part=0` and `source=NULL`, and gets a computed fingerprint.
- The new rows use uuid7 ids.
- `hs list --all -f plain` prints the old rows with `null` for the new columns.

Bare `hs list` with no arguments prints usage and exits 1. Use `--all` or `--count`.

## Red-first evidence

This was run at base `6c164d9` (`hypershell.__version__ == 2.9.0a1`), with `uv run` from the
worktree, an isolated environment, and a copy of the fixture:

```
$ hs submit 'echo new'
CRITICAL [hypershell.submit] OperationalError: (sqlite3.OperationalError) no such column: task.source - [SQL: SELECT task.id AS task_id, … FROM task WHERE task.schedule_time IS NOT NULL ORDER BY task.schedule_time DESC LIMIT ? OFFSET ?] …
exit=5          # cmdkit exit_status.runtime_error
$ hs list --all
CRITICAL [hypershell.task] OperationalError: (sqlite3.OperationalError) no such column: task.source - [SQL: SELECT task.id AS task_id, … FROM task] …
exit=5
$ hsx --restart -r 2 -N2 -b1 -w1 --no-tls
CRITICAL [hypershell.core.fsm] Uncaught exception from <class 'hypershell.server.Scheduler'>
CRITICAL [hypershell.core.fsm] OperationalError: (sqlite3.OperationalError) no such column: task.source - [SQL: SELECT count(*) …
exit=5
```

An R9 test that asserts `rc == exit_status.success` is therefore red on the base at its very
first step. The row count stays at 7, because the failed submit wrote no task.

## Half-upgraded state

After one failed base `hs submit`, `sqlite_master` gains exactly three objects:

```sql
CREATE TABLE source (id TEXT NOT NULL, path TEXT NOT NULL, fingerprint TEXT, task_count INTEGER,
  created DATETIME NOT NULL, PRIMARY KEY (id))
CREATE INDEX index_source_lookup ON source (path, fingerprint)
-- sqlite_autoindex_source_1
```

`task` is unchanged, and **`index_tasks_source` is not created**: `create_all` skips the indexes
of a table that already exists. Running PyPI `hypershell==2.9.0a1` against a fresh copy gives a
**byte-identical** `sqlite_master` to the base build.

**Recommendation.** Freeze those two `CREATE` statements as a constant in the test, and apply them
with raw `sqlite3` to a copy of the 2.8.3 fixture. This is deterministic and needs no network. It
also does not depend on the model under test. `Source.__table__.create(engine)` would be
tautological after the fix. Letting the base fail once is not reproducible in-tree.

## Assertions that survive random ids

Snapshot with raw `sqlite3`, never through the ORM, so that `tag` is compared as the stored
string:
`snap = {r[0]: r for r in con.execute('SELECT <the 30 original task columns> FROM task')}`.
Key the snapshot by `id` and compare dicts; do not compare by position, because the new rows sort
differently (uuid7). Take one snapshot of the copy before the first command, then:

1. **After the first command (the upgrade, R1 and R2).** Every original id is present and its 30
   original columns equal the snapshot exactly, including the raw `tag` text with `"part": 0`.
   `source` and `fingerprint` are NULL and `part` is 0. The `client` row is unchanged.
2. **After `hsx --restart -r 2` (R9).** The done rows and fail attempt 1 are entirely unchanged.
   Fail attempt 2 differs only in `retried` (0 → 1) and `next_id`. The pending rows keep `args`,
   `tag` and the `submit_*` columns, and now have `exit_status = 0` and a `completion_time`. There
   is exactly one new row with `args = "sh -c 'exit 3'"` and `attempt = 3`, whose
   `previous_id` = attempt 2's id.
3. **Counts and output.** `hs list --count` gives 7 → 8 after the submit, then 9 after the run.
   `--remaining --count` gives 0. `sorted(stdout.split())` gives `['new-0', 'pending-0',
   'pending-1']`. Exactly one `Non-zero exit status (3)` line is logged.
4. **Idempotence (R3).** A second command logs nothing above DEBUG from the upgrade step, and
   `sqlite_master` is unchanged.

## Open risks

- **The hostname patch works only while 2.8.3 runs the client in-process.** The generator must
  re-check that the developer's hostname is absent before the file is committed (add that check to
  the script).
- **The opt-in regeneration test needs PyPI and a 3.12 interpreter.** Gate it with
  `skipif(not os.environ.get(...))`; strict markers allow only `unit`, `integration` and builtins.
- **Each `hsx` run takes about 4 s.** Keep R9 down to one end-to-end test plus fast
  `hs submit` / `hs list` checks.
- **The validation above used a hand ALTER as a stand-in for the fix.** It proves the run step and
  the R7 retry with UNIQUE kept, not the upgrade step itself.
- **Local TCP binding is required.** Sandboxed runners must allow it.
