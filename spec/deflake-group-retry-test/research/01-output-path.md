# 01 — Task-output lifecycle: subprocess stdout → `hs cluster` stdout

Scope: `tests/test_groups.py::test_group_failed_task_with_retries` flake — the successful
retry's line (`"1"`) is missing from captured `hs cluster` stdout while a later line (`"2"`)
survives. Traced the full output path for a first-attempt task and a retry row.

## Verdict (root cause + confidence)

**(b) — this is an inherent property of HyperShell's best-effort console echo; the test is
asserting on a stream it must not treat as an ordered, complete, authoritative record.**
Confidence: high on the mechanism/classification; medium on the *exact* byte-loss trigger.

Default task stdout is **not captured and re-printed by any HyperShell thread**. Each task's
shell subprocess is spawned with `stdout=sys.stdout` (`client.py:671`, `client.py:772-774`), so
its `fileno()` (fd 1 of the `hs cluster` process — an OS pipe under the test's
`subprocess.run(..., stdout=PIPE)`, `tests/__init__.py:30`) is inherited and the child writes
**directly** to that fd. The parent never buffers, orders, flushes, or relays those bytes;
delivery is entirely a property of the OS pipe + each independent subprocess's libc/stdio flush.
This passthrough runs completely outside the control plane (DB + queues) that determines
success, so console stdout carries **no delivery guarantee tied to task completion**. The test's
line 143 assertion (`sorted(stdout.splitlines()) == ['0','1','2']`) scrapes that best-effort
stream and is the fragile part; the test's other assertions (row counts, group-transition log
lines, DB `id` lookups) are on authoritative state and are sound.

Important honesty note: for this *exact* invocation (default `-N1`, no `--capture`, normal
exit-0 on attempt 3) the **in-process code path cannot lose a single mid-stream line via the
pipe** — one executor writes `0\n`,`1\n`,`2\n` sequentially to one ordered FIFO that
`communicate()` drains to EOF. That strongly implies the actual byte loss is at the
OS/stdio/CI-runner boundary (see Open questions), which *reinforces* the verdict: the console is
outside HyperShell's guarantees, so the test should not depend on it.

## The output path, step by step

First-attempt task (e.g. `echo 0`):
1. Server `Scheduler` loads rows from DB and posts a bundle on `scheduled`
   (`server.py:206-257`); `Task.next` selects by group/attempts.
2. Client `ClientScheduler` pulls the bundle, pushes each `Task` to the local inbound queue
   (`client.py:206-261`).
3. `TaskExecutor.start_task` builds env (incl. `TASK_ATTEMPT`, `TASK_OUTPATH`) and spawns the
   subprocess: `Popen(self.task.command, shell=True, stdout=self.redirect_output,
   stderr=self.redirect_errors, ...)` (`client.py:756-774`). With `capture=False` (default),
   `self.redirect_output` is `sys.stdout` (`client.py:671`) — the child writes `0\n` straight to
   fd 1. **No copy is retained.**
4. `wait_task` blocks on `process.wait()`, records `exit_status`/`completion_time`/`duration`
   (`client.py:779-795`). Output bytes are neither read nor stored.
5. The completed `Task` (metadata only) goes outbound → `ClientCollector` bundles it →
   `completed` queue → server `Receiver.update_tasks` writes it back to the DB and logs
   `Completed task (…)`; on non-zero exit it logs `Non-zero exit status` and optionally writes
   `task.args` to `redirect_failures` (**args, not output**) (`server.py:456-466`).

`hs cluster` default routing: `ClusterApp.output_stream` = `sys.stdout` unless `-o PATH`
(`cluster/__init__.py:424-426`), threaded through `run_local` → `LocalCluster` → `ClientThread`
→ `TaskExecutor.redirect_output` (`cluster/__init__.py:292`, `cluster/local.py:241`,
`client.py:1232`). So default = **inherit the cluster's stdout fd**, never capture-then-reprint.

Retry row (attempt 3 of `n:1`, `[ $TASK_ATTEMPT -eq 3 ] && echo 1`): a retry is a *new row*
(attempt+1) scheduled by the server exactly like any other task; it flows through steps 1–5
**identically**. `TASK_ATTEMPT=3` comes from the row's `attempt` field via `task_env`
(`client.py:439-460`), so the shell runs `echo 1`, writes `1\n` to fd 1, exits 0. There is **no
distinct retry code path** for output — same `Popen(stdout=sys.stdout)`.

Why a retry's line is *more exposed* nonetheless: it is produced late, only after two failed
attempts plus the server's group-gating backoff (`server.py:224-241`), i.e. right before the
`Completed task group 0 - starting task group 1` transition. It therefore lands nearest the
group boundary and the eventual shutdown drain — the window where any flush/ordering weakness is
most likely to bite. (Note it is still *before* `echo 2`, so tail-truncation is excluded, as the
symptom confirms.)

## Where a line can be dropped (hazard enumeration)

1. **Pure fd inheritance, zero relay** (`client.py:671,772-774`): the parent has no buffer,
   flush, or ordering control over task bytes; they exist only if the OS pipe delivers them.
2. **Multiple unsynchronized writers**: with `-N>1` or multiple clients, N independent shell
   processes share one stdout fd with no app-level lock. Writes ≤ `PIPE_BUF` (64 KiB) are atomic
   but inter-process order is arbitrary; larger writes interleave. (Not the direct trigger here:
   this test is `-N1` and lines are sequential.)
3. **Subprocess killed before stdio flush**: on timeout/shutdown the executor escalates
   SIGINT→SIGTERM→SIGKILL (`client.py:865-884`); a signal-killed child loses buffered stdout.
   (Not this case — attempt 3 exits 0 normally, which flushes.)
4. **Pipe = block-buffered (not a TTY)**: under `subprocess.run(stdout=PIPE)` the child's stdout
   is block-buffered; short lines only reach the pipe at child exit. Reliable for normal exit,
   fragile for abnormal termination.
5. **Broken-pipe fd redirect** (`core/exceptions.py:99-114`): `handle_broken_pipe` does
   `os.dup2(devnull, sys.stdout.fileno())`, silently discarding *subsequent* stdout. Only fires
   on `BrokenPipeError` (early reader close, e.g. `hsx | head`); `communicate()` reads to EOF, so
   not triggered here — but it would drop *later*, not *earlier*, lines.
6. **Shutdown/exit race**: if the cluster process closed the pipe write end before a lagging
   child flushed. Guarded here by `process.wait()` before the executor advances, so not the
   in-process cause.

None of 1–6 cleanly drops an *earlier* line while keeping a *later* one under `-N1` + normal
exit — hence the environmental suspicion below.

## Authoritative record of task output (for test hardening)

- **Success/timing/exit are authoritative in the DB.** `Receiver` persists the `Task` row via
  `Task.update_all(... to_dict())` (`server.py:456-459`): `exit_status`, `completion_time`,
  `duration`, `attempt`, etc. Assert on these (e.g. `hs list`, `hs info ID`), not console text.
- **Task output *text* is NOT stored by default.** Without `--capture`/`-o`, stdout is *only*
  echoed to the console fd and kept nowhere. It is persisted to a file **only** under
  `--capture`: `task.outpath = {default_path.lib}/task/{id}.out`, opened per-task and recorded on
  the row (`client.py:453,757-761`; dir created in `core/platform.py:119`). `hs info ID --stdout`
  reads `task.outpath` (`task.py:195-231,255`). So a robust content assertion requires
  `hsx --capture …` then reading the `.out` file or `hs info ID --stdout` — a real, flushed,
  per-task file rather than a shared console pipe.

Recommended hardening: keep the DB/row-count/group-order assertions; replace the
`stdout.splitlines() == ['0','1','2']` scrape with either (a) DB-only assertions (already largely
present), or (b) a `--capture` run asserting per-task `.out` contents.

## Open questions

- **Exact byte-loss trigger under CI.** The `-N1`, exit-0 in-process path can't drop a
  mid-stream pipe line; the loss likely originates at the OS/stdio/CI-runner or test-harness
  layer (e.g. runner fd handling, or pytest-xdist worker effects). `main()` PIPEs the child's
  stdout directly to the test process (`tests/__init__.py:30`), so pytest's own capture should
  *not* touch it — worth confirming under `-n auto`.
- Does any CI wrapper wrap/replace the child's stdout with something non-fd-backed (which would
  send `Popen` down a different handle path)? Not observed in-repo.
- Whether raising executor concurrency or bundle timing on the loaded runner changes ordering
  enough to expose hazard #2 even at nominal `-N1` (e.g. leftover subprocesses). Believed no.
