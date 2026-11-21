# GOAL — A relay run ends within seconds of its last result

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-shutdown-tail
- **kind:** fix
- **appetite:** small  ·  *Two waits at the end of a run, each a narrow change (a client that runs out
  of work returns what it holds; a server notices the result that finishes its run), plus a
  test-only rider. Clarification 3 records where this stops being small and what is cut then.*

## Problem

**A relay run can end about 7 s after its last task, on `develop` today.** The *tail* of a run is the
time from the last task's `completion_time` in the upstream database to the upstream server's exit
(`spec/relay-hardening/GOAL.md` R8). `relay-hardening` (#103) cut it from 6.6–13.4 s to about 1.7 s
when nothing waits, and recorded two causes it left (outcome (b), `spec/relay-hardening/TECH.md` P7
and P8, `spec/relay-hardening/evidence/laptop/README.md`). Both are confirmed on `develop`
`f3fb4dd2`; both are **pre-existing** (neither is new in 2.9.0).

**(a) A site client sits on its last results for up to 5 s.** The relay launches each site client
with a result bundle of `max(relay.bundlesize, relay.num_threads)` and with `relay.bundlewait` as its
bundle wait (`src/hypershell/relay/__init__.py:318-325`, `:527`, `:538-547`, `:1150-1151`;
`relay/site.py:133-150`; `cluster/launch.py:510-511`). On `develop` the launch line reads
`hyper-shell client ... -N 8 -b 8 -w 5`. A client returns a bundle when it is full or when the wait
has passed since its previous return (`src/hypershell/client.py:404-415`), and returns a partial
bundle early only when it is shutting down (`:434-443`). So when a site client's last bundle is not
full, its results sit there until the 5 s run out, though the client has nothing left to run. While
they sit, the relay's site still holds unfinished rows, so the relay's own early return
(`relay-hardening` fix (i)) does not fire either and the relay returns on its 5 s cadence too. Any
`hs client` started with `-b` above 1 behaves the same way at the end of its work; the relay is where
it shows, because it always starts its clients with a bundle of at least their thread count.

**(b) A result landing does not wake the upstream scheduler.** With nothing left to load, the
scheduler backs off for a wait that starts at 0.5 s and doubles toward `server.poll` (30 s by default)
(`src/hypershell/server.py:118-120`, `:299-309`). The wait ends early on a cut, on new demand, or
when the input ends (`:311-334`, `:1223`), never on a landed result. A run whose last result lands
during a back-off ends only when the wait runs out: `relay-hardening`'s `laptop-04` lost 6.0 s to it
(two 4 s back-offs, over the segment's 2 s bound), and the wait can grow toward 30 s. This holds for
every server (`hs server`, `hs cluster`, and the relay-local server, whose `relay.poll` of 0.1 s keeps
it short there).

**Measured on `develop` `f3fb4dd2` during shaping** (laptop, macOS, loopback,
`spec/relay-hardening/drive/tail_run.py`, 4 site clients of 8 threads, tasks of 0.2 s; records kept in
the shaping scratch only):

| Size | Runs | Tail (s) | What waited |
|------|------|----------|-------------|
| 2000 tasks (the R8 size) | 6 | 1.59, 1.71, 1.72, 1.76, 1.77, 1.78 | nothing: every site client ran a multiple of 8 tasks (496 or 504), so no last bundle was partial; direct `hs cluster` tail 1.65–1.76 s |
| 2004 tasks | 2 | 6.75, 7.45 | one site client's last bundle of 4 results waited 5.0–5.3 s (`Bundle wait exceeded`); the relay returned on its 5 s cadence meanwhile; the upstream scheduler then saw the last 4 land 0.65 s into a back-off |

So the R8 size alone no longer shows (a) on `develop` (the seed's "three of five runs" was on the
`relay-hardening` head); a task count whose last site bundle is partial showed it in both runs. In the
2004-task runs `tail_run.py`'s segments put the hold in s2 and s4, not s1, because the held results
finished before the run's last task by `completion_time`; its tail is still right.

**Rider: a relay workload test fails when a process it scans exits mid-read.**
`tests/test_relay_workload.py::test_site_key_reaches_site_clients_in_their_environment_only` failed
once on CI (Python 3.14, 2026-11-15, on #107) with `psutil.AccessDenied` and passed on a re-run.
`scan_site_key` (`:501-523`) and `scan_site` (`:275-296`) read `cmdline()` and `environ()` of every
process the relay launched and skip only `NoSuchProcess` and `ZombieProcess` (`:285`, `:512`); on
Linux a process that is exiting can refuse the environment read with a permission error first. Both
scans already count only "environments that could be read" and the tests retry until they have seen
enough of them. `test_site_clients_keep_the_relay_config_file_without_relay_settings` reads the
site clients' environments with no guard at all (`:1058`). #113 changed how key variables are fenced
from launched processes (`cluster/launch.py`), not these helpers, which are unchanged since #103.

## Outcome / vision

A relay run ends within about 2 s of its last result whatever the task count, as a direct
`hs cluster` run does. A client that has run out of work hands back what it finished at once, and a
server notices the result that completes its run without waiting out a back-off. The relay workload
tests stop failing on a process that exits while they look at it.

## Acceptance criteria (the contract)

- **R1 — A client with nothing left to run returns what it holds.** WHEN a client holds finished
  results it has not returned and has no task running and none waiting to start, the client SHALL
  return those results within 1 s, whatever its bundle size and bundle wait. A test SHALL show it
  for a client with a bundle size above its task count and a bundle wait of at least 30 s, failing on
  `f3fb4dd2`.
- **R2 — Bundling while there is work is unchanged.** WHILE a client has a task running or waiting
  to start, it SHALL return results as today: a bundle when it reaches the bundle size, or when the
  bundle wait has passed since its previous return; and at shutdown it SHALL still return everything
  it holds.
- **R3 — The result that finishes a run ends the back-off.** WHEN a result lands on a server that is
  not in `--forever` mode, whose input has ended, and after which no task is left incomplete or
  retryable, the server SHALL begin finishing (its scheduler done and its clients told to shut down)
  within 1 s, however long its scheduler's current back-off. A test SHALL show it with the back-off
  grown to at least 8 s, failing on `f3fb4dd2`.
- **R4 — Servers otherwise schedule as today.** WHILE tasks remain to load, retry or finish, a
  server's back-off, `server.poll`, dispatch, retries, group advance and eviction SHALL behave as on
  `f3fb4dd2`; a `--forever` server SHALL never stop because a result landed.
- **R5 — The relay tail is bounded.** Laptop runs of `spec/relay-hardening/drive/tail_run.py` on the
  head, 4 site clients of 8 threads and tasks of 0.2 s, at 2000 tasks and at 2004 tasks (a partial
  last site bundle), SHALL show no tail over 3 s. Budget: at most 10 runs on the head, at least 3 at
  each size; every started run counts. Each run's record and log windows SHALL be kept under
  `spec/relay-shutdown-tail/evidence/`, with a table that sets them beside the `develop` baseline
  above.
- **R6 — The documentation says when results are returned.** The client bundle wait (`-w` in the
  `hs client` and `hs cluster` help, the manual and the man pages, and `client.bundlewait` in the
  parameter reference) SHALL say that a client with nothing left to run returns what it holds at
  once; `relay.bundlewait` SHALL say the same of its site clients; `server.poll` SHALL say that the
  result that finishes a run ends the scheduler's wait. Docs name top-level commands only.
- **R7 — Rider: the scans tolerate a process that exits mid-read.** IF reading the command line or
  environment of a process a relay launched raises `psutil.AccessDenied`, `NoSuchProcess` or
  `ZombieProcess`, THEN every scan in `tests/test_relay_workload.py` SHALL treat that process as one
  whose environment could not be read (skip it and keep scanning), including the site client
  environment read in `test_site_clients_keep_the_relay_config_file_without_relay_settings`. What
  each test asserts SHALL not weaken: the same counts and the same "no key seen" checks over the
  processes that could be read. A test SHALL inject `AccessDenied` into one process's read and show
  the scan skips it (failing on `f3fb4dd2`). Test-only: no product change.

## Non-goals (no-gos)

- **No new option, and no change to a default.** `-b`, `-w`, `client.bundlesize`,
  `client.bundlewait`, `relay.bundlesize`, `relay.bundlewait`, `relay.num_threads` and `server.poll`
  keep their meaning and defaults; only when a client with nothing to run returns, and when a
  finishing server stops waiting, change.
- **Not the rest of the tail.** The relay-local server's shutdown signal (s7, up to 0.6 s on
  `develop`) and the relay's final heartbeat (s8, 0.4–1.0 s) stay as they are; together they are most
  of today's 1.6–1.8 s. The direct `hs cluster` tail is not a target.
- **No cluster or Linux job.** The laptop runs are the evidence; `tail_cluster.sh` stays unrun.
- **No edits to `spec/relay-hardening/`.** Its drive tools and evidence are a landed cycle's record;
  this cycle uses `tail_run.py` as it is (its segment attribution, noted above, is not fixed here).
- **Not `hs submit`'s bundling** (`submit.bundlewait`), which commits to a database, not a server.
- **Not the other flaky test** in the same roadmap entry
  ([`issues/queue-limits-open-file-test-flaky.md`](../../issues/queue-limits-open-file-test-flaky.md)),
  which stays unshaped.
- **No 2.8-line backport.**

## Clarifications

- **Q:** Two seeds in one GOAL? — **A:** Yes, on the foreman's brief (2026-11-21):
  `relay-shutdown-tail` primary (slug from its stem), `relay-site-key-scan-access-denied` as a
  test-only rider (its own `kind: test`, carried under this cycle's `fix`); both marked
  `adopted:relay-shutdown-tail`.
- **Q:** Which fix for cause (a): every client returns what it holds once it has nothing to run, or
  the relay alone starts its site clients with a shorter bundle wait? — **Confirmed at the GOAL gate:** the first (R1).
  It removes the hold for every client and every bundle size with no new knob, and the bound does not
  depend on a wait. The second leaves a hold as long as the shorter wait (`-w` rounds a fraction up
  to a whole second, so at least 1 s), changes only the relay's launch line, and leaves plain clients
  with `-b` above 1 waiting at the end of their work. The first changes `hs client` for every user
  with a bundle size above 1, and also applies mid-run: a client waiting on the server for work
  returns what it holds instead of keeping it until the wait passes.
- **Q:** Appetite, and what is cut if it does not fit? — **Confirmed at the GOAL gate:** `small`, as both seeds say.
  The risk is R3, which touches every server's scheduler. IF `hs-plan` finds that R3 cannot be met
  without a database read for every landed result bundle on every server, or without changing the
  back-off for runs with work left, THEN R3 goes back to a narrowed seed and the cycle ships R1, R2,
  R4, R6 (less its `server.poll` line) and R7, with R5's bound restated as "no tail over 3 s, except
  where the upstream scheduler was backing off when the last result landed". Without R3 there is no
  honest upper bound: the back-off can reach `server.poll`.
- **Q:** The bound and the drive (R5)? — **Confirmed at the GOAL gate:** 3 s on the laptop at both sizes. The seed's R3
  ("no tail over 5 s at the R8 size") already holds on `develop` (6 of 6 runs at 1.6–1.8 s), so it
  cannot show the fix; the 2004-task size showed (a) in both runs (6.75 and 7.45 s). With (a) and (b)
  fixed, what is left is s7 and s8 (about 1–1.6 s) plus small segments, so 3 s leaves margin without
  hiding a 5 s hold. The shaping baseline above stands as the "before"; the plan need not re-run it.
- **Q:** The 1 s bounds in R1 and R3? — **Confirmed at the GOAL gate:** 1 s each, observable in a unit or harness
  test; the client already polls its finished-task queue once a second.
- **Q:** `relay-hardening`'s P8 verify (`tail_judge.py --outcome`) requires its seed to be
  `status: unshaped`; adopting it makes that landed check fail if re-run. — **Confirmed at the GOAL gate:** accept it.
  It is a landed cycle's record, not run by CI, and `/hs-roadmap` retires the seed when this cycle
  lands, which fails the same check anyway. No edit to `spec/relay-hardening/`.

Each **Proposed** answer above awaits the foreman's ruling at the GOAL gate; until then `hs-plan` does
not start.

**Ruled at the GOAL gate** (2026-11-21): approved by the foreman, gate judge under the maintainer's
mandate. Every **Proposed** answer is confirmed:
- the client-wide flush when idle (R1), a behaviour change for every `hs client` with `-b` above 1,
  stated in the release note;
- appetite small, with the defined cut: if `hs-plan` finds R3 needs a database read per landed
  result bundle on every server, or a back-off change while work remains, R3 returns to a narrowed
  seed and the cycle ships R1, R2, R4, R6 and R7 with R5's narrowed bound;
- R5's 3 s bound at 2000 and 2004 tasks;
- the two 1 s bounds;
- `relay-hardening`'s landed P8 check is allowed to fail if re-run.

R3 touches the server's scheduler (`server.py`, high-blast-radius), so the review's human gate
applies as the rubric says. This cycle targets 2.9.0. The contract is locked; `hs-plan` designs
against it.

## Related materials

- Seeds: [`issues/relay-shutdown-tail.md`](../../issues/relay-shutdown-tail.md) (primary),
  [`issues/relay-site-key-scan-access-denied.md`](../../issues/relay-site-key-scan-access-denied.md)
  (rider).
- `spec/relay-hardening/`: `GOAL.md` R8 (the tail), `TECH.md` P7 (the decision, fixes (i) and (ii))
  and P8 (outcome (b)), `evidence/laptop/README.md` and `laptop-02`, `-04`, `-08` to `-10` (the
  nine segments, s1 to s9), `drive/tail_run.py` and `drive/tail_judge.py` (the measuring tools).
- `spec/relay/drive/relay_throughput.py` (the workload `tail_run.py` loads).
- Source: `src/hypershell/client.py` (`ClientCollector`), `src/hypershell/server.py` (`Scheduler`),
  `src/hypershell/relay/__init__.py`, `src/hypershell/relay/site.py`,
  `src/hypershell/cluster/launch.py`, `docs/_include/config_param_ref.rst`,
  `docs/_include/client_help.rst`, `docs/_include/cluster_help.rst`.
- Tests: `tests/test_relay_workload.py` (`scan_site`, `scan_site_key`, `site_clients`).
- Related: #103 (relay-hardening), #109 (contended-writes, the Scheduler's `retried` mark), #113
  (env-config-keys, relay durations and key fencing).
