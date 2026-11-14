# Research digest — cluster-launch-failures

Four briefs, run in parallel on `9d1e1c5` (= `develop`'s source). Each decision below is the one
`PLAN.md` builds on; where briefs differ, the resolution is stated.

| Brief | Topic |
|---|---|
| [01](01-exit-watch-and-stop.md) | Observing launched exits; stopping the server; exit status (R1, R2, R4) |
| [02](02-tasks-remain.md) | The "tasks remain" predicate, with and without a database; the last-results race |
| [03](03-autoscale-failed-launches.md) | R3's counter, its registration signal, R5 for relay fleets, the config key |
| [04](04-advertised-address.md) | One advertised address (R6, R7), TLS names, the R8 docs inventory |

## Decisions

1. **One watch for launcher, `--mpi` and `--ssh`.** A shared helper polls every launched `Popen` about
   once a second while the server thread lives, warns at each non-zero exit as it happens, and once
   every process has exited with at least one failure, asks the predicate (2) at each tick. It
   replaces `clients.wait(); server.join()` (`remote.py:311-312`) and SSH's one-at-a-time waits
   (`ssh.py:310-312`). A negative return code is named `signal N (SIGNAME)`. Rejected: a thread per
   process, `SIGCHLD`/`waitpid(-1)`, a server-side "no clients" stop (01).
2. **"Tasks remain" = the server would otherwise wait forever** (02). A cluster's key is minted per
   run, so once its processes are gone no client can ever connect.
   - *Barrier first* (both modes): while the Receiver lives, put an empty bundle
     (`serialize_tasks([])`) on `queue.completed` and wait, bounded, until it is taken and
     `receiver.machine.held` is empty. The Receiver calls `task_done()` before its write
     (`server.py:596-598` vs `:619`), so the queue counters are not a barrier; the marker is (probe:
     400 unwritten results at client exit with `qsize=0`, `unfinished_tasks=0`).
   - *With a database:* the scheduler thread is alive, and `Task.count_remaining() > 0`, else
     `Task.count_retryable(attempts, group=scheduler.current_group, eager)` > 0 — the scheduler's
     own stop rule (`server.py:262-266`) without its `--forever` guard, counted in the same order —
     on two reads about 2 s apart. A scheduler that has ended (finished, failed group, signal)
     defers to today's path. Never `group=None` (old retries would read as work under `--forever`).
   - *Without a database:* the server is still alive about 3 s after the barrier, and its
     `LiveSubmitThread` is alive or has submitted any task. "Alive" alone is wrong: an in-memory
     server with an empty source and no client never ends today.
   - Re-checked every few seconds while the server lives, so `--forever`/`--restart` fire on a
     task submitted after the clients left (R1 as a state), and a `--forever` cluster with nothing
     pending keeps waiting (R4).
   - Placement: cluster code composing existing `Task` classmethods (precedent: `ZoneLoad.work`, the
     autoscaler's reads of `scheduler.machine`). No `server.py`, `core/dispatch.py` or
     `data/model.py` edit. Fallback if review wants it on `Task`: `Task.count_pending` after
     `count_retryable_by_zone` (clear of #103's hunks).
3. **Stop and status.** On R1 the cluster thread calls `server.stop(wait=True)` (joined; the staged
   revert still runs in the server thread, `server.py:1211-1228`), then raises one `RuntimeError`
   subclass, which `ClusterApp`'s shared mapping logs once at CRITICAL and turns into
   `exit_status.runtime_error` (5). `stop()` skips a server no longer alive, so `run_cluster`'s
   second `thread.stop()` repeats nothing. Measured stop: 2.9 s with a database.
4. **`LiveSubmitThread` must stop without blocking** (01, hazard 1). In `--no-db` its `stop()` and its
   run path block forever on `self.local.put(None)` (`submit.py:909`, `:881`) while no client drains
   the queue, so the GOAL's first drive would hang in `server.stop`. Fix in `submit.py` (not high
   blast radius; not in #103): stop the committer first; offer the sentinel with a timeout only while
   the committer lives. Simulated: `--no-db` stop 4.9 s.
5. **Timing.** From the last exit: ≤1 s tick + barrier (~1.5 s) + second read (2 s, database) or
   settle (3 s, no database) + stop (2.9 s / 4.9 s) ≈ 7–10 s. Tight without a database; P2 measures
   it in a test and trims the settle or the submitter's stop wait if needed.
6. **R3 registration signal** (03): `Client` rows stamped with this process's `INSTANCE` — `ever` (all
   of them, monotonic) and `now` (not disconnected). The run resets when `now > 0` or `ever` rose
   since the last check. Rejected: `Client.count_connected()` (reads `Task.latest_server()`, which can
   be an earlier server's stale rows), dispatcher demand and `HeartMonitor.beats` (forget a client
   that left; the latter is #103's). Read through `Client.query()` from cluster code (no model edit);
   a `Client.count_registered` classmethod is the alternative, for sign-off.
7. **R3 counter and stop.** In `AutoScaler.check`, after `clean()` (which now returns the exited
   processes): a registration clears the run; then in launch order, exit 0 clears it and a non-zero
   exit adds `(pid, zone, status)`. At the limit, `exhausted = True` and `FINAL`, before anything is
   launched. `AutoScalingCluster.run_with_exceptions` reads the flag after `autoscaler.join()`, stops
   the server, raises (3). Rejected: raising from `AutoScalerThread` (the relay's `zone_fleet` threads
   are that class).
8. **R5 by construction.** `AutoScaler`/`AutoScalerThread` default `failed_launches=0`; only
   `AutoScalingCluster` defaults to `DEFAULT_AUTOSCALE_FAILED_LAUNCHES` (3). `site_scaler` copies an
   explicit list without it, `zone_fleet` never passes it, so every relay fleet is at 0 with no relay
   edit; tests pin it. `hs server --autoscaling` (`cluster/topology.py`) has no `AutoScaler`.
9. **The environment variable needs a reader** (03): cmdkit splits every underscore, so
   `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES` lands at `autoscale.failed.launches`. A
   `configured_failed_launches()` reader prefers that path when it came from the environment, else
   `autoscale.failed_launches`, and refuses a bool, non-integer or negative value with
   `ConfigurationError` naming its source. The same defect in other underscored keys is pre-existing
   (a seed, not this cycle).
10. **One address rule** (04): `cluster/launch.py::advertised_host(bind, advertise=None)` — `advertise`
    if given; `HOSTNAME` for `0.0.0.0` or `''`; else the bind. Four call sites
    (`remote.py:288`, `:881`, `ssh.py:283`, `relay/site.py:172` without override). `::` never reaches
    it (the listener is IPv4 only). A default relay binds `0.0.0.0`, so R6 changes no default relay.
11. **`--advertise` / `server.advertise`.** Key declared `PARAM_UNSET`; option default `None` (so only
    the option is refused on a local cluster, exit 2); `ClusterApp` resolves option > key and passes
    `advertise=` to the constructors, which never read config (a relay's site cluster must not pick
    up the key).
12. **TLS.** The dialed name is checked only in system-CA mode (no `cafile`, no fingerprint, not
    insecure); with `cafile` plus `servername` the servername is checked; under `<auto>` none is.
    The `<auto>` certificate carries the FQDN, short name, `localhost`, `127.0.0.1`, `::1`. Docs say
    so in one sentence; no TLS behaviour changes.
13. **Docs.** Hand-maintained `docs/_include/cluster_*.rst`, `docs/cli/cluster.rst` markers,
    `config_param_ref.rst` (outside #103's `.evict` and `[relay]` blocks), `docs/security.rst`,
    `docs/_include/relay_help.rst:42` (not in #103's diff), bash completions `hs` and `hsx` (identical
    copies) and zsh `_hs`. Man pages are rebuilt only by `hs-release`; this cycle meets R8's man page
    through the snippets the release build includes.

## Drive facts (all briefs)

- This host's own name does not resolve inside the sandbox, so every drive binds or advertises
  `127.0.0.1`/`localhost`; a client told the host name dies with `HostAddressInfo` (an R1 case).
- `--ssh` takes one argument: `--ssh hostA,hostB` (the GOAL's evidence plan writes them apart).
- `--remote-exe` resolves to the absolute `.venv/bin/hs` under `uv run`; no override needed.
- Stopping `uv run` with SIGKILL can orphan its Python child: stop the process tree by PID.
- Two existing tests end today's hang with `until=` (`test_launcher_mode_without_environment_names_the_cause`,
  `test_ssh_mode_without_key_names_the_cause`); after R1 they should see exit status 5.
