# 02 — R7 (a retry keeps cores, memory, walltime) and its composition with #118

Tree: `fix/update-client-retry` at `eb2a4487`. `data/model.py` is byte-identical on `6a0c28ea`
(the GOAL's commit and #118's merge base), `origin/develop` and our HEAD, so the GOAL's
`model.py:889-896` still holds.

## Construction site (one, and only one)

`Task.__schedule_next_failed_tasks`, `src/hypershell/data/model.py:874-912`; the row is built at
`:890-897` by `cls.new(args, attempt+1, previous_id, tag, group, fingerprint, source, zone)`. Both
`Task.next` paths reach it (`__next_eager` `:854`, `__next_not_eager` `:868`). No second copy exists:

- **relay** (`relay/store.py:201-231`) upserts every `Task.columns` entry of rows the upstream server
  built; it never builds a retry. A fixed retry reaches site clients unchanged.
- **`hs update --revert` / `revert_*`** mutate in place and clear only `revert_columns`
  (`:449-463`), which hold none of cores/memory/timeout.
- **`--restart` / `hs submit --restart|--update`** resume or skip by fingerprint; they build no row
  from an old one. Other `Task.new` callers (`task.py:114,445`, `submit.py:358,389,1473`) are new tasks.

Probed (SQLite, our tree): a task with `cores=4, memory=1GiB, timeout=2` failed `-9` → retry row
`cores/memory/timeout = None None None`, tag and zone copied. Confirms the GOAL.

## Column classification (34 columns)

| Class | Columns |
|---|---|
| Copied today | `args`, `tag`, `group`, `fingerprint`, `source`, `zone` |
| **R7: must copy** | `cores` (SMALL_INTEGER, NULL = untracked), `memory` (INTEGER bytes), `timeout` (INTEGER seconds) |
| Fresh per row (never copy) | `id`, `submit_id`/`submit_host`/`submit_time` (stamped now; #118's `reached_group` counts a retry row by `submit_time >= pass start`, so it MUST stay fresh), `attempt` (+1), `retried` (False), `previous_id` (parent), `next_id` (NULL), `server_id`/`server_host`/`schedule_time` (dispatch stamp), `part` (rotation bookkeeping, default 0) |
| Result columns (never copy) | `Task.result_columns` (`:432-446`): `client_id`, `client_host`, `command`, `start_time`, `completion_time`, `waited`, `duration`, `exit_status`, `outpath`, `errpath`, `csvpath`, `cores_max`, `memory_max` |

9 + 12 + 13 = 34 = `Task.columns`. No other "declaration" column exists (zone is already copied;
`part` is not one). Out of scope: nothing else.

**How they are populated.** `hs submit -c/-m/-W` (`submit.py:1363-1367`), `hs server -c/-m/-W`
(`--task-cores/--task-memory/--task-timeout`, `server.py:1847-1852`) and `hs cluster -c/-m/-W`
(`cluster/__init__.py:235-251`, into `ServerThread(task_cores, task_memory, task_timeout)`), each
defaulting to `config.task.*`; inline `# HYPERSHELL: cores:/memory:/timeout:` tags, `-t` tags, and
`--from-json` keys of those names are popped into the columns by `Task.new` (`:536-553`).

**How the client reads them.** `insufficient_resources` (`client.py:684-691`, → `-1002`);
`acquire_resources`/backfill (`:591`, a task without `timeout` is never backfilled; `:608-668`);
walltime kill in `check_task` (`:936`): `min(client -W, task.timeout)`. **Probe trap:** every cluster
class also passes `-W` to its clients as `task_timeout` (`local.py:261`, `remote.py:317,1028`,
`ssh.py:314`), so under `hs cluster -W` the retry is killed anyway; the column's walltime is visible
only with `hs submit -W` (or an inline/JSON `timeout`) and a client without `-W`. `hs cluster -c/-m`
reach only the column (`-C/-M` are the client totals), so cores/memory drop under every launcher.
Nothing else reads the three columns (not `server.py`, not `cluster/demand.py`).

## What #118 does to the same construction

`git diff origin/develop...fix/submit-input-checks -- data/model.py`, hunk at our `:887-897`
(theirs `:1007-1027`): the dict comprehension becomes a loop that

1. copies `task.tag`, pops a `group` key into `group_tag` (G1: a `group` tag written by
   `hs update`'s tag form is neither read as the group nor checked);
2. calls `cls.new(args=task.args, parse_inline=False, strict_tag=False, …same kwargs…)` (F2: the
   stored command is not re-parsed for `# HYPERSHELL:`; JSON-relaxed tags are not re-checked);
3. restores the key: `retry.tag = {**retry.tag, **group_tag}`.

`Task.new` is otherwise unchanged except its group line (`ensure_group` on a tag group). #118's
tests pinning the retry (`tests/test_group_integer.py`: `test_cluster_json_retry_keeps_command_and_group`,
`test_cluster_json_retry_keeps_tags`, `test_cluster_retry_ignores_group_tag`) select explicit
columns (`id, args, "group", attempt, previous_id, exit_status, tag`); none reads cores/memory/timeout
and the tag assertions expect the tag whole, so **none needs an R7 addition and none breaks** under
the design below. The `AGENTS.md`/`invariants.md` prose it adds ("a retry copies a legacy text group")
stays true.

**The same hazard as G1 exists for the three R7 keys.** `hs update KEY:VALUE` can store a tag named
`cores`, `memory` or `timeout` (`task.py:1189-1194`; `memory`/`timeout` must parse, `cores` is any
tag-safe text). Today and after #118, `Task.new` pops such a tag into the retry's column and drops it
from the retry's tags. Probed (SQLite): tag `cores:8` → retry `cores=8`, tag `{}`; `memory:2GB` →
`2147483648`; `cores:x` → the string `'x'` stored in the SMALL_INTEGER column (on PostgreSQL this is
expected to fail the insert inside the scheduler, the G1 crash class; not probed). Merely adding
`cores=task.cores` keeps that: the tag still wins (`tag.pop('cores', other.get('cores'))`).

## Recommended design

**`Task.retry_columns`** — a `Final` tuple beside `result_columns`/`revert_columns`:
`('group', 'args', 'cores', 'memory', 'timeout', 'source', 'fingerprint', 'zone', 'tag')`, and
**`Task.new_retry(task) -> Task`**, a classmethod that builds the row directly:
`Task(id=uuid(), submit_id=INSTANCE, submit_host=HOSTNAME, submit_time=now, attempt=task.attempt + 1,
retried=False, previous_id=task.id, **{name: getattr(task, name) for name in retry_columns})`, with
`tag` copied as a new `dict(task.tag or {})`. `__schedule_next_failed_tasks` becomes
`retries = {task.id: cls.new_retry(task) for task in failed_tasks}`.

Why: `Task.new` contributes nothing to a retry but the stamps — #118 already turns off its inline
parse and strict check and undoes its group pop; the resource pops are the last parse left. Building
from columns makes F2, G1 and R7 one rule ("a retry is its task's declaration columns, as stored"),
removes the `cores:x` crash path, and a fence test (below) forces every future column into a class,
which is how R7's omission happened. Group is copied as stored (a legacy text group too), matching
#118's prose.

**Behaviour note for the plan (deviation table / release note):** a `cores`/`memory`/`timeout`
*tag* written by `hs update`'s tag form no longer sets the retry's column; it stays a tag, as G1 ruled
for `group`. The column form (`hs update cores=4`) is unaffected and is what the retry reads. If the
plan prefers the minimal diff instead: keep `Task.new`, pass `cores=/memory=/timeout=`, and widen #118's
`group_tag` pop/restore to the four keys (`RETRY_RESERVED_TAGS`); same behaviour, more code.

## Composition with #118 (expected conflict)

Only one textual conflict in source: the retry-construction hunk, our `model.py:889-897` vs #118's
`:1010-1027`. `Task.new`'s hunk (`:607-612` theirs) and our R5/R6 query edits (`__awaiting_retry`,
`increment_group`) do not overlap it. Prose: #118 inserts into `AGENTS.md` § the group-gating paragraph
(`@@ -260`) and `invariants.md` §3 after the group-gating bullet (`@@ -104`) — the same paragraphs R5/R6
must amend; expect adjacent-line conflicts there, resolved by keeping both.

- **#118 lands first (likely; maintainer approved merge on review-cycle-3).** Rebase; replace its loop
  body (`tag = dict(...)` through `retries[task.id] = retry`) with the `new_retry` comprehension; keep
  its comments' substance in `new_retry`'s docstring. Gate: its three retry tests plus ours green on
  both dialects; `submit_desc.rst`'s "a retry runs that same command in the task's group, with its tags"
  stays true; add "and its cores, memory and walltime" there (R9).
- **Ours lands first.** #118's hunk conflicts wholesale; resolution is to take ours (`new_retry`
  already satisfies F2 and G1) and keep #118's tests, which pass against it.

## Red-test candidates

1. **Model, SQLite** (`task_db` fixture as in `tests/test_retry_gate.py:35` / `test_zone_model.py`):
   `test_retry_keeps_declarations[eager]` — `Task.new(args='sleep 6', cores=2, memory=2**30, timeout=2,
   zone=gpu, tag={'k': 'v'})`, `Task.next(attempts=2)`, fail with `-2` (walltime SIGINT), next → retry
   (dedupe by id under eager, as `test_retry_keeps_zone` does); fresh read: `(cores, memory, timeout) ==
   (2, 2**30, 2)`, and an undeclared task's retry keeps `(None, None, None)` (not 0). Red today (probed).
2. **Model, PostgreSQL**: same body with the `pg_engine` fixture of `tests/test_zone_postgres.py:33-52`
   (`HS_TEST_POSTGRES`, `mark.integration`, skip when unset; run unsandboxed per the local PG setup).
   Next to `test_zone_filtered_selection`, which already pins "a retry keeps its zone" there.
3. **Fence**: `test_retry_columns_are_fixed`, modelled on `test_result_columns_are_fixed`
   (`tests/test_writeback.py:456`): `retry_columns`, `result_columns` and the 12 fresh columns are
   disjoint and cover `Task.columns`.
4. **Tag hazard**: a failed task carrying an `hs update` tag `cores:8` / `cores:x` → retry `cores` is the
   column value and the tag is kept (red today: `8`/`'x'`, tag dropped).
5. **End to end (R5-proof)**, pattern of #118's `test_cluster_retry_ignores_group_tag`: in `temp_site`,
   `hs submit -c 1 -m 1MB -W 2 -- sleep 6`, then `hs cluster --restart -r 1 -N1 --no-tls -H 127.0.0.1`
   (no `-W`, so only the column can kill; bounded `subprocess.run(timeout=)`, no `timeout(1)` on the
   host). Expect exit 0, two rows linked by `previous_id`, both `exit_status < 0` (killed, not
   `-1001/-1002`, so R5 still retries), retry `duration < 6` and `(cores, memory, timeout) == (1,
   1048576, 2)`. Red today: retry exit 0 after ~6 s with NULLs. A cheaper variant without sleeping:
   `hs cluster FILE -c 1 -m 1MB -r 1` over `false`, asserting the retry's columns. Do **not** use the
   GOAL's `-c 100000` probe: after R5 `-1002` is never retried.

## Risks

- `submit_time` must stay fresh on the retry (#118's `reached_group` retry branch).
- Direct construction skips `Task.new`'s `DEFAULT_ZONE_ID → NULL` normalisation; harmless (the stored
  zone is already NULL for the default), but say so in the docstring.
- With R7, a retry of a walltime-killed task is killed again, so `-W` runs with retries take longer to
  fail and spend each attempt; release note change 5 already says so.
- A legacy SQLite row whose `cores`/`memory` hold text (pre-#118 or `cores:x` today) is copied as is;
  SQLite accepts it, the client's `task.cores or 0` arithmetic would not. Pre-existing; not R7's.
