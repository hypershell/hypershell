# Research 01: watching launched exits, and stopping (R1, R2, R4; launcher, `--mpi`, `--ssh`)

Scope: how `hs cluster` observes its launched processes and stops. Not autoscaling, not the
"tasks remain" predicate (assumed: a callable `work_remains()`). Code read on this worktree
(equal to `develop`'s `src/`). Probes ran 2026-11-14 under `.agents/factory/bin/temp_site.sh`, Python 3.14.

## Findings (file:line)

- **Where it waits today.** `RemoteCluster.run_with_exceptions` calls `self.clients.wait()` then
  `self.server.join()` (`cluster/remote.py:311-312`). `SSHCluster` waits on each `ssh` in turn
  (`cluster/ssh.py:310-311`), then joins (`:312`). Exit statuses are never read. The address told is
  computed into a local `target` (`remote.py:288`) and not kept. `SSHCluster` keeps no host per launch.
  The hosts are the `nodelist` zipped into `self.launches` at `ssh.py:282-296`.
- **The error path.** `run_cluster` and `run_ssh` do `join_waking(thread)`, and on an exception
  `thread.stop()` **without wait**, then re-raise (`remote.py:84-88`, `ssh.py:63-67`). `Thread.run`
  stores the exception and `join` re-raises it (`core/thread.py:36-41`, `:56-60`). `ClusterApp.exceptions`
  is `get_shared_exception_mapping` (`cluster/__init__.py:272-274`). cmdkit takes the first
  `isinstance` match in insertion order (cmdkit `app.py:190-191`). `RuntimeError` comes before
  `Exception` (`core/exceptions.py:137` vs `:142`), so a `RuntimeError` subclass gets
  `handle_exception`: one `CRITICAL [hypershell.cluster] <Class>: <msg>` line with newlines
  turned into ` - ` (`exceptions.py:91-94`), and exit status 5 (`runtime_error`).
- **`ServerThread.stop(wait=True)`** (`server.py:1243-1257`) stops the submitter, scheduler,
  heartmonitor, receiver and confirm in that order, each joined, then joins itself. Each child's
  blocking op has a 2 s timeout (`DEFAULT_REMOTE_TIMEOUT`, `core/queue.py:930`; scheduler LOAD
  `server.py:252`; heartbeat `:771`; receiver `:597`). The server's own thread, released from
  `wait_scheduler`, still runs `wait_heartbeat` → `revert_staged` (`server.py:1211-1217`, `:1219-1228`)
  inside `with self.queue`. Its `super().stop(wait=True)` joins that thread, so **the staged revert
  is done before `stop` returns** (it is conditional, invariants §6). `dispatcher.finish()` is skipped
  because the heartmonitor is halted, which does not matter once no client is left. This is the existing
  abort path, and **no `server.py` edit is needed**.
- **Hazard 1: in `--no-db` mode `ServerThread.stop` hangs forever.** It also hangs with `wait=False`.
  With no client draining `scheduled` (`queuesize`=1, `bundlesize`=1 by default), the
  `QueueCommitter` loops on `QueueFull` (`submit.py:746-757`). The local `Queue(maxsize=bundlesize)`
  (`:864`) fills, and `LiveSubmitThread.stop` blocks on `self.local.put(None)` (`submit.py:909`). Its
  own run path blocks the same way after `loader.join()` (`:881`). The server thread is parked in
  `wait_submitter` (`server.py:1151`), and the scheduler is `None` in this mode (`:1125-1128`). Probe e
  hit this with 4 tasks. Today's `run_cluster` error path would also hang in `thread.stop()`. The GOAL's
  first drive (`--no-db --launcher false`) is this case. The fix belongs in `submit.py` (not high
  blast radius, and not touched by #103):
  ```python
  # LiveSubmitThread.stop: halt the committer before offering its sentinel; never block on it
  self.loader.stop(wait=wait, timeout=timeout)
  self.committer.stop(wait=wait, timeout=timeout)
  ...put_end()...
  # put_end (also used by run_with_exceptions in place of the bare put at :881):
  while self.committer.is_alive():
      try: self.local.put(None, timeout=DEFAULT_LOCAL_TIMEOUT); return
      except QueueFull: continue
  ```
  `SubmitThread.stop` (`:660`) has the same shape, but its database committer always drains, so it
  needs no change.
- **Hazard 2: the receiver can drop one queued bundle.** `stop` puts the sentinel and then halts the
  receiver (`server.py:1253-1254`). If the receiver has just taken a results bundle, the halt flag
  wins at the next transition (`core/fsm.py` `next()`), and at most one bundle (≤ bundlesize results)
  goes unrecorded. Those tasks stay incomplete (resumed by `--restart`). This only matters on the R1
  path, which already has tasks remaining.
- **Exit naming.** `Popen.returncode < 0` means the process was killed by signal `-rc`. Name it with
  `signal.Signals(-rc).name` → `signal 9 (SIGKILL)`, and fall back to `signal N` on `ValueError`.
  A wrapper shell's `128+N` and ssh's `255` are ordinary exit statuses, so report them as such.

## Probes (abbreviated)

The runner `bounded.sh SECS LOG cmd…` backgrounds the command, records `$!`, and kills that PID at
SECS. Launched clients write their PIDs to a file and are killed by PID too. Nothing was left running,
and the ports were checked free afterwards.

| # | Command (in temp site, `seq 4 > t.in`) | Observed |
|---|---|---|
| a | `hs cluster t.in --no-db --no-tls --launcher false` | Only `Server listening on 0.0.0.0:…`; still running at 12 s (killed). |
| b | same with `--launcher true` | Identical: waits (killed at 12 s). The GOAL-gate ruling keeps this. |
| c | `… -H 127.0.0.1 --launcher env` (also a recording exec launcher), `--no-db` and DB | Exit 0, 4 tasks run. **Cluster exits ~3.2 s after the last task output** in both modes. |
| c′ | default bind (`0.0.0.0`) with an `env`-style launcher | The client dies: `CRITICAL [hypershell.client] HostAddressInfo: Could not resolve host '<host's own name>'`. The cluster hangs (an R1 case in its own right). **Drives here must pass `-H 127.0.0.1`.** The host's own name does not resolve inside the agent sandbox. |
| d1 | stub ssh, `SSH_STUB_FAIL='hostA hostB'`, `hs cluster t.in --no-tls --ssh hostA,hostB` | Two `stub refused` lines from the stub, nothing from hs; hangs (killed at 12 s). |
| d2 | same, `SSH_STUB_FAIL=hostB`, `SSH_STUB_DIAL=127.0.0.1` | Exit 0 in 3 s, 4 tasks done. **No warning for hostB** (the R2 gap). |
| d3 | no failures | Exit 0 in 3 s, 4 tasks. |
| e | in-process `RemoteCluster(launcher='false', bind=127.0.0.1)`, launcher exited, then `server.stop(wait=True)` | DB: **2.9 s** (scheduler 0.9, heartmonitor 2.0, the rest ~0). `--no-db`: **hangs** (Hazard 1, stack at `submit.py:909`). `--no-db` with the fix above simulated: **4.9 s** (submitter 2.9, heartmonitor 2.0). |

`--ssh` takes **one** argument (`nargs='?'`, `cluster/__init__.py:192`). Write `--ssh hostA,hostB`, not
`--ssh hostA hostB` as the GOAL's evidence plan has it. `--remote-exe` defaults to
`sys.argv[0].rstrip('x')` (`cluster/__init__.py:202`). Under both `uv run --no-sync hs` and
`.venv/bin/hs` it resolved to the absolute `.venv/bin/hs`, so drives need no `--remote-exe`.

**Stub `ssh`** (put its directory first on `PATH`). It runs the remote `sh -c` script locally, and the
script reads the key from the stub's untouched stdin:

```sh
#!/bin/sh
# Stub ssh for drives: skip ssh options, take the host, run the remote command on this host.
# A host named in SSH_STUB_FAIL (space-separated) exits 255, as ssh does for an unreachable host.
# SSH_STUB_PIDS, when set, names a file each launched client PID is appended to.
while [ $# -gt 0 ]; do
  case "$1" in
    -[BbcDEeFIiJLlmOoPpQRSWw]) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
host=$1; shift
case " ${SSH_STUB_FAIL:-} " in
  *" $host "*) echo "ssh: connect to host $host port 22: stub refused" >&2; exit 255 ;;
esac
[ -n "${SSH_STUB_PIDS:-}" ] && echo $$ >> "$SSH_STUB_PIDS"
cmd=$*
# SSH_STUB_DIAL replaces the address the client is told (develop always tells --ssh clients the
# host's own name; after R6, `-H 127.0.0.1` makes this unnecessary).
[ -n "${SSH_STUB_DIAL:-}" ] && cmd=$(printf '%s' "$cmd" | sed "s/ client -H [^ ]* / client -H $SSH_STUB_DIAL /")
exec /bin/sh -c "$cmd"
```

## Recommended design

1. **One shared watcher**, for example `cluster/launch.py::watch_launched(server, launched, work_remains, advertised, interval=WATCH_INTERVAL)`.
   `launched` is a list of `(name, Popen)`: `[(launch.describe(), proc)]` in launcher and `--mpi`
   modes, `[(host, proc) …]` in `--ssh`. `RemoteCluster` and `SSHCluster` call it in place of their
   waits.
   ```python
   statuses = {}
   while server.is_alive():
       for name, proc in launched:                       # R2: each exit as it happens
           if name not in statuses and (rc := proc.poll()) is not None:
               statuses[name] = rc
               if rc != 0: log.warning(f'Client {name} exited with {describe_exit(rc)}')
       if (len(statuses) == len(launched) and any(statuses.values())
               and work_remains()):                     # R1 is a state: re-checked every tick
           server.stop(wait=True)                        # joined: staged revert done
           raise ClientsExited(...)                      # RuntimeError subclass → CRITICAL + exit 5
       server.join(timeout=interval)                     # re-raises a server failure at once
   for name, proc in launched: ...wait, warning on non-zero (R4: warned, never fatal)
   ```
   Key `statuses` by index, not name, since two hosts can be the same. The loop exits only when the
   server ends: a normal finish (R4, today's status), or an all-zero fleet waiting as today (b, and a
   `--forever` with nothing pending). After the loop it waits for the remaining clients, as
   `clients.wait()` does today.
2. **Interval 1 s** (`WATCH_INTERVAL`; `WAKE_INTERVAL`=0.5 s also works). Budget from the last exit:
   ≤1 s to see it + `work_remains()` + stop (2.9 s DB / 4.9 s no-db) + ≤0.5 s `join_waking` ≈ **5–7 s
   < 10 s**. That leaves ~3 s for any settle the predicate brief needs. R2 lands within 1 s.
3. **Raise in the cluster thread, after `server.stop(wait=True)`.** `run_cluster`/`run_ssh` stay
   unchanged. Guard `RemoteCluster.stop`/`SSHCluster.stop` with `if self.server.is_alive()` so the
   follow-up `thread.stop()` does not log the `Stopping` warnings twice. The R1 message is one line
   (it is ` - `-joined anyway) and names: every launched client exited with tasks remaining; each
   name and its `describe_exit`; the advertised `host:port` (keep `target` and `nodelist` as attributes).
4. **Fix `LiveSubmitThread` in `submit.py`** (Hazard 1). Without it the `--no-db` R1 drive hangs. Add a
   unit test that stops a `ServerThread(in_memory=True)` with more tasks than the queue holds.

**Rejected:** a thread per Popen or `Popen.wait(timeout)` in turn (more threads; sequential waits break
R2 under `--ssh`). `SIGCHLD`/`waitpid(-1)` (handlers run only on the main thread, conflict with
`core/signal.py`, and could reap the autoscaler's children). A server-side "no clients" stop
(touches `server.py`/`HeartMonitor`, #103). A partial stop that halts only the scheduler and heartmonitor
and lets the server thread drain the receiver (avoids Hazard 2 at the same latency, but reaches into
`ServerThread` internals, and in `--no-db` the scheduler is `None` and the thread is parked in
`wait_submitter`). Raising from `run_cluster` in the main thread (it does not own the Popen handles).

## Tests to extend

- `tests/test_cluster_launch.py` (integration; its stand-ins run in their own session with group-kill,
  `run_in_session` `:298-325`). `test_launcher_mode_without_environment_names_the_cause` (`:466`) and
  `test_ssh_mode_without_key_names_the_cause` (`:774`) end today's hang with `until=`. After R1 the run
  exits by itself, so strengthen both to assert `status == exit_status.runtime_error` and the R1 line.
  The stand-in ssh requires `-H <HOSTNAME>` exactly once in the remote command (`SSH_BODY`, `:116-140`).
  R6 breaks that once a `-H 127.0.0.1` run advertises the bind, so the address brief must update it.
- New unit tests, no network: `watch_launched` with `Popen(['sh','-c','exit 3'])`,
  `['sh','-c','exit 0']`, `['sh','-c','kill -9 $$']` and `['sh','-c','sleep 0.5; exit 1']`, plus a fake
  server (an object with `is_alive/join/stop` over an `Event`) and a stub `work_remains`. Cases: all
  non-zero with work → `stop(wait=True)` called and `ClientsExited` names each status and the address;
  all zero → still waiting at a bound; mixed with no work → no raise, warnings only; a late exit →
  warned while the others run; `-9` → `signal 9 (SIGKILL)`.
- Cluster-level: `RemoteCluster(launcher="sh -c 'exit 3'", bind=('127.0.0.1', port), in_memory=…)`. The
  argv becomes `sh -c 'exit 3' <remote_exe> client …`, which exits 3. Run it both with and without a
  database, and join with a timeout to catch Hazard 1. For `SSHCluster`, put a stand-in `ssh` on
  `PATH` (the stub above, or the suite's `SSH_BODY`).
