# Research 03 — relay-local server, relay-local database, site fleet

Scope: R1, R3, R4, R6, R10, R16, R17 (+ CLI wiring for R18). Read against the post-zone preview
(develop + Z1 + Z2) at `/tmp/claude-501/relayplan/postzone/`, with both sides of the conflicted
`server.py` / `model.py`. Experiments: scratch under `/tmp/claude-501/relayplan/scratch03/`
(develop venv + a patched copy of `src/`).

## 1. Import-time DB binding (highest risk)

**How it binds today.** `hs` = `hypershell:main`; `hypershell/__init__.py` imports `submit`,
`server`, `client`, `cluster`, `task`, `data` at module top, so `data/core.py` runs **before
`main()` sees argv**. `core/config.py` builds `config` at import (default → preload → system → user
→ local file → `HYPERSHELL_*` env, depth-first merge; `HYPERSHELL_CONFIG_FILE` replaces the file
layers with one file, `''` with none). `data/core.py` copies `config.database`, pops
`schema/echo/connect_args/pragmas/timescale`, builds `engine` and `Session = scoped_session(...)`.
Frozen copies made from that: `data/__init__.py` (`engine` name copy used by
`initdb/upgradedb/checkdb`; `DATABASE_ENABLED/PROVIDER/DIALECT/SITE`, `TIMESCALE`), `server.py`
(`DATABASE_ENABLED`), `task.py`/`submit.py`/`cluster/__init__.py` (`DATABASE_DIALECT`), and
`model.py` bakes `schema` into every table's `__table_args__` at class creation. The `Engine`
"connect" listener applies pragmas only if the *primary* provider is sqlite.

**Options, tested:**
- *(a) env vars only* — fails. Over a postgres config file, `HYPERSHELL_DATABASE_PROVIDER=sqlite`
  + `_FILE` → `Cannot provide both 'file' and 'database' for SQLite` (exit). Blanking keys (`''` →
  None) gets further but unknown keys survive as URL params (`sqlite:///…?sslmode=require`). Env
  can override keys, never delete them.
- *(b) rebind at relay start* — `Session` is one shared `scoped_session` object (rebinding via
  `Session.remove(); Session.configure(bind=…)` would propagate), but the frozen copies above, the
  baked `schema`, and the pragma listener would all still describe the primary. Also, with a
  postgres primary and no psycopg/libpq, **import exits before any relay code runs** (confirmed on
  this machine). Rejected.
- *(c) separate entry point* — collapses into (a)/(d): any `hypershell.*` module imports the
  package `__init__` first.
- *(d) wholesale section swap inside `core/config.py`, before `data.core` is imported* — **works**
  (patched copy): with a postgres config (`schema`, `sslmode`, env password),
  `config['database'] = Namespace(provider='sqlite', file=…)` gives `engine.url` = the relay
  file, `schema None`, `DATABASE_DIALECT sqlite`, `ensuredb()` + insert OK, and the psycopg
  **driver is never imported**. Every frozen copy downstream is consistent because they all derive
  from this one section.

**Recommendation (d) + trigger.** At the end of config build in `core/config.py` (before
`ACTIVE_CONFIG_VARS`): `RELAY_PROCESS = _relay_requested()`; if true, replace `config['database']`
with `{provider: 'sqlite', file: config.relay.database or <default>, pragmas: RELAY_PRAGMAS}`.
`_relay_requested()` = a private marker `_HYPERSHELL_RELAY` in `os.environ` (popped immediately, and
outside the `HYPERSHELL_` prefix so `Environ` never sees it) **or** argv sniff
(`basename(argv[0])` minus extension in {`hs`, `hyper-shell`} and `argv[1] == 'relay'`).
`RelayApp.run` asserts `RELAY_PROCESS`; if false (programmatic `main(['relay', …])` after import),
it `os.execve`s `sys.executable -c 'from hypershell import main; …' relay …` with the marker set.
Consequences: **one relay per process**; an upstream server and a relay can never share a process
(R20's loopback must use subprocesses).
Default path: `default_path.lib/relay/<HOSTNAME_SHORT>.db` (host-scoped: home dirs are shared across
login nodes), plus an exclusive lock (sidecar `fcntl` lock) so a second relay on the same file
refuses. Pragmas: `synchronous=FULL` (R3 "durable"); avoid WAL by default (unsafe on NFS homes);
document `[relay] database` for node-local disk. Prune-by-delete reuses free pages, so the file
stays size-stable (R10) without VACUUM.

**Deterministic test (R10/R20).** Subprocess with `HYPERSHELL_CONFIG_FILE` naming (i)
`provider='sqlite', file=<tmp>/primary.db` and (ii) `provider='postgres', host=127.0.0.1,
port=<closed>, schema='x', sslmode='require'`: run the relay entry (marker set) under a wrapper that
`event.listen(Engine, 'do_connect', record)`; assert every recorded URL is the relay file,
`primary.db` does not exist afterwards, `'psycopg' not in sys.modules`, and
`Task.__table__.schema is None`. Plus a unit test of the pure section-builder over
sqlite/postgres/timescale/`url=` configs.

## 2. Insert-before-confirm (R3) and stamps (R6)

**Insert.** Reply records are `Task.to_json()` text (Z2 `pack_bundle` stages
`json.dumps(task.to_json())` stamped at `Task.next`). `Task.from_json(d)` restores every column
(id, group, zone, tag, attempt, previous_id, fingerprint, source…). Before `add_all`, clear
`schedule_time`, `server_id`, `server_host`, `client_id`, `client_host` so the local scheduler
(`select_new`: `schedule_time IS NULL`) takes it. Insert row + stamp row in **one transaction**,
commit, then confirm upstream. Make it an upsert on `id`: an evicted-then-redispatched id arrives
again with a newer stamp, which must win.

**Where the upstream stamp lives: a side table, not a column.** Reasons found:
`revert_interrupted`/`revert_all` clear `schedule_time/server_id/server_host` (local restart would
erase a stamp kept there); the local scheduler needs those NULL; and **SQLite drops tzinfo**
(measured: `…957777-05:00` comes back naive `…957777`), so a stamp round-tripped through any
SQLite `DATETIME` column would no longer equal a PostgreSQL `timestamptz` upstream → every result
`STALE`. Side table `relay_task(id PK, schedule_time TEXT verbatim from the reply, server_id,
server_host, received_at)` on its **own `MetaData`/DeclarativeBase, not `Entity`**: `upgrade_schema`
creates every `Entity.metadata` table in *every* DB it touches, so an `Entity` subclass would appear
in users' primary databases. Create it with `upgrade_schema(engine, metadata=RelayBase.metadata)`
(the hook exists). On return: local row `to_json()` with `schedule_time/server_id/server_host`
replaced from the side table (`apply_results` only reads `id`, `schedule_time` + 13 result
columns).

**Timezones (surprise).** Result columns written by the local Receiver also lose tz in SQLite. Rule:
the relay DB holds relay-local wall time — convert received aware datetimes with `.astimezone()`
before insert, and attach the local tz to naive values on return. Site clients share the relay's
tz, so this is exact; without it, cross-timezone sites (Eastern vs Central/Mountain) return
skewed timing, and `waited` (computed by the client from `submit_time.astimezone()`) is skewed.

**Retry/group interplay (surprise).** Local `max_retries=0` → `attempts=1`. In
`increment_group`, any locally failed row (`attempt ≥ 1`, `retried False`) makes the group
*not viable*; in forever mode the local scheduler logs "waiting on failed tasks to be cleared" and
**never advances the group** until that row is gone. During an upstream outage (R9), failed rows
cannot be pruned, so the site stalls on the next group. Fix inside relay code only: completed rows
leave the scheduler's view at once — move them (same txn as the stamp lookup) into a relay-owned
outbox (`relay_result`, raw result JSON) that the returner drains and prunes on ack; or mark them
`retried=True` locally (not a result column, so it never leaks). Recommend the outbox: it is
also R9's durable buffer and keeps the scheduler tables small. With failures gone, D8/#74 holds:
`lowest_incomplete_group` picks up late, sparse, or out-of-order groups; no stall.
`revert_interrupted` on local restart re-runs local in-flight rows: fine (at-least-once; the
upstream stamp survives in the side table).

**Zones.** Keep the upstream zone id. `zone_id(name)` is a deterministic uuid5, so site clients
launched with `--zone <name>` match upstream ids exactly; NULL stays the default zone. Relay should
`Zone.ensure(name)` locally for names in logs. Rewriting to default would also work (zone is not a
result column) but breaks multi-zone sites (DQ1). Recommend: preserve, one fleet per served zone.

## 3. Embedding the local server

`ServerThread(source=None, bundlesize, bundlewait, in_memory=False, no_confirm=False, poll,
forever_mode=True, restart_mode, address, auth, max_retries=0, eager, redirect_failures,
evict_after, template, from_json, tls)`. Post-Z2 there is **no `dispatcher` arg**: it lives on
`self.queue.dispatcher`; queue size comes from the `config.server.queuesize` singleton. Process-global
state:
- **TLS:** Z1's in-process server keeps its context **on its listener** (`serve_with(tls)`), and
  the process context (`install_process_context`, set by `SecureManager.connect`) is used only for
  outbound client connections. So the upstream TLS (cafile/fingerprint) and the local `<auto>`
  server TLS coexist. **Constraint:** nothing in the relay may dial the *local* server in-process
  (it would use the upstream trust). So no `LocalCluster`/in-process `ClientThread` fleet, and no
  `LiveSubmitThread`.
- `INSTANCE` is used both as the local `server_id` and as the default `ClientInfo.client_id`.
  Harmless, since the local stamp never leaks, but per-zone upstream identities (DQ1a) must mint
  their own ids.
- `signal.RECEIVED` is process-wide: SIGUSR1/2 makes the local scheduler go `FINAL` and is seen by
  any reused client logic (`CLIENT_STANDALONE_MODE`). The relay must define one shutdown order:
  stop pulling, drain, return.
- Latency: the local scheduler sleeps up to `poll` (default 30 s, doubling from 0.5 s) and is woken
  only by dispatcher need, not by a DB insert. Pass a small `poll` (1–2 s), or add a public
  `Dispatcher.nudge()`, which is a Z2 file and would be an additive edit.

## 4. Site fleet (R1, R16, R17)

`AutoScalingCluster.__init__` (`cluster/remote.py`) is almost exactly the relay's inner half. It
mints `secrets.token_hex(64)`, embeds `ServerThread(forever_mode=True, max_retries=…, tls=…)`, and
builds the client argv `launcher… remote_exe client -H HOSTNAME -p -N -b -w -t -k <key> -d -S
[--zone] [--no-tls]…`, run by `AutoScalerThread`. `policy='fixed'` with `init=min=max=N` keeps N
clients and relaunches exits (`check_fixed`), which covers R16's local half. Quirks: it does not
forward `evict_after` (it uses the default), passes `max_retries` straight (so pass 0), and always
targets `HOSTNAME` (not `bind[0]` like `RemoteCluster`).

**Env leak (R17).** `AutoScaler.scale` and `RemoteCluster.run_with_exceptions` both `Popen(…,
env={**os.environ, **load_task_env()})`; `SSHCluster` inherits implicitly. So anything in the relay's
environment (`HYPERSHELL_SERVER_AUTH`, `HYPERSHELL_RELAY_*`, `HYPERSHELL_CONFIG_FILE`) reaches site
clients, and srun propagates it to the nodes. **No launcher edit is needed:** config is already
loaded at import, so `RelayApp` can scrub `os.environ` before the fleet starts. Pop the
`HYPERSHELL_RELAY_*` names, plus **any variable whose value contains the upstream key**. Launchers
copy `os.environ` at `Popen` time, so they inherit the scrub. Keep upstream credentials and TLS
under `[relay.upstream]` (with `auth_env`/`auth_eval` support from cmdkit), never in `[server]`.
Site clients resolve their TLS trust from their own `[server.tls]` (`<auto>` → site lib dir,
which is the relay's local cert), so upstream pinning placed in `[server.tls]` would break them.

**Security cycle.** The `fix/authkey-handling-hardening` branch has only GOAL/META, no plan yet. It
changes how `RemoteCluster.__init__`/`run_with_exceptions`, `AutoScalingCluster.__init__` +
`AutoScaler.scale`, and `SSHCluster.__init__`/`run_with_exceptions` hand the key over. `LocalCluster`
is out of scope for it. Relay should **call `AutoScalingCluster` (or `AutoScalerThread` +
`ServerThread`) as-is**, so it inherits whatever key delivery lands. Ask that plan to factor the
argv+env build into one helper (e.g. `client_launch(...) -> (argv, env)`) that both cluster
classes use. If it does not, relay's smallest late change is to reuse `AutoScalingCluster` whole
rather than copy its argv block. Relay calls nothing in `ssh.py`/`local.py`.

## 5. CLI wiring

- `__init__.py`: `commands['relay'] = RelayApp`, an `APP_HELP` line, `__all__`.
- `core/logging.py`: add `'relay'` to `DISTRIBUTED_ROLES`, which gives host-scoped
  `relay-<host>.log`.
- `core/config.py` `default['relay']`: `database` (`PARAM_UNSET` → default path), `bind`
  (`0.0.0.0`), `port` (**not 50001**: "relay beside the server" is the documented local-capacity
  pattern), `bundlesize`/`bundlewait`, `zone` (list), `poll`, `launcher`, `launcher_args`,
  `size` (fleet N), `client` knobs (`num_threads`, `timeout`, `template`), and `upstream`
  (`host`, `port`, `auth`, `bundlesize`, `heartrate`, `tls` with the same keys as `server.tls`).
- `RelayApp` offers no `--no-confirm` and refuses it explicitly. The local `ServerThread` keeps
  confirm on, and `AutoScalingCluster` ignores `no_confirm` anyway.
- Docs/completions: new `docs/cli/relay.rst` and `docs/_include/relay_{usage,help,desc}.rst`;
  additive edits to `docs/cli/index.rst`, `docs/manual.rst`, `docs/_include/config_param_ref.rst`
  (`[relay]`), `docs/database.rst`, `docs/security.rst`, `share/bash_completion.d/hs`,
  `share/zsh/site-functions/_hs`. Man pages are rebuilt at release; leave them alone.

## Recommendations

1. Bind the relay DB with a wholesale `config['database']` swap in `core/config.py`, triggered by
   argv sniff or a private marker; re-exec when a relay is invoked after import.
2. Host-scoped default `lib/relay/<host>.db`, exclusive lock, `synchronous=FULL`, no WAL by default.
3. Keep upstream stamps (verbatim `schedule_time` text) in a side table on a separate `MetaData`.
4. Move completed rows to a relay outbox at once, so local failures never hold a local group.
5. Normalize relay-DB datetimes to relay-local time; attach the tz on return.
6. Preserve zone ids and launch one site fleet per served zone with `--zone <name>`.
7. Reuse `AutoScalingCluster` (`policy=fixed`, `init=min=max=N`, `max_retries=0`, small `poll`)
   and run no in-process clients.
8. Scrub upstream credentials from `os.environ` before the fleet starts, by name and by value.
9. Keep upstream config under `[relay.upstream]`, so site clients' `[server.tls]` stays local.
10. Test the R10 fence by subprocess plus a `do_connect` recorder and a no-psycopg assertion.

## Files relay touches

| File | Kind |
|---|---|
| `src/hypershell/relay.py` (or `relay/` package: app, connector, store, returner) | new |
| `tests/test_relay_*.py` (DB fence, insert/stamp, outbox, env scrub, loopback) | new |
| `src/hypershell/core/config.py` (`[relay]` defaults, DB swap hook) | additive |
| `src/hypershell/__init__.py` (commands, `APP_HELP`, `__all__`) | additive |
| `src/hypershell/core/logging.py` (`DISTRIBUTED_ROLES`) | additive |
| `src/hypershell/cluster/remote.py` (consumed; optional launch-helper extraction) | **overlaps security cycle** |
| `src/hypershell/cluster/ssh.py`, `cluster/local.py` | not touched (ssh: security cycle) |
| `src/hypershell/core/dispatch.py` (optional `nudge()`; avoid with small `poll`) | additive, Z2 file |
| `src/hypershell/data/core.py`, `data/__init__.py`, `data/upgrade.py` | unchanged (reused) |
| `docs/cli/relay.rst`, `docs/_include/relay_*.rst` | new |
| `docs/cli/index.rst`, `docs/manual.rst`, `docs/_include/config_param_ref.rst`, `docs/database.rst` | additive |
| `docs/security.rst` | additive, **overlaps security cycle (its R8)** |
| `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs` | additive |
| `AGENTS.md` (D11 role location, relay footguns) | additive |
