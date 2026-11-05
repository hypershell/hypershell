# AGENTS.md

Guidance for coding agents (Claude Code and others) working in this repository.
`CLAUDE.md` is a symlink to this file — edit `AGENTS.md`, never a separate copy. (`.claude`
is likewise a symlink to `.agents`, so Claude Code discovers the factory skills and settings
through it.)

This document is the operating manual: the architecture, the load-bearing invariants, and
the process rules an autonomous agent needs to make correct, safe changes here without
rediscovering them each session. When something below disagrees with the code, **the code is
ground truth — fix this file.**

---

## Project

HyperShell is a distributed, high-throughput many-task engine for HPC / embarrassingly
parallel workloads. You give it a flat list of **shell commands**; a server pulls pending
tasks from a SQL database, bundles them, and pushes them over an encrypted TCP queue to
elastic clients that spawn subprocesses and return results. State lives in the database
(SQLite or PostgreSQL). There is **no DAG engine and no required Python API** — that is a
deliberate design choice (see `docs/landscape.rst` for how HyperShell relates to GNU
Parallel, HyperQueue, Balsam, HTCondor/Pegasus, etc.).

Console entry points (`pyproject.toml [project.scripts]`): **`hs`** → `hypershell:main`,
**`hsx`** → `hypershell:main_x` (prepends `cluster`, i.e. `hsx` == `hs cluster`), and legacy
**`hyper-shell`** → `hypershell:main` (keep all three).

## Environment & working rules

- **`del`, not `rm`** — `rm` is blocked in this environment; use `del <path>` (reversible
  trash; directories work with no flags).
- **Commit only when explicitly asked.** When you do: branch off **`develop`** (this is a
  git-flow repo: `develop` is the working branch, `master` is release). Never commit straight
  to `master`.
- **Squash-only merges** (`gh pr merge --squash`; merge-commit and rebase are disabled,
  branch auto-deletes). Commit subjects follow `[category] Imperative summary`. Common
  categories (see `git log`): `feature`, `fix`, `docs`, `ci`, `refactor`, `test`, `release`
  (version bumps or rebuilt assets like man pages), and `harness` (the `.agents/` factory).
  This set is **not closed** — coin a new lowercase category when one genuinely fits. **Do not
  add a `Co-Authored-By:` trailer** — a deliberate repo convention (we don't want the authoring
  model recorded on every line of the commit log). End PR **bodies** with the Claude Code
  generation line.
- **Version is single-sourced from `pyproject.toml`** (`hypershell.__version__` reads it).
  Never hardcode a version elsewhere.
- A feature/CLI change is expected to update, in the **same commit**, the affected
  `docs/_include/*.rst` help snippets and the shell completions under `share/`.

## Commands

Dependency management is `uv` (`uv.lock`, `[tool.uv]`, `dev` + `docs` groups sync by default).

```bash
uv sync                                  # install all deps (dev + docs)
uv run hs --help                         # main CLI
uv run hsx --help                        # alias for `hs cluster`

uv run pytest -v                         # full suite
uv run pytest -v tests/test_server.py    # one file
uv run pytest -v -k "pattern"            # by name
uv run pytest -v -m unit                 # marker: unit | integration (see below)
uv run pytest -v -n auto                 # parallel (pytest-xdist)

uv run sphinx-build docs docs/_build     # build docs (Furo/RST)
```

**Supported Python is 3.11–3.14** (`requires-python = ">=3.11"`; CI matrix + classifiers are
3.11–3.14). 3.9/3.10 are **not** supported — do not reintroduce compatibility shims for them.

## Repository map

Top-level package `src/hypershell/`:

| Path | Responsibility |
|------|----------------|
| `__init__.py` | `HyperShellApp` (cmdkit `ApplicationGroup`) + the `commands` dict that wires every subcommand; `main()`/`main_x()`. **All CLI wiring lives here — there is no `cli/` package.** |
| `submit.py` | `SubmitApp` / `SubmitThread` / `LiveSubmitThread` — ingest commands from stdin/files, assign UUIDs, parse `# HYPERSHELL:` tags, write task rows. Also the source-gating for safe re-submission (`--restart`/`--repeat`/`--update`): `GatedSource`, `source_fingerprint_and_count`, `apply_source_gate`. |
| `server.py` | `ServerThread`, the `Scheduler` FSM (load/pack/post bundles), and `HeartMonitor` (client eviction + task revert). |
| `client.py` | `ClientThread`, `ClientScheduler` + `TaskExecutor` FSMs — pull bundles, run subprocesses, emit heartbeats, return results. |
| `task.py` | The top-level `info`/`wait`/`run`/`list`/`update` apps, incl. exit-status-colorized `hs list`, and the legacy `task` group (kept until v3). |
| `core/` | Shared infrastructure (see below). |
| `data/core.py` | SQLAlchemy engine/`Session`, DB-URL construction, provider selection. `InitDBApp` (`hs initdb`) and the `initdb`/`checkdb`/`ensuredb` helpers live in `data/__init__.py`; the schema-upgrade step in `data/upgrade.py`. |
| `data/model.py` | `Entity` base + `Task`, `Client`, and `Source` ORM models (`Source` records submission provenance for re-submission gating; `DIRECT_SOURCE_ID`/`STDIN_SOURCE_ID` are reserved source ids). **Most query/state logic lives as `Task` classmethods here.** |
| `cluster/` | `__init__.py` (`ClusterApp`/`hsx`), `local.py` (`LocalCluster`), `remote.py` (`RemoteCluster`, `AutoScalingCluster`), `ssh.py` (`SSHCluster`). |

`core/` modules: `sys` (import-time `sys.path` sanitizer), `config`, `logging`, `signal`,
`exceptions`, `fsm` (`StateMachine`), `thread` (`Thread` base), `queue` (transport +
TLS-aware manager), `tls` (`TLSConfig`, cert generation), `heartbeat`, `template`, `tag`,
`types` (JSON `serialize`/`deserialize`), `resource` (per-node resource accounting),
`remote` (paramiko SFTP for task-output retrieval), `platform` (site paths), `pretty_print`,
`uuid`. **There is no `core/cipher.py`** — TLS is entirely `core/tls.py` + `core/queue.py`
(`ciphers` is just an OpenSSL-string field on `TLSConfig`).

Two repo-level trees sit outside the package: **`.agents/`** — the spec-driven "software
factory" (the `hs-feature|plan|build|review|publish` lifecycle skills plus three operational siblings —
`hs-harness` (meta/maintenance), `hs-roadmap` (retire landed seeds, keep `ROADMAP.md` true) and
`hs-release` (version bumps / releases); `factory/` methodology,
invariants, EARS/templates, a non-Claude harness
`portability.md` contract, and the `bin/` FSM + `meta_status.py` scripts; `.claude` symlinks here);
and **`spec/{slug}/`** — the committed, dated
per-feature design records (`GOAL.md`/`PLAN.md`/`TECH.md`/`REVIEW.md`, plus a `META.md` harness-feedback
log) the factory produces and **retains on merge**. `AGENTS.md` remains ground truth; `spec/{slug}/` is
a point-in-time record of intent. See `.agents/factory/methodology.md` and the lifecycle note under
"Working on this codebase as an agent."

Two more repo-level paths carry work that is **not yet in flight**:

```
issues/{slug}.md   # deferred code work, pre-shaped; /hs-feature promotes one into a GOAL
ROADMAP.md         # the ordered index of future cycles — one entry per issue
```

**Where a deferral goes — five homes, one rule each.** A pass that decides *not* to fix something
must still record it, and the destination is not a matter of taste:

| File | Holds | Written by |
|------|-------|------------|
| `spec/{slug}/META.md` | **Harness/skill feedback only** — "was this the *factory's* fault". Never code follow-ups. | the lifecycle skills |
| `issues/{slug}.md` | **Deferred code work**, pre-shaped from [`templates/ISSUE.md`](.agents/factory/templates/ISSUE.md) | whoever defers it |
| `ROADMAP.md` | the **ordered index** — one entry per issue, `**Seed:**` pointing at the `issues/` file | whoever defers it |
| `.security/issues/{slug}.md` + `.security/ROADMAP.md` | the same two things for **unremediated security findings** — gitignored, never published | whoever defers it |
| `spec/{slug}/` | work **actually in flight** | the lifecycle skills |

An `issues/{slug}.md` is a *candidate, not a contract*: `/hs-feature` promotes it into
`spec/{slug}/GOAL.md`, and that promotion is where appetite, non-goals and the R-IDs get
negotiated with a human. **Never copy one into a `GOAL.md` verbatim** — `hs-review` grades a GOAL,
and a proposal nobody accepted is not a contract. The `status:` field is the guard: `unshaped`
(raw deferral — evidence captured, nothing agreed), `shaped` (already negotiated with a human, but
**not yet accepted into a cycle**), `adopted:{slug}` (promoted; `spec/{slug}/` owns it now, and the
record stays *while the cycle is in flight* so the ROADMAP index does not dangle). Two further
values close a deferral **without** shipping — `declined` and `accepted-behaviour` — and those are
terminal records, indexed under `ROADMAP.md` § *Settled questions* rather than as cycles.

**A deferral is retired, not kept forever.** When the cycle that adopted a seed lands on `develop`,
`/hs-roadmap` deletes the seed and removes its `ROADMAP.md` entry: `spec/{slug}/` is the retained
account and git history holds the file. Retire only on evidence the cycle *landed* — an
`adopted:{slug}` marker proves a cycle started, never that it finished. The security lane inverts
this: nothing under `.security/` is ever deleted, because it is gitignored and a deletion there
leaves no history to recover from, so a remediated finding keeps its file, its closure is recorded
in `.security/STATUS.md`, and its `.security/ROADMAP.md` entry moves out of the ordered queue.

**The security lane is not optional.** A deferral that describes an *unremediated* weakness — a
live exploitable mechanism, an attack path, or enough evidence to reconstruct one — goes in
`.security/`, which is gitignored along with `.local/`. Publishing a standing roadmap of live
vulnerabilities hands an attacker a work plan. The *fixes* land as ordinary public commits and PRs
when they ship; only the inventory of what is still open stays private. Inside that lane,
`.security/README.md` + `findings/F*.md` hold the evidence, `.security/STATUS.md` is the
current-status overlay, and `.security/ROADMAP.md` is the ordered remediation index — the same
convention as the public lane, hidden. When in doubt which lane an item belongs to, use
`.security/` and ask.

This coexists with the **GitHub tracker**: a GH issue is the public-facing ticket,
`issues/{slug}.md` is the pre-shaped spec behind it, and the two may link to each other. Neither
replaces the other.

## Architecture & data flow

```
submit ─writes rows→ [DB] ←reads pending─ server(Scheduler FSM) ─stages→ [zone pools] ←request─ client(ClientScheduler)
                       ▲                                                                      │ spawns subprocs
                       └──────────────── server writes exit/timing/output back ──────────────┘ returns results
```

The queue exposes four threading `queue.Queue`s served in-process by the manager's request
handling: `scheduled` (task bundles submitted live, without a database), `completed` (results),
`heartbeat` (client liveness), `confirmed` (bundle acks); and one `Dispatcher` (`core/dispatch.py`),
whose two remotely callable methods are `request`, which hands work to clients, and
`takes_submissions`, which `hs submit -q` calls to learn whether the server serves its live queue
(only a server without a database does). The scheduler stages stamped tasks in one pool per zone;
a client calls `request` with its id, zone and advertised size, and the call runs on the handler
thread of that client's own connection and returns, on it, up to that many tasks
cut from its zone's pool (JSON text both ways, bounded wait, first-come-first-served). There is no manager child process: `QueueServer.start` runs its own
accept loop around the stock `multiprocessing.managers.Server` on daemon threads of the server
process (`core/queue.py` `_InProcessServer`), whose handler threads and the server's own threads
share the same queue objects. That loop leans on CPython privates; `tests/test_queue.py`
`TestPrivateInternals` pins them. Remote clients see a pinned nine-method surface (`QUEUE_EXPOSED`)
on the queues and a pinned two-method surface (`DISPATCH_EXPOSED`: `request` and
`takes_submissions`) on the dispatcher; `tests/test_dispatch_version.py` pins the latter, so a
method added to or dropped from that pickle-framed RPC surface fails a test.

**Two modes change the picture — check for them before assuming DB behavior:**
- `in_memory` (in-memory SQLite): the `Scheduler` is `None` and the server **skips all DB
  writeback, retries, revert, and group-gating**. Guard every server-side DB write with
  `if not self.in_memory:`.
- `no_confirm`: disables orphan recovery (client id is never stamped), so a dead client's
  tasks are **not** rescheduled.

## Task lifecycle contract (read before touching `data/model.py`)

There is **no status enum** — task state is a function of nullable columns:

- **unscheduled**: `schedule_time IS NULL`
- **in-flight / interrupted**: `schedule_time` set, `completion_time IS NULL`
- **done (terminal)**: `completion_time` set
- **cancelled (terminal)**: `exit_status == CANCEL_STATUS` (`-1`, `data/model.py:49`) **and**
  `completion_time` set

Every new query must reproduce these predicates exactly. Setting `schedule_time` without
eventually setting `completion_time` creates a task that `revert_interrupted()` re-runs on the
next server start.

**`exit_status` is overloaded** — respect the reserved ranges:

| Value | Meaning |
|-------|---------|
| `0` | success |
| `> 0` | real process exit code |
| `-1 .. -64` | killed by signal N (`-N`); **`-1` = `CANCEL_STATUS`** (collides with SIGHUP death) |
| `-1001` | `TASK_TEMPLATE_ERROR` — template expansion failed, task never ran (`client.py:612`) |
| `-1002` | `TASK_RESOURCE_ERROR` — insufficient local resources, never ran (`client.py:613`) |

**New internal "never ran" sentinels go below `-1000`. Never use `-1..-64`** (reserved for
signal deaths).

**Retry model:** retries are **new rows** (`attempt+1`; the new row's `previous_id` names the
old row, which gets `retried=True` and `next_id`) — never mutate a row in place to retry.
`attempts == max_retries + 1` and this relationship spans `server.py`,
`Task.query_retryable`/`select_failed`/`count_retryable`, and `Task.increment_group` — change them
together. **Every failure/retry/count-as-failed query MUST filter `exit_status != 0 AND
exit_status != CANCEL_STATUS`**, or cancelled tasks get resurrected.

Nothing in the schema prevents a doubled retry. A failed row is retried once because the
server's single Scheduler thread is the only caller of `Task.next`, and `Task.query_retryable`
(under `select_failed`) skips rows already marked `retried`. **Unguarded**, each adding a second
retry row with the same `previous_id`:
- two servers on one database, which is unsupported: no query takes row locks, so new tasks
  double-schedule too;
- a process killed between the retry insert and the `retried` mark, which are separate commits;
- a reset `retried` flag, from `hs update`.

A returned result writes only its result columns (`Task.result_columns`), and only for its current
assignment: the row's `schedule_time` as dispatched, while `completion_time` is NULL
(`Task.apply_results`). A stale or duplicate result is logged and dropped, so a late result from an
evicted client cannot reopen a retried or cancelled row.

Pre-2.9 SQLite databases keep `UNIQUE` on both columns, because SQLite cannot drop them without a
table rebuild. Normal operation never trips them; in the three cases above they raise
`IntegrityError` instead.

**Group-gating can stall by design:** `increment_group` won't advance while the current group
has incomplete tasks or failures still awaiting retry, so one permanently-failing task halts the
group (and, in non-`forever` mode, the server). This is intended; don't "fix" it without
understanding it. Once the group clears, the next group is the smallest one with incomplete
tasks, which may be lower when tasks arrive late. File input to `hs cluster`/`hs server` is
submitted in full (the whole file) before the scheduler starts, so its groups run in order. Stream
input is best effort: a late lower group runs after the current one, with a warning.

## Concurrency model (read before touching `server.py`, `client.py`, `core/fsm.py|thread.py|signal.py`)

Long-running work runs in **daemon threads** (`core/thread.py`) driving **state machines**
(`core/fsm.py`). This is the single most error-prone area and the concurrency primitives have
**no dedicated unit tests** — edit with care.

**Authoring an FSM + Thread:**
- Subclass `StateMachine` with a `State` enum that includes a **`HALT`** member; every
  non-HALT state returns the next `State` and has an entry in the `actions` dict.
- Subclass `Thread` and implement `run_with_exceptions`. **Override `stop()` to call
  `self.machine.halt()` before `super().stop()`** — the base `Thread.__should_halt` flag is
  vestigial and does **not** stop an FSM.
- **`fsm.py` does NOT poll signals.** `StateMachine.next()` only checks its private halt flag.
  Signal polling is **manual** — individual scheduler action methods call `check_signal()`
  (see `server.py`, `client.py`). A new state does not observe signals unless you add the poll.
- **Every blocking queue op inside a state must use a finite timeout and re-enter its state.**
  That timeout is your shutdown-latency bound; unbounded blocking hangs the process (daemon
  threads only die at process exit).
- Shut down with `stop(wait=True)` so `join()` re-raises captured thread exceptions.

**Shutdown/sentinel ordering is load-bearing.** Preserve the exact ordering in
`ServerThread`/`ClientThread` (client: scheduler → executors → collector → heartbeat; server:
submitter → scheduler → `dispatcher.finish()` (where per-client sentinels on `scheduled` used to
be posted: clients drain their zone's staged tasks, then are told to shut down) → heartbeat join →
staged revert (`ServerThread.revert_staged`: rows still staged, never delivered, go back to
unscheduled) → sentinels to receiver/confirm) and the submit flush
(`loader.join()` then `queue.put(None)`). Reordering drops the last results or hangs a join.
**A staged revert never touches a completed or cancelled row.** Both staged reverts, at shutdown and
when a zone's last client leaves (`Scheduler.revert_unstaged`), go through `Task.revert_staged`: one
conditional update on `completion_time IS NULL`, so a row cancelled while staged stays cancelled
and a deleted one is skipped. Never revert staged ids with the unconditional `Task.revert_all`.
The stream sentinel is `make_sentinel()` (`serialize(None)`); consumers detect end-of-stream
by dequeuing it. **Remote-queue payloads go through `serialize_tasks`/`deserialize_tasks` and
`heartbeat.pack` (JSON) — never put a live object or a literal `None` on a remote queue.**

**Resource accounting** (`core/resource.py`) is process-global mutable state under
`executor_lock` and assumes **one `ClientThread` per process**. `acquire`/`release` must be
balanced; tasks that early-exit (template/resource error) never acquire, so must never release.

## Signals

`register_handlers()` installs a cooperative handler for **`SIGUSR1`, `SIGUSR2`, `SIGHUP`
only** (no-op on Windows). **`SIGINT`/`SIGTERM` are deliberately not captured** — Ctrl-C
raises `KeyboardInterrupt`, handled by cmdkit's `Application.main`, *not* the `check_signal()`
path. Graceful cooperative drain is driven by USR1/USR2/HUP.

`signal.RECEIVED` is a **process-global, sticky** module flag shared across all threads: it is
only reset on the SIGHUP log-rotation path. USR1/USR2 are intentionally never reset — once
seen they steer every polling FSM toward shutdown. **Do not add stray `reset_signal()` calls.**

## Queue transport & security

- **TLS is ON by default** (`config.server.tls.enabled = True`, `core/config.py:144`).
  `cert`/`key`/`cafile` default to `'<auto>'` and are materialized as a self-signed cert on
  first server start under the site lib dir. Disable only with `--no-tls` (represented
  internally as `tls=None`). There is **no `cipher.py`**.
- **Auth is ALWAYS required.** The shared key gates the queue via a multiprocessing HMAC
  challenge/response (the key never crosses the wire). The server **refuses to start** with
  the placeholder key `DEFAULT_AUTH = '<not-secure>'` and enforces `AUTH_MINIMUM_LENGTH`
  (≥16) and `AUTH_ALLOWED_CHARS` (`[A-Za-z0-9._+/=-]`). Never weaken these; never remove the
  authkey to "disable TLS" (pass `tls=None` instead).
- **The server process hosts the manager.** Queue connections, the HMAC handshake and the TLS
  handshake are handled by threads of the server process itself (not a forked manager child). The
  server's TLS context lives on its own listener, never in the process-wide slot that clients
  install (`core/tls.py` `install_process_context`), so a same-process client with another TLS
  config cannot affect it. The wire, auth and TLS behaviour are those of the stock manager,
  unchanged. A failure to start serving (unsupported CPython internals, bind or listen error)
  raises `QueueServerError` from `QueueServer.start`, which `ServerThread.start` re-raises. Since
  every connection's descriptor lives in the server process, the accept loop refuses a connection
  that would use the last `RESERVE` descriptors below the soft open-file limit (one WARNING per
  interval), so the database and logs keep working; a server TLS context that cannot be built is
  logged once at ERROR and connections fail closed. A queue client's proxy finalizer (its decref)
  can run on any thread the cyclic GC picks, server threads included. So once `QueueClient.connect`
  has made a proxy, a decref finalized on the server side goes to the `hypershell-queue-decref`
  thread, which the process's first server starts (`core/queue.py` `_decref`). The server side is
  decided by thread, never by address: every thread an in-process server starts (accept loop,
  handlers, stop, the sender), any thread inside a server's start or stop, any thread in a TLS
  handshake with a queue server (the accept loop runs it inline), and any thread that `threading`
  has not registered as started: a new thread before its mark, one past its unregistration, or one
  that `threading` did not start (an entry left under a reused identifier by an ended thread does not
  count). Every other decref is the stock one, on the finalizing thread. A forked child drops the marks and waiting decrefs it inherits.
- **Every queue TCP connection sets `TCP_NODELAY` on both ends where the platform allows it**
  (`core/queue.py` `_no_delay`): TLS in `SecureListener.accept` and `secure_client`, cleartext
  through the `hypershell-plain` serializer's `(Listener, Client)` pair. The cleartext pair reaches
  the stock connection's socket through a duplicated descriptor (skipped where there is none, as on
  Windows) and puts back the descriptor's blocking mode, which a process default socket timeout
  would otherwise clear. The client writes twice before a read (the handshake's last message, then
  its first request); the server does so only under TLS 1.3 (its session tickets, then the
  challenge). With Nagle's algorithm on, that second write waits out the peer's delayed ACK (about
  40 ms on Linux) on every new connection. It is a socket option only; the wire is unchanged.
- **Wire format:** the multiprocessing `BaseManager` RPC **framing is `pickle`** (an RCE
  surface — a party who reaches the port *and* holds the key, or an active MITM in `insecure`
  mode, can do more than enqueue tasks). HyperShell's **payloads are JSON** (`core/types`,
  `serialize_tasks`, `heartbeat.pack`). Both facts matter; don't conflate them.
- **No mTLS** — the server authenticates to the client, the client authenticates via the
  shared key; there is no client-certificate identity (`verify_mode=CERT_NONE`).
- **Distributed-TLS pitfall:** `'<auto>'` only works on a single host / shared filesystem. A
  remote client with default config generates *its own* self-signed cert and pins to it, so
  verifying the real server fails. Distributed TLS needs a shared cert, `server.tls.cafile`,
  or a `fingerprint` pin. **Cert/key material is not forwarded to launched clients** — only
  the disabled state (`--no-tls`) is propagated.
- **Auth-token exposure:** each cluster launch mints a fresh `secrets.token_hex(64)` and
  passes it to clients as `-k <auth>` **on the command line** — visible in `ps`/`/proc`
  locally and on remote hosts. `redact_secrets()` only sanitizes log output, not the process
  table. The full posture is `docs/security.rst` (authoritative).

## Cluster orchestration

- `LocalCluster` embeds server + clients in one process group.
- `RemoteCluster` launches clients via an external launcher (MPI/Slurm/…); `AutoScalingCluster`
  grows/shrinks the client fleet (staggered launch, scale-to-zero).
- **`SSHCluster` shells out to the plain `ssh` binary via `subprocess.Popen`** (one process
  per host; `config.ssh.args` for options), with `a[00-32].cluster` nodelist expansion.
  **paramiko is NOT used here** — paramiko lives only in `core/remote.py` for SFTP task-output
  retrieval. (The `docs/security.rst` claim that `SSHCluster` uses paramiko is inaccurate.)
- There is no shared client-argv builder — a change to launched-client arguments must be
  replicated across `local`/`remote`/`ssh`/`autoscale`. In JSON mode, clients must be sent
  `DEFAULT_TEMPLATE` (never the user template) to avoid double expansion. The advertised
  `HOSTNAME` must be routable from clients. The `AutoScaler` never terminates clients (they
  self-exit on idle) and silently ignores `no_confirm`/`in_memory`/`forever`/`restart`.

## Configuration

`config` is built **once at import** (`core/config.py`) and is effectively an **immutable
per-process singleton**. Precedence: **CLI > `HYPERSHELL_*` env > local > user > system >
preload > defaults**. `hs config set` writes to disk but **does not mutate the live singleton**
— a running server won't see changes without a restart.

- `_eval` / `_env` suffixes on any key resolve via a shell command / env var (cmdkit) — used
  to keep secrets out of the TOML.
- Sentinels: `'<auto>'` (materialize), `'<none>'` (unset). The `X or None` idiom means
  **`0` = unlimited/auto** for many numeric knobs.
- Defaults agents care about: server `port`, `bundlesize`, `bundlewait`, `evict` (≥10s),
  `attempts`, and `tls.enabled = True`. Note `bundlesize`/`bundlewait` are read from
  `config.submit.*` for CLI defaults but `config.client.*` for module defaults — two sections.

## Data layer specifics

- `data/core.py` creates the **engine and `Session` at import time** and can `sys.exit` on
  failure (`bad_config = 3`); it rebinds `config` to a DB-only namespace. SQLite uses
  `check_same_thread=False`. Prefer the `postgres-system` / `postgres-c` install extras over
  `postgres` in production (OS `libpq`/OpenSSL for security updates).
- **Task-state transition logic belongs in `Task` classmethods** in `data/model.py`
  (`next`, `select_failed`, `increment_group`, `revert_orphaned`, `revert_interrupted`,
  `cancel_all`, …), not scattered into the FSM driver files. Adding a column means touching
  the model, the relevant queries, and any dialect-variant type handling.
- **The schema-upgrade step** (`upgradedb()` → `data/upgrade.py`) runs from `initdb` (every SQLite
  start, `--initdb`, `hs initdb`) and `checkdb` (every PostgreSQL start), and brings an existing
  database up to the model in place. It only adds missing tables, columns and indexes — the one
  removal is the retired `previous_id`/`next_id` `UNIQUE` constraints, on PostgreSQL only — and logs
  each change at INFO (the no-op path at `trace`). A new column must be nullable or carry a
  constant `server_default`, and a new index must not be `UNIQUE`, or the step refuses to upgrade
  an existing table (`SchemaUpgradeError`). Python API callers bypass the CLI helpers and must
  call `ensuredb()` first.
- **A 0-row UPDATE of a `task` row is not proof the row is gone or no longer qualifies.** Under the
  `timescale` provider on TimescaleDB 2.29+, an UPDATE that waited on another transaction's row lock
  can match 0 rows although the row exists and still satisfies the statement's condition (a plain
  table, plain PostgreSQL and TimescaleDB before 2.29 re-check it and match 1). A new statement sees
  the other transaction's commit and matches normally. So a task-row write that another writer can
  contend checks its own matched count and, on 0, re-runs only while a fresh read says the row still
  qualifies, at most `CONTENDED_RERUNS` times, each extra attempt logged at DEBUG; at the bound it
  logs a WARNING naming the task and the path and takes a safe outcome (never a crash, a silently
  stranded task, or a write that lands without its condition). A write that matches first time is
  neither re-run nor re-checked. `Task.confirm_all`/`Task.revert_all` (per-row UPDATE by id) and
  `Task.apply_results` (re-run behind the classifying SELECT, `ResultOutcome.DEFERRED` at the bound,
  held by the Receiver) are the pattern. Never use `bulk_update_mappings` or an ORM flush for such a
  write: they assert the total and raise `StaleDataError`. The Scheduler's `Task.next` stamp and
  `retried` mark, `Task.cancel_all` and `hs update` do not follow it yet
  (`issues/hypertable-contended-scheduler-and-cli-writes.md`).

## Testing

- `tests/conftest.py`: `isolate_environment()` runs at **import time** and an **autouse
  `clean_env` fixture** re-runs it for **every** test — it blanks `HYPERSHELL_CONFIG_FILE`
  and binds `HYPERSHELL_SERVER_PORT` to a `free_port()`. The **`temp_site`** fixture only
  creates the temp dir and sets `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
  `HYPERSHELL_LOGGING_LEVEL=DEBUG` (env isolation is *not* its job). Use `temp_site` for
  anything touching config or the DB.
- `tests/__init__.py` helpers: `main(argv)` → `(returncode, stdout, stderr)`;
  `main_lines(argv)` → stripped-line lists; `assert_output(pattern, output, count=, groups=)`
  for regex line-counting; `create_taskfile_echo(temp_site, count, tags=)`.
- **Only `@mark.unit` and `@mark.integration` are real markers** under `--strict-markers`.
  `parameterize` is a non-functional placeholder — use pytest's built-in `@mark.parametrize`.
  Always tag new tests `unit` or `integration`. Integration tests shell out to the installed
  CLI, so they need `uv sync` first. `client.py` has no dedicated test file — its coverage is
  indirect (be extra careful editing it).

## Packaging & release

- CI matrix (`.github/workflows/tests.yml`) runs `pytest` on Python 3.11–3.14.
- `pyproject.toml` ships **three mutually-exclusive postgres extras** (`postgres`,
  `postgres-system`, `postgres-c`). **Dependency floors are pinned low for EPEL/RHEL parity —
  do not raise them casually.** There is a `greenlet` constraint for Python 3.14 wheels.
- The wheel ships `share/` (bash+zsh completions, man pages) via
  `[tool.hatch.build.targets.wheel.shared-data]`; a CI metadata job asserts those paths — keep
  `share/` and the CI list in lockstep.
- `README.rst` must pass `twine check --strict` (no `raw::` directive) or PyPI publish 400s.

## Docs

reStructuredText + Sphinx (Furo theme). Conventions: `.. _anchor:` + `:ref:`, `===`/`---`/`^^^`
underlines (must be ≥ title length), `-------------------` section rules, `|br|`, `*HyperShell*`
italics, first-person-plural voice. CLI help text is generated into `docs/_include/*.rst` — a
CLI change updates those. Build with `uv run sphinx-build docs docs/_build` and confirm no new
warnings (`task_submit.rst`/`manual.rst` "not in any toctree" warnings are pre-existing).

Current docs, notes, seeds and PRs name only the top-level commands: `hs list`, `hs info`,
`hs wait`, `hs run`, `hs update`. The `hs task …` subcommands stay in the code for backwards
compatibility until v3, but are never referenced.

## High-risk files & footguns (quick reference)

- **`data/model.py`** — highest blast radius. A dropped `CANCEL_STATUS` filter or a missing
  `completion_time` silently double-runs or resurrects tasks; one retry per failed row rests on
  the single Scheduler plus the `retried` filter, not on the schema (`previous_id`/`next_id` are
  not unique, except in pre-2.9 SQLite databases); a contended `task`-row UPDATE can match 0 rows on
  a TimescaleDB 2.29+ hypertable, so a new task-row write follows the `CONTENDED_RERUNS` rule in
  *Data layer specifics*.
- **`server.py`** — Scheduler FSM, HeartMonitor (Issue-#29 re-registration branch, `evict_after≥10`),
  and shutdown/sentinel ordering.
- **`client.py`** — FSMs, process-global resource counters under `executor_lock`, sticky
  `RECEIVED`, shutdown order — and no dedicated test file.
- **`core/queue.py` + `core/tls.py`** — pickle-framed RPC (RCE surface); `SecureConnection`
  framing must stay byte-compatible with `multiprocessing.connection`; per-process TLS context
  install; no mTLS; keep the post-handshake fingerprint check.
- **`core/fsm.py|thread.py|signal.py`** — load-bearing, untested; the vestigial `__should_halt`
  flag and sticky global `RECEIVED` are subtle.
- **`cluster/remote.py|ssh.py`** — per-mode argv builders that must change together; fresh
  auth token; `HOSTNAME` routability; autoscaler never terminates clients.
- `submit.py` queue-mode silently ignores `-t/--tag` (leaves `group=None`) — asymmetric vs DB
  mode; tag/attribute resolution actually happens in `Task.new`.

## Working on this codebase as an agent

- **Use the factory for non-trivial work.** A feature/fix/refactor flows through the `.agents/`
  spec-driven lifecycle — `/hs-feature` (shape `GOAL.md`, or promote an
  `issues/{slug}.md`) → `/hs-plan` (research +
  `PLAN.md`/`TECH.md`) → `/hs-build` (execute phases) → `/hs-review` (blind, externally-verified
  QA) → `/hs-publish` (squash PR to `develop`), each on a `feature/`|`fix/` branch with artifacts
  committed under `spec/{slug}/`. `.agents/factory/methodology.md` is the *why*;
  `.agents/factory/invariants.md` is the curated footgun checklist derived from this file (kept in
  lockstep — if it drifts, this file wins). Ceremony scales to appetite: a one-sentence change may
  skip the lifecycle entirely. Each lifecycle skill also ends with a **silence-by-default meta-note**
  that logs *harness* friction (not code issues) to `spec/{slug}/META.md`; `/hs-publish` surfaces
  substantial notes in the PR; and the human-gated **`/hs-harness`** (meta/maintenance, **not** a
  lifecycle step) applies those fixes back to `.agents/`, logging each in `factory/harness-log.md` —
  and never weakens a non-negotiable gate on a finding's say-so.
- **Retire what shipped with `/hs-roadmap`** (operational, **not** a lifecycle step): run it between
  releases. It finds every `issues/{slug}.md` carrying `status: adopted:{slug}`, confirms the cycle
  actually reached `develop` (`git ls-tree develop -- spec/{slug}`, because the marker only proves a
  cycle *started*), and after a human-gated preview deletes the seed and its `ROADMAP.md` entry —
  then repairs the drift the removal leaves: discharged `Depends on:` prerequisites, dangling
  cross-references, stale figures in surviving seeds, and markers left by abandoned branches. It is
  deliberately **not** folded into `/hs-publish`: retirement writes outside `spec/`, which is exactly
  what publish's staleness gate exists to notice. In the security lane it moves entries and never
  deletes.
- **Cut releases with `/hs-release`** (also operational, **not** a lifecycle step): it bumps the
  single version source, rebuilds man pages, runs the CI-mirror gate (incl. `twine check --strict`),
  signs a tag, and publishes — in one of three modes (`patch` off `master`, `pre-release` off
  `develop`, `full-release` promoting `develop` → `master`) — rehearsed in a `git worktree` dry-run
  and gated on an explicit human OK before any irreversible push. It fills the gap `/hs-publish` leaves.
- **Verify by driving the CLI, not just tests.** After a change, exercise the real flow in a
  throwaway site: e.g. `.agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run hsx -t 'echo {}'
  -N4 && uv run hs list"` — the wrapper mirrors the pytest `temp_site` fixture's env isolation so
  nothing touches your real database. The concurrency and DB behavior are where bugs hide, and
  integration tests need the installed CLI anyway.
- **Put logic where it belongs:** task state/query logic → `Task` classmethods; never
  hand-roll state predicates in FSM code. Reuse `cmdkit.app.exit_status` constants for return
  codes — don't invent integer literals. New subcommand = a cmdkit `App`
  (`interface` + `run()` + `exceptions = {**get_shared_exception_mapping(__name__)}`) imported
  and registered in `__init__.py`'s `commands` dict (+ `APP_HELP`, and `DISTRIBUTED_ROLES` if
  it writes its own log).
- **Import-time side effects are real:** importing `hypershell` runs `core.sys`
  (`HYPERSHELL_PYTHONPATH`), builds the config singleton, and creates the DB engine — several
  of these can `sys.exit(3)`. Keep `import hypershell.core.sys` first in `__init__.py`.
- **Prefer idiomatic, concise Python** (the maintainer refactors toward it), but verify real
  equivalence — e.g. `dict.get(k, default)` evaluates `default` eagerly.
- **Comments are declarative statements, not spec pointers.** Write each comment/docstring as a
  capitalized statement of the invariant or the *why* (`# Reserved id, exempt from gating.`) — not a
  lowercase fragment and not a `label:`-prefixed form. **Never embed feature-scoped spec ids** (`R#`,
  `P#`) in source: they restart per feature, live in `spec/{slug}/`, and collide across branches, so a
  reader of the merged tree can't tell what `R7` means. Requirement provenance lives in the commit, the
  PR, and the retained `spec/{slug}/` (the traceability chain in `.agents/factory/methodology.md`).
  Referencing *stable* things is fine — real symbols (`CANCEL_STATUS`), documented invariants, the
  `exit_status` ranges.
- **Parallelizing agent work:** the subsystems safe to touch independently are the leaf apps in
  `task.py`, docs, and tests. The coupled core — `data/model.py`, `server.py`, `client.py`,
  `core/queue.py|fsm.py|thread.py|signal.py` — shares the invariants above and should be
  changed with a single coherent view; parallel edits there conflict on *contracts*, not just
  lines. When in doubt, fan out to read/understand and serialize the edit.
- **This file is the map, but it drifts.** For a deep change, re-verify the specific invariant
  against the source before relying on it, and update this file when the code moves.
