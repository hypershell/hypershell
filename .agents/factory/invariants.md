# Invariant gate & footgun checklist

A curated, explicitly-enumerated subset of the load-bearing invariants in
[`AGENTS.md`](../../AGENTS.md), maintained **in lockstep with it** (`AGENTS.md` is ground truth — if
this drifts, fix it). Two consumers:

- **`hs-plan` (gate):** before research *and* after PLAN/TECH is drafted, walk the sections a change
  touches and confirm the design honors each. Record any bend in PLAN's deviation-justification
  table.
- **`hs-review` (footgun list):** a violation of any invariant here is **auto-CRITICAL** and, when it
  touches the high-blast-radius core, forces a human sign-off gate.

Only invoke the sections relevant to the change — do not manufacture findings against untouched
subsystems.

## High-blast-radius files (any CONFIRMED finding here → mandatory human gate)

`data/model.py` · `server.py` · `client.py` · `core/queue.py` · `core/tls.py` · `core/fsm.py` ·
`core/thread.py` · `core/signal.py` · `cluster/remote.py` · `cluster/ssh.py`

---

## 1. Task lifecycle (`data/model.py`) — highest blast radius

- **No status enum.** State is a function of nullable columns:
  - unscheduled: `schedule_time IS NULL`
  - in-flight/interrupted: `schedule_time` set, `completion_time IS NULL`
  - done (terminal): `completion_time` set
  - cancelled (terminal): `exit_status == CANCEL_STATUS` (`-1`) **and** `completion_time` set
- Every new query must reproduce these predicates exactly.
- Setting `schedule_time` without eventually setting `completion_time` creates a task
  `revert_interrupted()` re-runs on next server start.
- **Task-state transition logic belongs in `Task` classmethods**, not scattered into FSM driver
  files.

## 2. `exit_status` overloading

| Value | Meaning |
|-------|---------|
| `0` | success |
| `> 0` | real process exit code |
| `-1 .. -64` | killed by signal N (`-N`); **`-1` = `CANCEL_STATUS`** |
| `-1001` | `TASK_TEMPLATE_ERROR` (never ran) |
| `-1002` | `TASK_RESOURCE_ERROR` (never ran) |

- New "never ran" sentinels go **below `-1000`**. **Never** use `-1..-64` (reserved for signals).
- **Every failure/retry/count-as-failed query MUST filter `exit_status != 0 AND exit_status !=
  CANCEL_STATUS`** — or cancelled tasks get resurrected.

## 3. Retry model

- Retries are **new rows** (`attempt+1`, `previous_id`/`next_id` **UNIQUE** chain, old row
  `retried=True`) — never mutate a row in place to retry.
- `attempts == max_retries + 1`; this relationship spans `server.py`, `Task.select_failed`, and
  `Task.increment_group` — change them together.
- Group-gating can stall **by design** (a permanently-failing task halts the group); don't "fix" it
  without understanding it.

## 4. Server modes — guard before assuming DB behavior

- **`in_memory`**: `Scheduler` is `None`; the server skips all DB writeback, retries, revert, and
  group-gating. Guard every server-side DB write with `if not self.in_memory:`.
- **`no_confirm`**: disables orphan recovery (client id never stamped) — a dead client's tasks are
  **not** rescheduled.

## 5. Concurrency (FSM + Thread) — untested, edit with care

- `StateMachine` subclasses need a `HALT` state; every non-HALT state returns the next state and has
  an `actions` entry.
- `Thread.stop()` overrides **must** call `self.machine.halt()` before `super().stop()` (the base
  `__should_halt` flag does not stop an FSM).
- `fsm.py` does **not** poll signals — polling is manual via `check_signal()` in scheduler action
  methods. A new state observes signals only if you add the poll.
- Every blocking queue op inside a state uses a **finite timeout** and **re-enters** its state
  (that timeout is the shutdown-latency bound).
- Shut down with `stop(wait=True)` so `join()` re-raises captured thread exceptions.

## 6. Shutdown / sentinel ordering — load-bearing

- Preserve exact ordering. Client: scheduler → executors → collector → heartbeat. Server: submitter
  → scheduler → sentinels to heartbeat/receiver/confirm. Submit flush: `loader.join()` **then**
  `queue.put(None)`.
- Stream sentinel is `make_sentinel()` (`serialize(None)`).
- **Remote-queue payloads** go through `serialize_tasks`/`deserialize_tasks` and `heartbeat.pack`
  (JSON) — **never** put a live object or a literal `None` on a remote queue.

## 7. Resource accounting (`core/resource.py`)

- Process-global mutable state under `executor_lock`; assumes **one `ClientThread` per process**.
- `acquire`/`release` must be balanced. Tasks that early-exit (template/resource error) never
  acquire → must never release.

## 8. Signals

- `register_handlers()` handles **`SIGUSR1`, `SIGUSR2`, `SIGHUP` only** (no-op on Windows).
- `SIGINT`/`SIGTERM` are **deliberately not captured** (Ctrl-C → `KeyboardInterrupt` via cmdkit).
- `signal.RECEIVED` is a process-global **sticky** flag; USR1/USR2 are never reset. **Do not add
  stray `reset_signal()` calls.**

## 9. Queue transport & security

- **TLS is ON by default**; disable only via `--no-tls` (internally `tls=None`). There is **no
  `cipher.py`**.
- **Auth is ALWAYS required.** Refuse the placeholder key `DEFAULT_AUTH = '<not-secure>'`; enforce
  `AUTH_MINIMUM_LENGTH` (≥16) and `AUTH_ALLOWED_CHARS`. Never weaken; never drop the authkey to
  "disable TLS" (pass `tls=None`).
- Multiprocessing RPC framing is **pickle** (an RCE surface); HyperShell **payloads are JSON**. Both
  facts matter — don't conflate them.
- **No mTLS** (`verify_mode=CERT_NONE`); keep the post-handshake fingerprint check.
- `'<auto>'` TLS materials only work single-host / shared FS; cert/key are **not** forwarded to
  launched clients (only `--no-tls` is propagated).

## 10. Configuration

- `config` is built **once at import** — an effectively immutable per-process singleton. `hs config
  set` writes disk but does **not** mutate the live singleton (needs restart).
- Sentinels: `'<auto>'` (materialize), `'<none>'` (unset). The `X or None` idiom means **`0` =
  unlimited/auto** for many numeric knobs.
- `data/core.py` creates engine + `Session` at import and can `sys.exit(3)` on bad config; SQLite
  uses `check_same_thread=False`.

## 11. Cluster orchestration

- No shared client-argv builder — a change to launched-client arguments must be replicated across
  `local`/`remote`/`ssh`/`autoscale`.
- In JSON mode, clients must be sent `DEFAULT_TEMPLATE` (never the user template) to avoid double
  expansion.
- Advertised `HOSTNAME` must be routable from clients. The autoscaler never terminates clients
  (they self-exit on idle) and ignores `no_confirm`/`in_memory`/`forever`/`restart`.
- `SSHCluster` shells out to the plain `ssh` binary (not paramiko; paramiko is only in
  `core/remote.py` for SFTP).
- `submit.py` queue-mode silently ignores `-t/--tag` (asymmetric vs DB mode).

## 12. Project conventions (same-commit rules)

- **Version is single-sourced from `pyproject.toml`** — never hardcode elsewhere.
- A CLI/feature change updates the affected `docs/_include/*.rst` help snippets **and** the `share/`
  completions **in the same commit**.
- The wheel ships `share/`; a CI metadata job asserts those paths — keep `share/` and the CI list in
  lockstep.
- Supported Python is **3.11–3.14**; do not reintroduce 3.9/3.10 shims.
- Dependency floors are pinned low for EPEL/RHEL parity — do not raise casually.
- Tests: only `@mark.unit` / `@mark.integration` are real (`--strict-markers`); use pytest's
  `@mark.parametrize`, not the `parameterize` placeholder. Tag every new test.
- Reuse `cmdkit.app.exit_status` constants for return codes — don't invent integer literals.
- Prefer idiomatic/concise Python but verify real equivalence (e.g. `dict.get(k, default)` evaluates
  `default` eagerly).
