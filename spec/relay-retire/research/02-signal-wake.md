# Research 02 — Main-thread signal wake (R11–R15)

## 1. The untimed main-thread joins

All nine named sites are confirmed. Each one is `thread = X.new(...)` and then `try: thread.join()`. The worker is a `core.thread.Thread` subclass.

| Site | Command | Joined thread | Handler on failure |
|---|---|---|---|
| `relay/__init__.py:1015` (`RelayApp.run`) | `hs relay` | `RelayThread` | `except BaseException: thread.stop(); raise`, `finally: thread.release()` |
| `client.py:1518` (`run_client`) | `hs client` | `ClientThread` | `except Exception: thread.stop(); raise` |
| `server.py:1376` (`serve_from`) | `hs server FILE` | `ServerThread` | same |
| `server.py:1567` (`serve_forever`) | `hs server --forever` | `ServerThread` | same |
| `cluster/topology.py:579` (`serve_with_sites`) | `hs server --autoscaling` | `AutoScalingServer` | same |
| `cluster/local.py:47` (`run_local`) | `hs cluster` (local) | `LocalCluster` | same |
| `cluster/ssh.py:64` (`run_ssh`) | `hs cluster --ssh` | `SSHCluster` | same |
| `cluster/remote.py:85` (`run_cluster`) | `hs cluster --remote/--mpi/--launcher/--autoscaling` | `RemoteCluster` / `AutoScalingCluster` | same |
| `submit.py:1009` (`submit_from`) | `hs submit` | `SubmitThread` / `LiveSubmitThread` | same, then `return thread.task_count` |

No other main-path wait was found. Every other `.join()` or `.wait()` in `src/` runs inside a worker thread, or is already timed: `ServerThread.start` and `wait_scheduling` sleep-poll (`server.py:1150-1185`), and the relay's stop and supervision joins use `STOP_CHECK_INTERVAL` or `STOP_TIMEOUT`. `core/remote.py:173` (`RemoteProcess.run_and_wait`) is an untimed join too, but nothing in `src/` calls it. `ServerApp.run` (`server.py:1724-1776`), `ClientApp.run` (`client.py:1662`) and `ClusterApp.run` (`cluster/__init__.py:276-336`) reach these helpers with nothing else blocking on the main thread.

**Today's exit paths, when the main thread itself takes the signal:**

- **Worker failure.** `Thread.run` stores the exception, and `Thread.join` re-raises it (`core/thread.py:33-56`). The `except` clause stops the worker and re-raises. cmdkit's `Application.main` then maps the error through `exceptions` (`core/exceptions.py:137-142`, runtime_error, among others). That path gives the log line and the exit status.
- **SIGINT.** The default handler raises `KeyboardInterrupt` from inside the lock wait (EINTR, then handlers, per PEP 475). `except Exception` does not catch it, except in the relay, which catches `BaseException` and stops its worker. cmdkit logs `keyboard-interrupt: going down now!` and returns `exit_status.keyboard_interrupt` (4).
- **SIGTERM.** In every command except the relay it is not caught (invariants §8), so the kernel's default action kills the process (-15), whichever thread takes it. The join is irrelevant. In the relay, `terminate_handler` (`relay/__init__.py:163-166`) raises `RelayTerminated` in the main thread. `RelayApp.run` stops the worker. `RelayApp.main` then calls `end_by_signal` (`:180-187`), which re-delivers SIGTERM with SIG_DFL.
- **SIGUSR1, SIGUSR2 and SIGHUP.** Client, server, cluster and submit use the stock `handler` (`core/signal.py:96-115`, registered in `main`, `__init__.py:143`), which sets the sticky `RECEIVED` flag. The client scheduler ends at `request_bundle` (`client.py:224`, "Signal interrupt (SIGUSR1)", standalone only), and its executors stop on SIGUSR2 (`:889`). The server scheduler goes FINAL at `load_bundle` (`server.py:244`). SIGHUP rotates logs on the next emit (`core/logging.py:417`). Submit only records the flag. The relay's `drain_handler` (`relay/__init__.py:133-147`) sets `DRAIN`, and the puller logs "Drain requested" at its gate (`relay/upstream.py:342-344`).

Measured on this host, with an external `kill -USR1` and the main thread taking it:

- `hs client` exits 0 in 0.7 s.
- `hs relay` (empty backlog) exits 0 in 2.5 s.
- `hs server --forever` with one connected client exits 0 in 2.7 s, after "Signal interrupt (SIGUSR1)", "Signaling clients" and "Done".
- `hs server --forever` with no client ever connected does **not** exit. It logs "Done (scheduler)" and then waits, because `HeartMonitor.startup_phase` (`server.py:713`, `:889`) holds it until a client has connected. This is today's behaviour, and the server test must connect a client first.

## 2. `Thread.join`, `stop` and the invariants

`Thread.join(timeout=None)` (`core/thread.py:52-56`) calls `threading.Thread.join(timeout)` and then raises `self.__exception` if it is set. It does this whether or not the timeout expired while the thread was alive. The flag is set only at the end of `run()`, so a timed join raises at most a moment early, with the same exception object. A timed-join loop therefore keeps the failure path unchanged.

`stop(wait=True, timeout)` sets a vestigial flag and calls `self.join(timeout)` (`:46-50`).

Invariants §5 says to shut down with `stop(wait=True)` so that `join()` re-raises. §8 says: no new SIGINT or SIGTERM capture, `RECEIVED` stays sticky, and the relay carve-out is unchanged. The helper touches none of these.

## 3. The shared helper

Proposed for `core/thread.py`, exported in `__all__`:

```python
WAKE_INTERVAL: Final[float] = 1.0
"""Seconds between main-thread wakes while it waits on a worker, so a signal handler tripped by any thread runs."""

def join_waking(thread: threading.Thread, interval: float = WAKE_INTERVAL) -> None:
    """Join `thread`, waking every `interval` so Python signal handlers run; re-raises its failure (see `Thread.join`)."""
    while thread.is_alive():
        thread.join(timeout=interval)
    thread.join()
```

Each of the nine sites changes `thread.join()` to `join_waking(thread)` and keeps its `try`/`except`/`finally` as it is. Make it a function, not a change to `Thread.join` itself. The function works on plain threads and leaves the in-worker joins and `stop(wait=True)` alone, which matters because §5 calls this area untested.

**Experiment** (`$TMPDIR`, pure Python, CPython 3.11.18, 3.12.13, 3.13.16 and 3.14.6, macOS arm64, 3 runs each). The main thread joins a worker. The worker waits until the main thread's frame is inside `threading.join` / `_wait_for_tstate_lock`, then runs `signal.pthread_kill(threading.get_ident(), SIGUSR1)`, then waits for the handler's Event.

- Untimed `join()`: **hung 12 of 12** (stopped with SIGTERM by PID after 6 s).
- `while alive: join(1.0)`: **12 of 12** ran the handler at +1.01 s, and the main thread returned.

A timed acquire returns to bytecode at each timeout, and the eval breaker runs the pending handler there.

**SIGINT and SIGTERM parity** (3.11 and 3.14, main thread takes the signal): both the untimed and the timed join raise `KeyboardInterrupt` / the handler's exception at +0.31 s. A main-thread signal interrupts the wait at once and does not wait for the interval. A worker's failure propagates as before (see §2). R13 and R14 therefore hold by construction.

**Interval.** 1.0 s matches `STOP_CHECK_INTERVAL` (`relay/__init__.py:120`), and the handler runs within ≤ 1 s. R11, though, times "Drain requested", which the *puller* logs at its gate. When the main thread takes the signal, that line already waits behind an in-flight upstream request of up to `DEFAULT_REQUEST_WAIT` = 2 s (`relay/upstream.py:82`), or `ZONE_REQUEST_WAIT` = 1 s with several zones. So 1 s + 2 s can exceed R11's 2 s in the worst case.

Recommendation: `WAKE_INTERVAL = 0.5` (two wakes a second on one idle thread, negligible) for headroom. If R11's 2 s is meant to bound only the wake, use 1.0 and note the gate latency as pre-existing. Either way, the tests should assert exit within a deadline, not a 2 s stopwatch.

## 4. Tests (R15)

**Existing patterns.**

- `tests/test_relay_db.py` writes a wrapper program called `RECORDER` to `tmp_path/'hs'` (`:55-81`). Its body sits under `if __name__ == '__main__'`, because the queue manager's spawned helper imports it again. The `relays` fixture (`:154-191`) runs it in its own session with an in-process `QueueServer` upstream (`:146-151`) and `HYPERSHELL_RELAY_LAUNCHER=true`. `Relay.drain` and `wait_exit` (`:117-129`) and `kill` (`:134-144`, killpg) round it out.
- `tests/test_auth_variants.py:259-288` (`start_server` / `stop_server`) and `tests/test_dispatch_staged.py:45-63` start `hs server` and `hs client` with `Popen`.
- `conftest.py` provides `temp_site` and `isolate_environment`.

**The injector wrapper.** Write it as an `INJECTOR` source string, next to or modelled on `RECORDER`, to `tmp_path/'hs'`.

- **The name `hs` is required.** `RELAY_PROCESS` checks the basename of argv[0] (`core/config.py:482-491`). A `python -c` wrapper would make the relay re-exec itself (`bind_database`, `relay/__init__.py:1125-1146`), and the re-exec would lose the injector thread.
- **Gate.** Under the main guard, the wrapper starts a daemon thread that waits for two things: a go-file named in an env var, and the main thread parked in a join. For the second, walk `sys._current_frames()[main_ident]` for a `threading.py` frame named `join` or `_wait_for_tstate_lock`, with a `hypershell.*` caller among `run_client`, `serve_forever`, `serve_from`, `serve_with_sites`, `run_local`, `run_ssh`, `run_cluster`, `submit_from` or `run`.
- **Firing.** The thread then sleeps 0.2–1.0 s, prints a marker line to stderr, and calls `signal.pthread_kill(threading.get_ident(), signal.SIGUSR1)`.
- **Body.** It then runs `sys.exit(main(sys.argv[1:]))`.
- **Why the frame check.** It makes "main is parked when the signal lands" a precondition, not a race. With the helper in place, the main thread still sits in `threading.join`, called through `join_waking`, so the gate works in both cases.

**Per command.** Each case uses a deadline of about 20–30 s, and on timeout it fails with the log and kills the process group.

- **relay:** the `relays` fixture's `start(program=[sys.executable, injector, 'relay', …])` with the go-file present. Assert exit 0, plus "Drain requested" and the marker in the log.
- **client:** a plain `hs server --forever` (`--no-tls`, loopback), and the injected `hs client -N 1`. Touch the go-file once the server log shows "Registered client" (`server.py:778`, DEBUG). Assert that the client exits 0 with "Signal interrupt (SIGUSR1)", then stop the server.
- **server:** the injected `hs server --forever`, and a plain `hs client`. Touch the go-file after "Registered client". Assert that the server exits 0 with "Signal interrupt (SIGUSR1)" and "Done". The client leaves on the server's disconnect.

**Result on this macOS host** (Python 3.14, the worktree source, the fix simulated by having the wrapper patch `Thread.join` into the loop for main-thread untimed calls):

| Command | Current code (no fix), injected | Simulated fix, injected | Runs |
|---|---|---|---|
| client | **hung >20 s** | exit 0 in 3.1–3.2 s | 3 |
| server | **hung >20 s** | exit 0 in 2.2–5.2 s | 3 |
| relay | **hung >20 s** | exit 0 in 2.1–4.0 s | 3 |

Every process was stopped by PID (SIGTERM, then a kill fallback).

**Linux, expected.** `pthread_kill` is `tgkill`, which delivers to the named thread. CPython's C handler runs on the injector thread: it sets the trip flag and the eval breaker, and writes the wakeup fd only if one is set. The main thread, asleep in an untimed `sem_wait` (3.11/3.12) or parking-lot wait (3.13+), gets no EINTR, so it hangs the same way. This matches the reported reproduction ("hangs 5 of 5" with `pthread_kill`, while an `os.kill` control drains). CI should confirm it on Linux 3.11–3.14.

**Gaps.** None of the R15 tests cover `hs cluster` or `hs submit` (R12 only). If the plan wants evidence for them, the same injector can drive `hs cluster` (local, which embeds a server and a client). Its exit behaviour after SIGUSR1 was not measured here.
