# 01 — Client bundling (R1, R2, R6)

Read-only survey of the worktree at the cycle's base. Citations are `file:line` in this worktree.

## 1. How the collector returns a bundle today

- Pipeline (`ClientThread.__init__`, `src/hypershell/client.py:1295-1317`): scheduler → `inbound`
  `Queue(maxsize=bundlesize)` (:1304) → N `TaskExecutor`s → `outbound` `Queue(maxsize=bundlesize)` (:1305)
  → `ClientCollector` → `queue.completed`. Collector `bundlesize` is the `-b` value, or
  `DEFAULT_BUNDLESIZE` (1) when unset (:1295).
- `get_local` (:391-402) blocks on `outbound.get(timeout=DEFAULT_LOCAL_TIMEOUT)`; that timeout is
  **1 s** (`core/queue.py:1117`). So the poll period is 1 s when no task arrives, immediate when one does.
- `check_bundle` (:404-415): PACK when `len(tasks) >= bundlesize` (size), else when
  `now - previous_send >= bundlewait` (wait, `trace 'Bundle wait exceeded'`), else back to GET_LOCAL.
  `previous_send` is set at START (:388) and after each successful put (:430).
- Shutdown: `None` sentinel on `outbound` (`wait_collector`, :1359-1363) → FINAL → `finalize`
  (:437-446) loops `pack_bundle`/`put_remote` while `tasks` is non-empty.
- Pre-existing quirk: with no tasks, the wait branch still packs `serialize_tasks([])` = `b'[]'`
  (`core/types.py:78-80`), which is truthy, so `put_remote` (:425) sends an **empty bundle every
  `bundlewait`**. Leave it (R2 "as today"), but the new trigger must not fire on an empty `tasks`.

## 2. State the collector can read for "nothing running, nothing waiting"

Candidates and why they fall short:
- `inbound.empty()` / `outbound.empty()`: miss the scheduler's fetched-not-queued list
  (`ClientScheduler.tasks`, `pop_task`/`put_local` :266-281) and an executor that has done
  `inbound.get` + `task_done()` (:771-772) but whose FSM state still reads GET_LOCAL.
  `Queue.unfinished_tasks` is useless: executors call `task_done()` the moment they get (:772).
- Executor FSM state (`machine.state`): set only after the action returns (`core/fsm.py:74`), so
  a task in hand is invisible for one step; also N reads, not atomic.
- Resource accounting (`client.py:579-650`, `tasks_active`/`tasks_pending`, `executor_lock`):
  only covers tasks between `acquire` and `release`, misses inbound/outbound/scheduler-held tasks
  and template/resource-error tasks (never acquire, §7), and is process-global (§7 one ClientThread
  per process) — do not lean on it.

**Narrowest reliable signal: an in-flight count** — tasks the client has received from the server
and the collector has not yet taken. It covers scheduler-held, queued, starting (resource wait in
`start_task` :801-813), running, rate-limited (`wait_ratelimit` :946-962) and on-`outbound` tasks.

**Proposed design (small, `client.py` only):**
- A tiny `TaskCount` (an `int` under a `threading.Lock`: `add(n)`, `sub(n)`, `value`) created in
  `ClientThread.__init__` and passed to `ClientScheduler(…, inflight=…)` and
  `ClientCollector(…, inflight=…)` (both optional, default `None` = today's behaviour, so standalone
  machines and existing tests are untouched).
- Scheduler: `inflight.add(len(reply['tasks']))` in `request_bundle` the moment a non-empty reply
  arrives (:233-236, before UNPACK) — the narrowest point.
- Collector: `inflight.sub(1)` in `get_local` when it takes a real task (:396-398). Because the
  collector itself decrements, its read right after appending the last task already sees 0: no
  1 s lag, no cross-thread race on its own decrement.
- `check_bundle`: after the size test, add `elif self.tasks and inflight is not None and
  inflight.value == 0:` → `log.trace('Client idle …')` → PACK_BUNDLE. Mirrors the relay returner's
  `idle` early return (`relay/upstream.py:841-842`, `:848-854`).
- Races, all benign: (a) the server has staged tasks for this client but its reply is still in
  flight → collector may return a partial bundle a moment early (R1 is about what the client holds;
  Clarification 2 accepts mid-run returns); (b) an executor that gives up on an unkillable task
  (`wait_signal` → FINAL, :921-924) or a crashed executor never hands its task over → count stays
  > 0 → falls back to today's wait (no regression); (c) `ClientThread.stop` mid-bundle: same
  fallback; `finalize` still flushes everything.
- §5: adds no blocking op (lock is a short critical section); every queue op keeps its finite
  timeout. §6: sentinel order and `finalize` unchanged. §7: does not touch `resource.py`/accounting.
  `client.py` is high-blast-radius → human gate at review.

## 3. R2 stays as today

While `inflight > 0` the new branch is skipped, so `check_bundle` reduces to the existing size and
wait tests exactly; `previous_send` bookkeeping and `finalize` are untouched. Keep the size test
first so a full bundle still logs `Bundle size reached`. Guard on `self.tasks` so an idle client does
not send `b'[]'` every second (only the pre-existing per-`bundlewait` empty send remains).

## 4. Tests

Existing: no test drives `ClientCollector` or client-side bundling. Hits for bundlewait are option
parsing (`tests/test_duration_options.py:179-359`), launch-line assembly
(`tests/test_cluster_launch.py:480-1234`, `tests/test_relay_launch.py:585`), the relay returner
(`tests/test_relay_return.py:354`, `tests/test_relay_fsm.py:290-716`) and `LocalCluster` runs with
`bundlesize=1` (`tests/test_stream_input.py:302-453`). Closest pattern:
`tests/test_client_request.py:37-62` (`FakeDispatcher`, `SimpleNamespace` queue, monkeypatched
`hypershell.client.DEFAULT_REMOTE_TIMEOUT`).

**R1 test (fails on the base, uses only existing public API)** — new `tests/test_client_return.py`,
`@mark.integration`:
- Monkeypatch `hypershell.client.QueueClient` with a `FakeQueueClient` class (`__enter__` returns
  self, `__exit__` no-op; attributes `dispatcher`, `confirmed`, `completed`, `heartbeat`, each a
  plain `Queue` except the dispatcher). Its dispatcher (as `FakeDispatcher`) replies once with three
  task records (`Task.new(args='true').to_json()`), then `{'protocol': 1, 'tasks': [], 'shutdown':
  False}` after a 0.05 s sleep (as a server bounds its wait), and `'shutdown': True` once a
  `threading.Event` `done` is set. This is the mid-run case (client waiting on the server).
- `ClientThread(num_threads=2, bundlesize=8, bundlewait=60, template='{}', address=('127.0.0.1', 1),
  auth='test', redirect_output=<tmp file>)`; `start()`.
- Read `fake.completed.get(timeout=0.5)` in a loop until 3 tasks have come back or a 10 s deadline;
  skip `b'[]'`; stamp arrival with `datetime.now().astimezone()`.
- Assert all 3 returned, and `arrival - max(task.completion_time) <= 1 s` (R1's bound, measured
  from the tasks' own `completion_time`).
- `finally`: `done.set()`; `client.join(timeout=15)`; `assert not client.is_alive()`.
- Runtime bound on the unfixed base: nothing returns before 60 s, so the 10 s deadline fails the
  assertion; `done` then sends `shutdown`, the scheduler finishes, sentinels flow, and `finalize`
  flushes — the thread ends within a few seconds (join bounded at 15 s). No pytest-timeout plugin is
  configured (`pyproject.toml:142-147`), so the test bounds itself.
- Note §7: `set_resources` is reset per `ClientThread` and the tasks ask for no cores/memory;
  `LocalCluster` already runs an in-process `ClientThread` in tests.

**R2 unit tests** (`@mark.unit`, same file), driving `ClientCollector` action by action on
`SimpleNamespace(completed=Queue())` with `DEFAULT_LOCAL_TIMEOUT` monkeypatched small:
`bundlesize=4, bundlewait=3600`, `inflight` at 2 after one task taken → `check_bundle` is GET_LOCAL
(held); four tasks → PACK by size; `bundlewait=0` → PACK by wait; `inflight=None` → today's
behaviour; `finalize` with tasks held → all returned. Also a scheduler unit test in
`test_client_request.py` style: a non-empty reply adds its length to `inflight`.

## 5. Other consumers

- Every client runs `ClientThread`: `hs client` (`ClientApp.run` :1668-1680 → `run_client`),
  `hs cluster` local mode in-process (`cluster/local.py:239`), and all launched clients
  (`cluster/launch.py:509-511` builds `-N … -b … -w …`). The fix lands in all of them with no launch
  change. Relay site clients are launched processes with `-b max(relay.bundlesize, relay.num_threads)`
  (`relay/__init__.py:318-325`, `:527`, `:538-547`; `relay/site.py:133-150`).
- Relay cadence: `UpstreamReturner.collect` (`relay/upstream.py:841-845`) already sends at once when
  `site_idle()` (`store.site_idle`, wired at `relay/__init__.py:613-615`) and otherwise wakes every
  `min(poll, bundlewait - waited)` with `relay.poll` 0.1 s. Its 5 s cadence persists only because the
  site client holds results, leaving unfinished rows; R1 removes that hold, so no separate relay change
  is needed for (a).
- Aside (pre-existing, out of scope): `-w` defaults to `config.submit.bundlewait`, not
  `client.bundlewait` (`client.py:1605`, `cluster/__init__.py:153`); `client.bundlewait` feeds only
  `DEFAULT_BUNDLEWAIT` (:318) and the help text. Do not fix here; don't let R6's text imply otherwise.

## 6. Docs for R6

- Help strings: `client.py:1556` and `cluster/__init__.py:87` (`-w … Wait before flushing tasks`);
  short help, add at most "(sooner when idle)". Also docstrings `client.py:1181-1183`, `:1421-1423`.
- `docs/_include/client_help.rst:34-46` (client `-w` paragraph at :42-44) and
  `docs/_include/cluster_help.rst:101-113` (:109-111): add "A client with nothing left to run returns
  what it holds at once." These are what `docs/manual.rst:73`, `:101` include.
- `docs/_include/config_param_ref.rst:413-422` (`client.bundlewait`) and `:765-776`
  (`relay.bundlewait`; already says the relay returns at once when its site is idle — add that its
  site clients do too). `server.poll` (:284) is R3's line.
- Version notes: follow `docs/cli/client.rst:21-33` / `docs/cli/cluster.rst:22-56`
  (`.. versionchanged:: 2.9.0` + one sentence, `:pr:` when known) and in the param ref the
  `.. only:: html` + indented `.. versionchanged:: 2.9.0` form (`config_param_ref.rst:491-506`, `:775`).
- Man pages `share/man/man1/{hs,hsx,hyper-shell}.1` are Sphinx output (`docs/conf.py:161-172`);
  client `-w` at `:929-936`, cluster `-w` at `:224-231` (server `:687`, submit `:1385`). History
  shows they change only in `[release]` "rebuild man pages" commits (`/hs-release`, `AGENTS.md:687-688`).
  Recommend: edit the `.rst` sources; the plan states that the man pages pick it up at the next
  release rebuild, unless the foreman wants them regenerated in-cycle for R6.
- Wording must name top-level commands only (`hs client`, `hs cluster`, `hs relay`).
