# Research 02 — Test Harness & Robust-Assertion Menu

Scope: what `tests/` gives a rebuilt `test_group_failed_task_with_retries`, and the
authoritative (non-stdout-scraping) signals it can assert on.

## How `main()` captures output — the root of the flake

`tests/__init__.py:28` `main(argv)` runs the CLI as a **real subprocess**:

```python
proc = run(argv, stdout=PIPE, stderr=PIPE, env=os.environ)   # tests/__init__.py:30
return (proc.returncode, proc.stdout.decode().strip(), proc.stderr.decode().strip())
```

- **Not** `redirect_stdout`, **not** `capsys`, **not** in-process. `hs cluster` is a
  separate process; server + client threads live inside it and inherit its stdout.
- By default the client runs tasks with **`capture=False`**, so `redirect_output =
  sys.stdout` (`client.py:671`) and every task subprocess's stdout is wired **directly to
  the cluster process's stdout fd** (`Popen(..., stdout=self.redirect_output ...)`,
  `client.py:773`). With `-N>1` many task subprocesses share that one fd concurrently.
- So the captured `stdout` is a **concurrent merge of N task subprocess fds**. A missing
  line ("1") is a lost/interleaved write on that shared fd under a race — the buffer is
  inherently non-deterministic and not a reliable oracle. This is exactly the flake.
- `main_lines` (`:38`) just splits/str ips `main()`'s output; `assert_output(pattern,
  output, count, groups)` (`:48`) regex-counts matching **lines** (`n == count`);
  `NO_OUTPUT = ['']` (`:25`) is the empty-stderr sentinel; `create_taskfile` (`:59`) /
  `create_taskfile_echo` (`:68`, writes `echo {n} # HYPERSHELL: ... n:{n}`) build inputs.

**Implication for the rebuild:** stop asserting on merged cluster stdout for per-task
content; assert on DB rows written back by the server (authoritative, single-writer).

## Fixtures (`tests/conftest.py`)

- `isolate_environment()` (`:23`) runs at **import** (`:39`) and via the autouse
  `clean_env` fixture (`:42`) before **every** test: scrubs all `HYPERSHELL_*`, sets
  `HYPERSHELL_CONFIG_FILE=''` and `HYPERSHELL_SERVER_PORT=free_port()` (`:16` binds an
  ephemeral loopback port — no cross-test collisions).
- `temp_site` (`:48`) makes a tmp dir and sets `HYPERSHELL_SITE`,
  `HYPERSHELL_DATABASE_FILE=<site>/local.db`, `HYPERSHELL_LOGGING_LEVEL=DEBUG`. The DB is a
  per-test SQLite file; `hs list`/`hs info` in the same test see the rows the cluster wrote.
- DEBUG logging is why the existing assertions can grep `DEBUG [hypershell.server]
  Completed task (...)` from stderr (stderr carries logs; stdout carries task output).

## Robust-signal menu (assert on these, not stdout)

Task columns encoding outcome (`data/model.py:256-336`): `exit_status` (int, nullable),
`attempt` (int), `retried` (bool), `group` (int), `previous_id`/`next_id` (UNIQUE retry
chain), `completion_time`, `outpath`, `tag` (JSON; carries `n:` labels). Retries are **new
rows** copying the parent `tag`, `group`, `fingerprint`, `source` (`model.py:620-627`), so
**every row in n:1's retry chain still matches `-t n:1`** (3 rows here).

`hs list` interface (`task.py:552-673`): positional FIELD names (any of `Task.columns`),
`-t/--with-tag`, `-g/--group`, `-c/--count`, `-s/--order-by FIELD [--desc]`,
`-f plain|csv|json|table|normal`, and status aliases `-F/--failed` (`exit_status!=0`),
`-S/--succeeded` (`==0`), `-C/--completed` (`!=null`), `-R/--remaining` (`==null`),
`-X/--cancelled` (`==-1`), `--retries` (`attempt>1`), `--signal NAME`, `-w COND`.

| Assertion | Proves |
|---|---|
| `main_lines(['hs','list','-c'])` == `['5']` | 2 base tasks + 3 rows for n:1 (attempts) |
| `main_lines(['hs','list','-c','-t','n:1'])` == `['3']` | n:1 ran exactly 3 attempts (chain length) |
| `main_lines(['hs','list','exit_status','-t','n:1','-s','attempt'])` == `['1','...','0']`? see note | per-attempt outcome ordered by attempt |
| `main_lines(['hs','list','exit_status','-t','n:1','-S'])` == `['0']` | exactly one attempt of n:1 succeeded |
| `main_lines(['hs','list','attempt','-t','n:1','-S'])` == `['3']` | the success was the 3rd attempt |
| `main_lines(['hs','list','-c','-t','n:1','-F'])` == `['2']` | 2 failed attempts (matches the 2 "Non-zero exit status" logs) |
| `main_lines(['hs','list','retried','-t','n:1','-s','attempt'])` first two `true` | failed rows are chained/retired |
| `main_lines(['hs','list','exit_status','-t','n:2','-S'])` == `['0']` | group-1 task n:2 completed successfully |
| `main_lines(['hs','list','group','-t','n:2'])` == `['1']` | n:2 is in group 1 (ran after group 0 drained) |
| `main_lines(['hs','list','-c','-R'])` == `['0']` | nothing left unscheduled/incomplete |

Note: the exact `exit_status` of the two failed attempts is the shell's `[ ... ] && echo`
exit (1), but assert `!=0` via `-F`/`--failed` rather than a literal to stay robust.
`-s/--order-by` maps straight to `ORDER BY <col>` (`task.py:504-511`); plain format joins
fields by tab. Ordering the *group-transition-before-n:2* check can also be recast: n:2's
`schedule_time` > every group-0 row's `completion_time` (all authoritative timestamps),
instead of grepping log-line indices.

## Output-content question ("did the 3rd attempt actually emit `1`?")

By default there is **no authoritative record of task stdout content**. With
`capture=False`, output streams to the shared process fd and **`outpath` stays NULL** (only
set when capturing — `client.py:757-760`). So "1" exists *only* in the flaky merged buffer.

To make content authoritative, run the cluster with **`--capture`**: each task's stdout goes
to `<site>/lib/task/<id>.out` and the row's `outpath` is populated (`client.py:757`,
`task.py:274`). Then verify via `hs info <id> --stdout` (`task.py:196,233` reads `outpath`;
same-host so no SFTP — `client_host == HOSTNAME`, `task.py:238`). Flow: get the succeeding
row's id with `hs list id -t n:1 -S`, then `hs info <id> --stdout` == `'1'`. This is the
only robust way to assert the *content* `1` was produced by the successful attempt.
(`hs list outpath -t n:1 -S` also confirms the file exists.) If content-proof is
out of scope, `... -S` giving `exit_status==0` on `attempt==3` already proves the winning
attempt ran to success — the command's own `[ $TASK_ATTEMPT -eq 3 ]` guard means exit 0 is
only reachable on attempt 3, i.e. the echo path.

## Exemplar robust assertions in the tree

- `tests/test_cancel.py:43` `assert main_lines(['hs','list','exit_status','-t','n:1'])[1]
  == ['-1']` and `:99` `... 'attempt','-t','n:2'])[1] == ['1']` (asserts no retry copy).
- `tests/test_cluster.py:183` `assert main_lines(['hs','list','exit_status','-f','plain',
  '-s','submit_time']) == (exit_status.success, ['0']*4, NO_OUTPUT)` — status per task from DB.
- `tests/test_update.py:38-40` `main_lines(['hs','list','exit_status','-t','n:0'])[1] ==
  ['null']` / `['-15']` / `['0']` — per-tag exit-status probes.
- Same file already uses `main_lines(['hs','list','-c']) == (success, ['5'], NO_OUTPUT)`
  (`test_groups.py:146`) — the count check is the one robust assertion it already had.

## Open questions

1. Is `--capture` acceptable in this test (it changes the run path slightly and writes
   per-task files under `temp_site`)? Needed only if content-of-`1` must be proven.
2. Does the plan want to keep any stderr log-count assertions (`DEBUG ... Completed task`,
   `Non-zero exit status` x2, group-transition x1)? Those passed in CI and read from the
   single-writer log stream, so they are far less flaky than task stdout — reasonable to keep.
3. With `-r 3`, confirm the intended chain length is 3 rows for n:1 (attempts 1,2,3);
   `attempts == max_retries + 1` semantics (AGENTS.md) suggest `-r 3` allows up to 4 rows,
   but the command succeeds on attempt 3 so only 3 are created — assert `<=` if brittle.
