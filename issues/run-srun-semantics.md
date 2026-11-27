---
status: shaped
shaped-as: run-srun-semantics
kind: feature
appetite: big
target: 2.10.0
lane: public
---

# `hs run` should behave like `srun`: its exit status, its output, its signals

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim. Shaped with the maintainer
> 2026-11-26. Ships after `task-output-storage`, whose `core.io` locators it reads.

## Problem

`hs run -- CMD` is meant to feel like `srun`. The shell blocks, the command runs on some client,
and then the task's exit status and output come back as if it ran locally. The maintainer's example
is GNU Make recipes that call `hs run` under `make -j32`: Make owns the workflow and the local
concurrency, and HyperShell carries each command to remote resources. Today that does not work.
`TaskRunApp` (`src/hypershell/task.py:448-467`) is `Task.add`, then `TaskWaitApp.run()`, then two
`TaskInfoApp.run()` calls, and it never got the hardening `hs submit` received in 2.9. Audit of
`develop` a093341e (2026-11-26):

**The result is lost**
- **The exit status is never returned.** `TaskWaitApp` is built without `return_status`
  (`task.py:465`), so `hs run -- false` exits 0 and Make never sees a failure.
- **Exit codes collide.** HyperShell's own failures use 1–6 (2 for usage, 3 for configuration, 4
  for interrupt, 5 for runtime). `hs wait -r` hands a negative status to `sys.exit`, so a cancel
  (−1) becomes 255, SIGTERM (−15) becomes 241, and a template error (−1001) becomes 23
  (`model.py:301-317`).
- **Retries are not followed.** `hs run` returns on attempt 1, with its output, while the retry row
  runs unobserved (`previous_id`/`next_id`, `model.py:845-860`).
- **A cancel or a deletion ends in exit 5.** A cancelled task has no output path, and a deleted row
  raises `Task.NotFound` inside the wait loop, which has no handler.

**Output only arrives through `--capture` and SFTP**
- **Without `--capture` on the client, a successful task becomes exit 5.** `outpath` is NULL, and
  `print_file` raises `RuntimeError` (`task.py:262-263`).
- **Retrieval guesses the path.** `print_file` computes the path from the reader's own site
  (`task.py:299-312`) rather than reading the path stored on the row. When that file is missing, it
  copies over paramiko SFTP from `client_host`, with `AutoAddPolicy` and no timeout
  (`core/remote.py`).
- **Parallel runs and relays break it.** One SSH connection per `hs run` can hit sshd's
  `MaxStartups` under `make -j32`. A relay's site clients do not capture, and their hosts are
  unreachable anyway.
- **Output is replayed only at the end**: all of stdout, then all of stderr, in text mode. Binary
  output gives a `UnicodeDecodeError` and exit 5, `| head` gives a `BrokenPipeError` traceback, and
  the copies are kept forever in `lib/task/`.

**Signals and waiting**
- **An interrupt leaves the task running.** Ctrl-C exits 4, not 130, and does not cancel the task.
  SIGTERM has no handler (`core/signal.py:107-115`). SIGHUP sets a flag `hs run` never reads.
- **`hs run` can wait forever.** With no server, no client in the zone, or a `--no-db` server, it
  polls with no deadline (`task.py:394-406`). It always needs a database and has no queue mode.

**What reaches the remote task**
- **Quoting is lost.** The command is `' '.join(argv)` (`task.py:454`), so
  `hs run -- grep "a b" f` runs `grep a b f` under the client's `shell=True`. `hs submit` quotes its
  arguments.
- **The local context does not travel.** The task's working directory, environment and stdin are
  the client's: Make's cwd, its exported variables and `< file` never reach the task.
- **Missing options:** `--zone`, `-c/-m/-W`, `-g` and `--template`, all of which `hs submit` has.
  `-t` takes `nargs='+'`, so `hs run -t a:1 cmd` without `--` swallows the command.
- **Group 0 is always used.** `hs run` applies neither `default_group()` nor the reached-group
  rule. Under `--forever`, a failed group holds the others back.

**Concurrency**
- **SQLite:** no WAL and no busy timeout on the main database (`data/core.py:204, 289-295`), and
  each `hs run` calls `ensuredb()` four times.
- **PostgreSQL:** a polling `hs run` holds one connection idle in a transaction for its whole run.
- **Partitions (likely):** with rotated SQLite partitions, the second `TaskInfoApp` call repeats
  `auto_union_sqlite`'s ATTACH.

**Tests:** none runs `hs run` to completion. No test covers output replay, an uncaptured task, the
`-r` codes, a retry, a cancel, a signal or concurrency.

## Outcome / vision

`hs run -- CMD` behaves like `srun` would in a Makefile:
- it exits with the task's status, and with a code of its own, distinct from any task's, when
  HyperShell itself fails;
- it prints the task's stdout and stderr to its own, byte for byte;
- it follows the task through retries;
- an interrupt cancels the task;
- it gives up after a deadline instead of hanging;
- the command it runs is the one the shell parsed.

Fifty of them in parallel are routine.

## Sketch of the acceptance criteria

Draft R-IDs for shaping. Every number is open.

- **R1** — `hs run` SHALL exit with the final attempt's exit status when it is 0–255, 128+N for a
  task killed by signal N, and one reserved code when HyperShell itself fails or the task never ran.
  (Candidates follow GNU `timeout`/`env`: 124 for the deadline, 125 for HyperShell's own failure.)
- **R2** — WHEN a task is retried, `hs run` SHALL wait for and report the last attempt.
- **R3** — `hs run` SHALL write the task's stdout and stderr to its own, unchanged (binary included),
  through the output layer that `task-output-storage` defines, wherever the client ran: a relay
  site, another host, no shared filesystem.
- **R4** — WHEN `hs run` receives SIGINT, SIGTERM or SIGHUP, it SHALL cancel its task and exit
  128+N.
- **R5** — `hs run` SHALL take `--timeout` (a wait deadline), after which it cancels the task and
  exits 124.
- **R6** — The command SHALL reach the client as the local shell parsed it (quoted as `hs submit`
  quotes it).
- **R7** — `hs run` SHALL take `--zone`, `-c/-m/-W` and `-g` as `hs submit` does, and the same 2.9
  input checks.
- **R8** — N concurrent `hs run` processes SHALL complete on SQLite and PostgreSQL without "database
  is locked" or connections held idle in a transaction.
- **R9** — `hs wait` and `hs info` SHALL get the same fixes where they share the path (exit-status
  mapping, output, broken pipe, missing row).
- **R10** — `hs run` SHALL take `--chdir [DIR]` (the caller's working directory, or the one given)
  and `--export=VAR,…` (named variables), both opt-in. With neither, the task runs in the client's
  context as today.
- **R11** — IF `hs run`'s stdin is a pipe or a regular file (input was redirected to it), THEN it
  SHALL refuse to start (exit 2) rather than silently drop the input. A terminal, `/dev/null`, or a
  closed or invalid descriptor SHALL be accepted. Under `make -j` every job but one gets an invalid
  stdin, and CI gives `/dev/null`.

## Clarifications

- **Q:** Should `hs run` (and `hs wait -r`) exit with the task's own status, given the declined
  `run-exit-status-failure-aware`? — **A:** Yes. Exit with the last attempt's status (0–255), 128+N
  for signal N, and reserved codes in the GNU `timeout` style (125 for HyperShell's own failure, 124
  for the deadline). `hs cluster`/`hs server` keep the 2026-11-14 ruling (maintainer, 2026-11-26).
- **Q:** Should `hs run` poll the database or talk to the server? — **A:** Harden database polling
  (SQLite WAL and busy timeout; no PostgreSQL connection idle in a transaction), and fetch output
  from the row's locator through `core.io`. Server-side streaming is a later step (maintainer,
  2026-11-26).
- **Q:** What local context travels with the task? — **A:** Working directory and environment are
  opt-in (`--chdir`, `--export=VAR,…` as `srun` has), never by default. Piped stdin is refused with
  an error rather than silently ignored (maintainer, 2026-11-26). The foreman narrowed this to
  redirected input (a pipe or a file), so that Make's `/dev/null` or invalid stdin still runs (R11).

## Open questions (for shaping)

1–2, 4–6 are settled above (exit status, transport, context, stdin, order).
3. **Live output.** Replay at the end (binary-safe) for the first cycle. Live output follows when
   `task-output-storage`'s chunk channel can send while a task runs.

## Notes

- **Related seeds:** `client-io-hygiene` (binary output, stdin),
  `stance-cancel-status-signal-collision` (−1 → 255), `evicted-client-fencing-and-cancel-reach` (a
  cancel cannot reach a running task, R4), `templating-and-remote-env-contract` (cwd, environment,
  quoting), `submit-args-quote-directive`, `database-locked-during-ingest-hangs`,
  `run-exit-status-failure-aware` (declined; question 1).
- `hs task submit` (`TaskSubmitApp`) is legacy and dead; it is out of scope except for removal.
