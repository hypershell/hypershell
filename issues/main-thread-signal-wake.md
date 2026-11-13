---
status: adopted:relay-retire
kind: fix
appetite: small
lane: public
---

# A drain signal can be lost while the main thread waits on its worker

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs relay`, `hs client` and `hs server` each start their work in a thread and then block the main
thread in an untimed `thread.join()`. CPython runs a Python-level signal handler only on the main
thread. When another thread takes a process-directed signal, the C-level handler only sets a flag;
the main thread, asleep in an untimed lock wait, gets no `EINTR`, so the Python handler does not run
until something else wakes it. For these commands that can be never.

On `develop` (`8403388`):

- **`hs relay`.** `RelayApp.run` waits in `thread.join()` (`src/hypershell/relay/__init__.py:1015`).
  Its drain handler for SIGUSR1 and SIGUSR2 only sets `DRAIN` (`drain_handler`, `:133-135`;
  installed by `install_drain_handlers`, `:138-147`), and the puller reads it at its gate
  (`src/hypershell/relay/upstream.py:342-344`, "Drain requested"). If the handler never runs, the
  relay never drains.
- **`hs client`.** `run_client` waits in `thread.join()` (`src/hypershell/client.py:1518`). It
  relies on the stock handler, which records SIGUSR1, SIGUSR2 and SIGHUP for the whole process
  (`handler` and `register_handlers`, `src/hypershell/core/signal.py:96-115`, registered by `main`,
  `src/hypershell/__init__.py:143`), read by the scheduler (`client.py:224`) and each executor
  (`client.py:889`).
- **`hs server`.** `serve_from` and `serve_forever` wait in `thread.join()`
  (`src/hypershell/server.py:1376`, `:1567`), as does `serve_with_sites`, the topology-publishing
  server (`src/hypershell/cluster/topology.py:579`). The scheduler reads the stock flag for SIGUSR1
  and SIGUSR2 (`server.py:244`); log rotation reads it for SIGHUP
  (`src/hypershell/core/logging.py:417`).

On Linux a process-directed signal can be taken by a thread other than the main one, notably a
thread that recalculates its pending signals: one just created, or one forking with a changed mask
inside `subprocess`. The relay produces exactly that burst right after "Relay-local server ready"
(`relay/__init__.py:593`): its fleets start, then the heartbeat, returner and puller threads
(`:594-598`), and the autoscaler starts site clients with `Popen`
(`src/hypershell/cluster/launch.py:108-109`).

**Evidence.** A CI run on Python 3.12 (Linux), on 2026-11-13, failed
`tests/test_relay_db.py::test_relay_never_opens_postgres_primary[postgres]` with "Relay did not
exit within 60.0 seconds" after SIGUSR1 (the test's `drain`, `tests/test_relay_db.py:126-129`;
`DEADLINE`, `:47`). The relay log ended at "Autoscale adding client", with no "Drain requested"
line. On macOS the main thread took the signal every time (25 of 25 runs, and 200 of 200 under
stress), which is why it passed locally.

A deterministic reproduction: a thread inside the relay process sends itself SIGUSR1 with
`signal.pthread_kill(threading.get_ident(), signal.SIGUSR1)`. The relay hangs 5 of 5 times, both
on the `relay-topology` branch and on `develop` `22684fd`. `os.kill(os.getpid(), signal.SIGUSR1)`
from the same thread, as a control, drains every time.

## Why it was pre-existing

Pre-existing since the relay landed (#82), and in `hs client` and `hs server` before that: the
untimed join is the original pattern. It surfaced while `relay-topology` (#100) was in CI; nothing
in that cycle changed the signal path.

## Outcome / vision

Each long-running command's main thread waits with a timed join in a loop, for example `while
thread.is_alive(): thread.join(timeout=…)` and then a final `join()` that re-raises the worker's
failure (`Thread.join`, `src/hypershell/core/thread.py:52-56`, already re-raises). A handler tripped
by any thread then runs within about a second, whichever thread the kernel chose. SIGTERM and
SIGINT keep their behaviour.

## Sketch of the acceptance criteria

- **R1** — When SIGUSR1 or SIGUSR2 reaches an `hs relay` process, whichever of its threads takes
  it, the relay shall log "Drain requested" within 2 seconds and then drain and exit as it does
  today.
- **R2** — When SIGUSR1, SIGUSR2 or SIGHUP reaches an `hs client` process, whichever of its threads
  takes it, the client shall act on it within 2 seconds as it does today when the main thread
  takes it.
- **R3** — When SIGUSR1, SIGUSR2 or SIGHUP reaches an `hs server` process (with or without relay
  sites), whichever of its threads takes it, the server shall act on it within 2 seconds as it does
  today when the main thread takes it.
- **R4** — SIGTERM and SIGINT shall end each of the three commands as they do today, with the same
  exit status.
- **R5** — A failure in a command's worker thread shall still be raised by the command, with the
  same log line and exit status as today; with no signal, no command's output or exit status
  shall change.
- **R6** — A test shall fail on `develop` and pass with the fix, deterministically on Linux and
  macOS, for each of the three commands: a recorder-style variant starts a thread in the process
  that, when a trigger file appears, sends SIGUSR1 to its own thread with `signal.pthread_kill`;
  the test asserts the process exits successfully within the test's deadline.

## Notes

- **Rejected alternative, recorded so it is not re-proposed:** block SIGUSR1 and SIGUSR2 in every
  worker thread (`signal.pthread_sigmask`), so only the main thread can take them. A child process
  inherits the signal mask of the thread that spawns it, so the site clients a relay's autoscaler
  thread launches, and the task processes a client's executors start, would begin with their drain
  and interrupt signals blocked.
- The relay's SIGTERM handler is Python-level too (`terminate_handler`,
  `src/hypershell/relay/__init__.py:163-166`), so a SIGTERM taken by another thread waits on the
  same join. The timed join covers it without changing what it does; R4 holds either way.
- The same untimed join is in `run_cluster` (`src/hypershell/cluster/remote.py:85`), `run_local`
  (`src/hypershell/cluster/local.py:47`), `run_ssh` (`src/hypershell/cluster/ssh.py:64`) and
  `hs submit` (`src/hypershell/submit.py:1009`). Whether those are in scope is for shaping; one
  shared helper would cover all of them.
- Open question for `/hs-plan`: the wake interval. One second matches `STOP_CHECK_INTERVAL`
  (`src/hypershell/relay/__init__.py:120`).
- The 2.8 line has the same pattern in `hs client` and `hs server`; whether to backport is for
  shaping.
- Related: [`process-group-signal-teardown.md`](process-group-signal-teardown.md), the other signal
  seed (SIGTERM and Ctrl-C teardown), which this does not overlap.
- Related: [`relay-superseded-instance-retire.md`](relay-superseded-instance-retire.md), where a
  drain is one of the ways an operator would try to free a stuck relay.
- Found by: a diagnosis of the CI failure above (2026-11-13), during `relay-topology` (#100).
