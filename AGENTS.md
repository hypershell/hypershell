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
| `task.py` | The `task` CLI group and top-level `submit`/`info`/`wait`/`run`/`list`/`search`/`update` apps, incl. exit-status-colorized `hs list`. |
| `core/` | Shared infrastructure (see below). |
| `data/core.py` | SQLAlchemy engine/`Session`, DB-URL construction, provider selection, `InitDBApp` (`hs initdb`). |
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
factory" (the `hs-feature|plan|build|review|publish` skills plus `factory/` methodology,
invariants, EARS/templates, and the `bin/` FSM scripts; `.claude` symlinks here); and
**`spec/{slug}/`** — the committed, dated per-feature design records
(`GOAL.md`/`PLAN.md`/`TECH.md`/`REVIEW.md`) the factory produces and **retains on merge**.
`AGENTS.md` remains ground truth; `spec/{slug}/` is a point-in-time record of intent. See
`.agents/factory/methodology.md` and the lifecycle note under "Working on this codebase as an
agent."

## Architecture & data flow

```
submit ─writes rows→ [DB] ←reads pending─ server(Scheduler FSM) ─bundles→ [TCP queue] → client(ClientScheduler)
                       ▲                                                                      │ spawns subprocs
                       └──────────────── server writes exit/timing/output back ──────────────┘ returns results
```

The queue exposes four `JoinableQueue`s over one manager: `scheduled` (task bundles),
`completed` (results), `heartbeat` (client liveness), `confirmed` (bundle acks).

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

**Retry model:** retries are **new rows** (`attempt+1`, `previous_id`/`next_id` UNIQUE chain,
old row `retried=True`) — never mutate a row in place to retry. `attempts == max_retries + 1`
and this relationship spans `server.py`, `Task.select_failed`, and `Task.increment_group` —
change them together. **Every failure/retry/count-as-failed query MUST filter
`exit_status != 0 AND exit_status != CANCEL_STATUS`**, or cancelled tasks get resurrected.

**Group-gating can stall by design:** `increment_group` won't advance while the current group
has incomplete tasks, so one permanently-failing task halts the group (and, in non-`forever`
mode, the server). This is intended; don't "fix" it without understanding it.

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
submitter → scheduler → sentinels to heartbeat/receiver/confirm) and the submit flush
(`loader.join()` then `queue.put(None)`). Reordering drops the last results or hangs a join.
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

## High-risk files & footguns (quick reference)

- **`data/model.py`** — highest blast radius. A dropped `CANCEL_STATUS` filter or a missing
  `completion_time` silently double-runs or resurrects tasks; `previous_id`/`next_id` are UNIQUE.
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
  spec-driven lifecycle — `/hs-feature` (shape `GOAL.md`) → `/hs-plan` (research +
  `PLAN.md`/`TECH.md`) → `/hs-build` (execute phases) → `/hs-review` (blind, externally-verified
  QA) → `/hs-publish` (squash PR to `develop`), each on a `feature/`|`fix/` branch with artifacts
  committed under `spec/{slug}/`. `.agents/factory/methodology.md` is the *why*;
  `.agents/factory/invariants.md` is the curated footgun checklist derived from this file (kept in
  lockstep — if it drifts, this file wins). Ceremony scales to appetite: a one-sentence change may
  skip the lifecycle entirely.
- **Verify by driving the CLI, not just tests.** After a change, exercise the real flow in a
  `temp_site`: e.g. `seq 100 | uv run hsx -t 'echo {}' -N4` and inspect `uv run hs list` /
  `hs info`. The concurrency and DB behavior are where bugs hide, and integration tests need
  the installed CLI anyway.
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
