# GOAL — A locked database delays a cluster's decision instead of ending the run; a `--no-db` cluster whose work is done never reports a failed launch

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** cluster-launch-followups
- **kind:** fix
- **appetite:** small  ·  *Two `small` seeds, both follow-ups to `cluster-launch-failures` (#107),
  promoted together on the foreman's brief (Clarifications). Each is local: how the cluster's own
  checks treat one database error (`cluster/remote.py`'s `AutoScaler`, `cluster/watch.py`), and one
  count the `--no-db` predicate reads (`cluster/watch.py`, plus a result count kept by the server's
  Receiver, `server.py`). Shaping reproduced every defect on `develop` at `9c82842d`, each
  deterministically. No new option, configuration key, wire change or doc page.*

## Problem

Two ways `hs cluster` ends a run wrongly, both in code `cluster-launch-failures` (#107) added or
leans on. #107 landed after 2.9.0a8, so most of this is unreleased; the one released part is marked
**pre-existing** below.

**A locked SQLite database ends the cluster with a traceback.** An autoscaling cluster reads the
database at every check: the client registrations behind #107's failed-launch stop
(`client_registrations()`, `src/hypershell/cluster/remote.py:383-394`, called from
`AutoScaler.count_failures`, `:666`, whenever `autoscale.failed_launches` is above 0, which it is by
default), and the task counts that size each zone (`AutoScaler.current_group`, `:595-602`;
`check_zones`, `:604-629`; `read_zone_loads`, `src/hypershell/cluster/demand.py:111`). Nothing
around those reads treats `database is locked` as transient, so the autoscaler's state machine logs
`Uncaught exception from <class '…AutoScaler'>`, writes an exception traceback file, and the cluster
stops its server and exits 5 with a second `CRITICAL … OperationalError: (sqlite3.OperationalError)
database is locked` line naming the same file. The registration read is new in #107; the sizing
reads have had the same exposure since at least 2.8.0, whose `AutoScaler.check_fixed` reads
`Client.count_connected()` and `Task.count_remaining()` at every check (**pre-existing**).

Reproduced for this shaping in a throwaway site: `hs cluster tasks.txt --no-tls --autoscaling fixed
-P 2s --launcher false`, five tasks, SQLite, with a second connection holding `BEGIN EXCLUSIVE` from
3 s to 12 s after start. With `autoscale.failed_launches` at its default the registration read
failed; with it at 0 the zone-sizing task count failed the same way. In both the run ended with
the traceback file at about 9 s, where it should have reached the failed-launch stop (default) or
kept relaunching (0). The read waited SQLite's busy timeout before failing: HyperShell sets no
`timeout` or `busy_timeout` of its own (`src/hypershell/data/core.py:203-217`), so Python's
`sqlite3` default of 5 s applies, and the failure came roughly 5 s into a check that began under
the lock. The CI failure that opened the seed (the `busy-stdin` case of
`tests/test_autoscale_failed_launches.py`, 2026-11-15) met the lock from the submitter's own
one-task-per-commit writer rather than a second process; six local runs of that test passed, so
the held lock is the deterministic stand-in.

The same exposure exists outside autoscaling, also new in #107: in launcher, `--mpi` and `--ssh`
modes, once every launched client has exited with at least one failure, the cluster asks whether
tasks remain (`work_remains` → `scheduled_work`, `src/hypershell/cluster/watch.py:106-124`), which
reads `Task.count_remaining()`. With the lock held from 0.5 s to 9.5 s,
`hs cluster tasks.txt --no-tls --launcher false` ended with exit 5, the same `database is locked`
CRITICAL line and a traceback file, instead of #107's error naming the failed client and the
address it was told.

**A `--no-db` cluster reports a failed run although every result is written.** #107's R1 stops a
cluster when every launched client has exited, at least one non-zero, and work remains; its R4
says a client exiting non-zero after the work is done must not make the run fail. Without a
database the server keeps no task rows, so `work_remains` (`watch.py:127-155`) reads the work as
remaining when the server is still running `MEMORY_SETTLE` (3 s, `:52`) after every returned
result is written and its submitter has submitted any task. A client that returns its last result
and exits non-zero before it sends its `FINISHED` heartbeat leaves the server waiting to evict it,
so the predicate cannot tell it from a client that failed with work left. #107's plan gate
accepted this as a known gap (`spec/cluster-launch-failures/PLAN.md` §2.3, plan-gate ruling 3;
recorded at `watch.py:140-142`) because the exact fix needed a result count in `server.py`, which
relay-hardening (#103) was changing; #103 has since landed (`f6a1d622`).

Reproduced for this shaping: `hs cluster tasks.txt --no-db --no-tls --launcher env --remote-exe
crashy`, where `crashy` runs a real client patched to exit 1 in place of sending `FINISHED`. All
five results were written to standard output, then the cluster exited 5 about 5 s after start
with `ClusterLaunchError: every launched client has exited with tasks remaining: …`. With a
database the same client does not trigger #107's error: the scheduler has ended, so the predicate
reads no work, and the run on `develop` exited 0 after 602 s, once the server evicted the client.
The seed's account of the pre-#107 ending holds: the same `--no-db` run on `f6a1d622` (`develop`
just before #107) also evicted the client and exited 0 after 602 s, every result written.

## Outcome / vision

A database that is briefly locked delays a cluster's decision; it never ends the run. The cluster
says once per skipped check that the database was locked, decides at its next check, and reaches
the same end it would have reached without the lock: the failed-launch stop, #107's
every-client-exited stop, or a completed run.

A `--no-db` cluster tells "every result is in" apart from "work was left" by counting, as a
database cluster does by reading rows. A client that dies after returning its last result is
warned about and the run ends as it did before #107.

## Acceptance criteria (the contract)

A locked database during the cluster's own checks:

- **R1** — WHILE `hs cluster --autoscaling` runs on SQLite, IF a database read made by an autoscaler
  check fails because the database is locked, THEN the cluster SHALL NOT end for it: no CRITICAL
  line, no exception traceback file. It SHALL log one WARNING for that check saying the database
  was locked and the check was skipped, and SHALL check again at its next period. This holds for
  every read a check makes (the client registrations behind the failed-launch stop and the task
  counts that size each zone), with `autoscale.failed_launches` at its default and at 0.
- **R2** — WHEN the lock is released, an autoscaling cluster SHALL reach the end it would have
  reached without the lock: one whose launches all fail SHALL stop with #107's failed-launch error
  and exit status 5 once `autoscale.failed_launches` launched clients in a row have exited
  non-zero, counting the ones that exited during skipped checks; one whose clients work SHALL
  complete every task, with the same output as an unlocked run, and keep serving as today
  (`--autoscaling` implies `--forever`).
- **R3** — WHEN every client `hs cluster` launched in launcher, `--mpi` or `--ssh` mode has exited
  and at least one exited non-zero, IF the cluster's read of whether tasks remain fails because the
  database is locked, THEN the cluster SHALL NOT end with a traceback. It SHALL log a WARNING that
  the database was locked, read again, and stop with #107's R1 error and exit status 5 within R1's
  10 s bound counted from the release of the lock, when tasks remain.
- **R4** — Any other error from these reads (one that is not a locked database) SHALL end the run
  exactly as it does on `develop` at `9c82842d`.

A `--no-db` cluster whose work is done:

- **R5** — WHEN every client a `hs cluster --no-db` run launched has exited, at least one non-zero,
  IF the server has written a result for every task submitted and the input has been read to its
  end, THEN the cluster SHALL NOT raise #107's every-client-exited error. It SHALL warn about each
  non-zero exit (#107 R2) and end as it did before #107: once the server ends, with every result
  written and the same exit status.
- **R6** — In `--no-db`, #107's R1 SHALL hold unchanged when work does remain: a submitted task with
  no result written, or input still being read, when every launched client has exited with at
  least one failure, SHALL stop the cluster with R1's error and exit status 5 within 10 s of the
  last exit.

Both:

- **R7** — Each of R1, R2, R3 and R5 SHALL be pinned by a regression test that fails on `develop` at
  `9c82842d` and passes on the branch. The locked cases SHALL be made deterministic by a second
  connection holding an exclusive lock across the check, not by the timing of a busy input stream.

## Non-goals (no-gos)

- **The server's own threads meeting a locked database.** The submitter's committer is
  [`issues/database-locked-during-ingest-hangs.md`](../../issues/database-locked-during-ingest-hangs.md);
  the scheduler, receiver and heartbeat monitor are unchanged. Only the cluster's own decision
  reads (the autoscaler's check and the every-client-exited check) are in scope.
- **The busy timeout and journal mode.** No change to SQLite's busy wait, to `database.pragmas`
  defaults, or to WAL. The seed's "does the busy timeout apply" is answered (yes, Python's 5 s
  default) and left as is.
- **Other databases and other errors.** PostgreSQL readers do not block on writers in this way; a
  lost connection, a disk error or any non-lock error keeps today's ending (R4).
- **A prompt end for a finished run whose client never said it finished.** R5 restores the
  pre-#107 ending, which waits out the server's eviction period (600 s by default) before exiting.
  Ending sooner once every result is in would change the database mode too; a follow-up seed if
  wanted.
- **`hs cluster` and `server.evict`.** Shaping saw a launcher-mode cluster keep the 600 s eviction
  period with `HYPERSHELL_SERVER_EVICT=12` set (`ClusterApp` passes no eviction period to the
  server, `src/hypershell/cluster/__init__.py`); pre-existing and not this cycle's.
- **Relay site fleets and `hs server --autoscaling`.** No separate requirement or drive. If the
  plan puts R1's handling in the `AutoScaler` they share, they get it too (Clarifications).
- **New options, configuration keys or docs pages.** The new WARNING lines are log output only.
- **No 2.8-line backport.**

## Clarifications

Proposed in shaping on 2026-11-16; every item marked **confirmed at the GOAL gate** needs the foreman's sign-off at
the GOAL gate before `/hs-plan`.

- **Q:** Two seeds in one cycle, and what appetite? — **A:** One cycle, `fix`, `small`, on the
  foreman's brief. Both are #107 follow-ups in `cluster/watch.py` and `cluster/remote.py` and share
  that cycle's tests and drive harness; each seed says `small` and together they stay one phase.
  If the plan finds the Receiver count needs more than a counter and a read, it says so before
  building. *(Pending.)*
- **Q:** Do the seeds hold against `develop` at `9c82842d`? — **A:** Yes in substance, both
  reproduced (Problem); every `file:line` they cite is current. Two corrections. The locked-read
  seed calls the defect unreleased; only the registration read is, while the zone-sizing reads it
  asked shaping to check fail the same way and have since at least 2.8.0. And the CI flake did not
  reproduce in six local runs, so the held lock stands in for it. `origin/develop` has moved one
  commit since (`bf03942d`, `ROADMAP.md` and two new seeds only).
- **Q:** Is the every-client-exited check (R3) in scope? The seed names only the autoscaler. —
  **A:** Proposed yes: it is the same unguarded read in the same #107 code, reproduced the same
  way, and leaving it would ship the defect in the modes #107 was mainly for. *(Pending.)*
- **Q:** How loud is a skipped check? — **A:** Proposed one WARNING per skipped check (autoscaling)
  or per failed read (R3), no traceback. A lock that outlasts a 5 s busy wait is worth seeing; at
  the default 60 s period that is at most one line a minute. DEBUG was the seed's other option.
  *(Pending.)*
- **Q:** What does R5 restore: the pre-#107 ending (wait out the eviction period, then exit as the
  server does) or a prompt end? — **A:** Proposed the pre-#107 ending only, which is what #107's R4
  promised; a prompt end is a Non-goal. *(Pending.)*
- **Q:** The Receiver count edits `server.py`, which #107's plan gate kept clear of while #103 was
  in flight. — **A:** #103 has landed (`f6a1d622`), so the seed's direction (a count of results the
  Receiver has written, compared with the tasks the submitter put on the queue) is open. Proposed
  in scope as the narrowest exact fix; the plan confirms it adds a count and changes no stop order
  (§6). *(Pending.)*
- **Q:** Relay site fleets and `hs server --autoscaling` share `AutoScaler`. — **A:** Proposed:
  acceptable for them to inherit R1's handling if the plan puts it there, with no separate
  requirement or drive; R5 of #107 (their failure behaviour) is untouched either way. *(Pending.)*
- **Left for `/hs-plan`** (not clarification markers): where the locked error is caught (per read
  or per check), how exits seen during a skipped check are carried to the next (R2), the warning
  wording, whether `MEMORY_SETTLE` stays once the count is exact, and how a locked read is told from
  other `OperationalError`s.

**Ruled at the GOAL gate** (2026-11-16): approved by the foreman, gate judge under the maintainer's
mandate. Every pending clarification is confirmed as proposed:
- two seeds in one cycle, appetite small;
- R3 is in scope: the launcher, `--mpi` and `--ssh` "tasks remain" read is #107 code with the same
  exposure;
- one WARNING per skipped check or failed read;
- R5 restores the pre-#107 ending (the eviction wait), not a prompt end;
- the Receiver result counter in `server.py` is accepted now that #103 has landed; `server.py` is
  high-blast-radius, so the plan keeps that edit minimal and the review's human gate applies as the
  rubric says;
- relay site fleets and `hs server --autoscaling` may inherit R1's handling through the shared
  `AutoScaler` without a requirement of their own.

`hs cluster` ignoring `server.evict` is already filed (`issues/config-keys-unread.md`, from
`env-config-keys`). The contract is locked; `hs-plan` designs against it.

## Evidence plan

What `hs-review` should be able to run against the branch:

- **Unit:** `AutoScaler.check` with a `registrations` callable and zone reads that raise a
  `database is locked` `OperationalError`: the check is skipped with one WARNING, the machine does
  not halt, and exits seen meanwhile still count (R1, R2); any other error still propagates (R4).
  `work_remains` with a locked read: warns and reads again (R3). The `--no-db` predicate with a
  stand-in server: results equal to submitted tasks and the submitter done → no work; fewer → work;
  submitter alive → work (R5, R6).
- **`.agents/factory/bin/temp_site.sh` drives** (a second connection holds `BEGIN EXCLUSIVE` on the
  site's database for 9 s from a few seconds after start):
  - `hs cluster tasks.txt --no-tls --autoscaling fixed -P 2s --launcher false`: exits 5 with
    "3 launched clients in a row exited non-zero", at least one "database was locked" WARNING, no
    traceback file. With `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0`: still relaunching at a bounded
    check after the lock is released, no traceback file.
  - The same with a working launcher (`--launcher env`): every task completes (`hs list`), the
    cluster still serving at a bounded check, no traceback file.
  - `hs cluster tasks.txt --no-tls --launcher false` with the lock from 0.5 s: exits 5 with #107's
    R1 error within 10 s of the release, no traceback file.
  - `hs cluster tasks.txt --no-db --no-tls --launcher env --remote-exe <client that exits 1 in
    place of FINISHED>`: still running at a bounded check well past 10 s, every result on standard
    output, no R1 error (R5). With a client that exits 1 before its first result (`--launcher
    false`): exits 5 with R1's error within 10 s (R6).
- The test suite under Python 3.11 and 3.14, including `tests/test_cluster_exit.py` and
  `tests/test_autoscale_failed_launches.py` unchanged in what they assert.

## Related materials

- Seeds: [`issues/nodb-cluster-finish-race.md`](../../issues/nodb-cluster-finish-race.md),
  [`issues/autoscale-read-database-locked.md`](../../issues/autoscale-read-database-locked.md).
- Prior cycle: [`spec/cluster-launch-failures/`](../cluster-launch-failures/GOAL.md) (#107: GOAL R1–R4;
  PLAN §2.3, §2.5 and plan-gate ruling 3; REVIEW).
- Related seed: [`issues/database-locked-during-ingest-hangs.md`](../../issues/database-locked-during-ingest-hangs.md)
  (the submitter's side of a locked database).
- Source: `src/hypershell/cluster/watch.py` (`results_written`, `scheduled_work`, `work_remains`,
  `watch_launched`), `src/hypershell/cluster/remote.py` (`client_registrations`, `AutoScaler.check`,
  `count_failures`, `current_group`, `check_zones`), `src/hypershell/cluster/demand.py`
  (`read_zone_loads`), `src/hypershell/server.py` (`Receiver`, `:598`), `src/hypershell/submit.py`
  (`LiveSubmitThread.task_count`), `src/hypershell/data/core.py` (engine and `connect_args`),
  `src/hypershell/core/fsm.py` (the uncaught-exception traceback).
