# PLAN — A relay run ends within seconds of its last result

> **Status:** Draft for review · **Last updated:** 2026-11-21
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (fanned out under the high-blast-radius exception: `server.py`, `client.py`).
> Every design element traces to a GOAL R-ID.

## 1. Summary

Two small changes at the two waits, plus the test-only rider. **R1:** every client keeps a count of the
tasks it has received and not yet collected; when the count is zero, its collector returns what it holds
at once instead of waiting out `-w`. **R3:** a scheduler whose input has ended *arms* when a pass finds
nothing to load, noting the results landed so far and the remaining count it already reads; the
receiver, once enough results have landed to possibly finish the run, reads the counts and wakes the
scheduler (the #110 `wake`) only when nothing is left incomplete or retryable. **The GOAL's cut does not
apply:** R3 needs neither a database read per landed result bundle (about one check per run) nor any
change to the back-off while work remains (no wait is cut unless the run is over) —
[`research/02`](research/02-server-finish-wake.md). **R7** routes the relay workload scans through one
reader that skips a refused process. Prototyped in scratch, the relay tail at 2004 tasks fell from
6.75–7.45 s to 1.54 s.

## 2. Design

### R1, R2 — the client returns what it holds once it has nothing to run (`src/hypershell/client.py`)

- **`TaskCount`** (new, module-level in `client.py`): an `int` under a `threading.Lock` with `add(n)`
  (negative to lower) and a `value` read. Not public API; no new option or configuration.
- **`ClientThread.__init__`** (`:1295-1317`) makes one and passes it as `inflight=` to
  `ClientSchedulerThread`/`ClientScheduler` and `ClientCollectorThread`/`ClientCollector` (new optional
  keyword, default `None`: a machine built without it behaves exactly as today).
- **`ClientScheduler.request_bundle`** (`:225-240`): when a reply carries tasks, `inflight.add(len(tasks))`
  before UNPACK — the earliest point the client holds them.
- **`ClientCollector.get_local`** (`:391-402`): after appending a real task, `inflight.add(-1)`. The
  collector lowers the count itself, so its own read right after taking the last task already sees 0.
- **`ClientCollector.check_bundle`** (`:404-415`): after the size test and before the wait test,
  `elif self.tasks and self.inflight is not None and self.inflight.value == 0:` → TRACE `Client idle -
  returning N tasks` → PACK_BUNDLE. The `self.tasks` guard keeps an idle client from sending an empty
  bundle every second (the pre-existing empty send each `bundlewait` stays, R2).
- **What the count covers:** tasks fetched but not yet queued (`ClientScheduler.tasks`), on `inbound`,
  waiting for resources or the rate limit, running, and on `outbound` — everything "running or waiting
  to start". **R2:** while it is above 0 the new branch is skipped, so the size and wait triggers and
  `finalize`'s flush are untouched.
- **Races, all benign** ([`research/01`](research/01-client-bundling.md) § 2): a reply in flight can make
  a partial return a moment early (the mid-run case Clarification 2 accepts); an executor that gives up
  on an unkillable task or crashes leaves the count above 0, and the client falls back to today's wait.
- **Reach:** every client is a `ClientThread` — `hs client`, `hs cluster` local mode in-process, every
  launched client (`cluster/launch.py`), relay site clients. No launch-line change. The relay's own
  returner already returns at once when its site is idle (`relay/upstream.py:841`); its 5 s cadence came
  only from the held site results, so R1 alone cures cause (a).

### R3, R4 — the result that finishes a run ends the back-off (`src/hypershell/server.py`)

- **State on `Scheduler`:** `landed: int` (results written back; written only by the receiver) and
  `finishing: Optional[Tuple[int, int]]` (`(landed at the mark, remaining)` while armed, else `None`).
- **Arming in `load_bundle`** (`:244-297`), on a pass that loaded nothing: after `input_ended =
  self.submission_complete()`, if not `forever_mode` and `input_ended`, set `finishing = (landed, 0)`
  **before** the counts, then read the counts as today (`Task.count() > 0`, then `count_remaining()`
  once, then `count_retryable(...)` only when it is 0) and, armed, set `finishing = (mark, remaining)`.
  Same queries, same order, each at most once. A pass that loads tasks sets `finishing = None` beside
  its `poll`/`woken` reset.
- **`Scheduler.results_landed(n)`** (new): `landed += n`; return unless armed and `landed - mark >=
  remaining`; then `if Task.count_remaining() == 0 and Task.count_retryable(attempts,
  group=current_group, eager=eager) == 0: self.wake()`. Runs in the receiver's thread (the scoped
  `Session` gives it its own session, as `apply_results` already uses); reads scheduler attributes only.
- **Receiver** (`:598-701`): new optional `scheduler=` keyword on `Receiver`/`ReceiverThread`; after
  `update_tasks` classifies a bundle, `applied = outcomes.count(ResultOutcome.APPLIED)` and, when
  non-zero and a scheduler is given, `scheduler.results_landed(applied)`. In-memory: no scheduler, no
  call. Stale, duplicate and deferred results count only once they apply.
- **`ServerThread.__init__`** (`:1211`): `ReceiverThread(..., scheduler=None if self.scheduler is None
  else self.scheduler.machine)`. Every server path builds a `ServerThread` (`hs server`, `hs cluster` in
  every mode, the relay's local server, the topology server).
- **Why it meets R3:** the check runs no later than the finishing result (every result after the count
  is in `landed - mark`), and arming before the counts means a result either lands before the count (the
  pass stops itself) or sees the arm and wakes; the wake bumps the dispatcher generation, which ends or
  skips the wait (#110's `test_wake_before_the_wait_is_not_lost` pattern). The woken pass reads the
  counts and returns FINAL; `ServerThread` then signals the heart monitor and the clients at once.
  Measured in the prototype: landed → FINAL 0.001–0.005 s.
- **Why it meets R4:** a wake only happens when nothing is incomplete or retryable, so while work
  remains no back-off is cut and `poll`/`woken` evolve as today; `--forever` never arms; dispatch,
  retries (`Task.next` is still called only by the scheduler), group advance, eviction and hand-backs
  are untouched. The scheduler's own reads are unchanged.
- **Residual edge (§ 5):** `hs update` cancelling or deleting tasks during the last back-off lowers
  `remaining` without results; that back-off then runs out as today and the next pass re-arms.

### R7 — the scans tolerate a process that exits mid-read (`tests/test_relay_workload.py`, test-only)

- `read_process(proc)` returns `(cmdline, environ)` or `None` on `psutil.AccessDenied`,
  `NoSuchProcess`, `ZombieProcess`. `scan_site` (`:275-296`) and `scan_site_key` (`:501-523`) skip a
  `None`; their counts and "no key seen" checks are unchanged over the processes read.
- `client_environments(relay)` returns the readable site client environments (keeping an empty one, so
  a withheld environment still fails as today); `test_site_clients_keep_the_relay_config_file_...`
  (`:1058`) reads it inside `until(...)` (one client: a skipped read is retried, not an empty pass).
- `running_tasks`, `site_clients` already catch `psutil.Error`; names imported by
  `tests/test_relay_outage.py:37` keep their signatures. No product change.

### R5 — the drive and its judge (`spec/relay-shutdown-tail/drive/`)

- `tail_run.py` from `spec/relay-hardening/drive/` is run read-only on the committed head into
  `evidence/head/` (`head-2004-NN`, `head-2000-NN`), wrapped in `drive/bounded.py 300`.
- `drive/tail_bound.py` (new, standard library) judges: at most 10 records, every record at the drive's
  sizes, at least 3 complete runs at each size, every relay tail present and ≤ 3 s, one commit, no
  `src_changed`; it writes the baseline-beside-head table (`evidence/tails.md`). Dry-run at plan time:
  the baseline fails it (6.75 and 7.45 s; 2 runs at 2004), an empty `head/` fails it, bad usage exits 64.
- `evidence/baseline/` holds the shaping records (copied verbatim); `evidence/plan-probe/` the
  prototype run (not the head; README says so).

### R6 — documentation (no option, no default changes)

- `-w` help in `src/hypershell/client.py:1556` and `src/hypershell/cluster/__init__.py:87`, and the `-w`
  paragraphs in `docs/_include/client_help.rst` and `docs/_include/cluster_help.rst`: a client with
  nothing left to run returns what it holds at once.
- `docs/_include/config_param_ref.rst`: `client.bundlewait` (`:413-422`) the same; `relay.bundlewait`
  (`:765-776`) that its site clients do too; `server.poll` (`:284-298`) that the result that finishes a
  run ends the scheduler's wait. Each with the `.. only:: html` + `.. versionchanged:: 2.9.0` form used
  there. The `-Q` paragraphs (`server_help.rst`, `cluster_help.rst`) get the `server.poll` sentence.
- Prose pages: `.. versionchanged:: 2.9.0` notes in `docs/cli/client.rst` and `docs/cli/cluster.rst`
  (bundle wait) and `docs/cli/server.rst` (poll). Top-level commands only.
- Man pages (`share/man/man1/*.1`) are Sphinx output rebuilt by `/hs-release`; this cycle edits their
  sources and checks the man builder's output carries the new sentences, without committing generated
  pages (sign-off item, § 5). Completions are unchanged: no option changes.
- `AGENTS.md` and `.agents/factory/invariants.md` §3 in lockstep: the out-of-work stop of a
  non-`--forever` scheduler whose input has ended also takes effect when the result that finishes the
  run lands (the receiver wakes it), not only on the next pass.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `TaskCount`; scheduler raises it on a reply with tasks; collector lowers it per task and returns a non-empty bundle at 0 (`client.py`) |
| R2   | New branch only at count 0; size and wait triggers, `finalize` flush unchanged; `inflight=None` is today's machine |
| R3   | `Scheduler.landed`/`finishing`/`results_landed`; arm before the counts in `load_bundle`; `Receiver(scheduler=)` reports applied results; `Scheduler.wake` |
| R4   | Wake only when nothing is incomplete or retryable; never armed with `--forever`; scheduler queries and back-off arithmetic unchanged |
| R5   | `tail_run.py` runs on the head into `evidence/head/`; `drive/tail_bound.py`; `evidence/README.md`, `tails.md` |
| R6   | `-w`/`client.bundlewait`/`relay.bundlewait`/`server.poll` text in help, `_include` snippets, param reference, prose pages; man output checked |
| R7   | `read_process`, `client_environments` in `tests/test_relay_workload.py`; the injection test |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(gate #1: §3, §4, §5, §6, §7, §10, §11, §12 touched; the intent is sane — R3 is a wake on the existing
stop condition, R1 a new return trigger beside the existing ones) and again after this design:

- **§3 stop conditions** — the out-of-work stop keeps its predicate exactly: input ended (read before the
  counts), every task terminal, nothing retryable for the current group. The receiver's check reads the
  same `count_remaining`/`count_retryable` with the scheduler's own `attempts`, `current_group` and
  `eager`, and only wakes; FINAL is still decided by the scheduler's pass. The failed-group halt, the
  SIGUSR stops and the open-input back-off are unchanged. The §3 sentence "not during a back-off" is
  updated for this one stop, in `AGENTS.md` and `invariants.md` together.
- **§3 retries / back-off** — the scheduler stays the sole `Task.next` caller; a wake cannot happen while
  a retry is pending (`count_retryable > 0`). `poll`/`woken` arithmetic unchanged.
- **§2 / §1** — no new query predicate: the counts are the existing classmethods (their `CANCEL_STATUS`
  and `completion_time IS NULL` rules intact). No task-row write added.
- **§4 modes** — `in_memory` has no scheduler and the receiver gets `None`; `--forever` never arms;
  `no_confirm` is untouched.
- **§5 concurrency** — no new FSM state; no blocking call added (the `TaskCount` lock and the
  dispatcher lock in `wake()` are short critical sections); every queue op keeps its finite timeout.
  Cross-thread state is one writer per field (`landed`: receiver; `finishing`: scheduler; a tuple
  assigned whole).
- **§6 shutdown order** — unchanged on both sides: the client's sentinels and `finalize`, the server's
  submitter → scheduler → dispatcher finish → heartbeat → reverts → sentinels. The wake only makes the
  scheduler's FINAL earlier.
- **§7 resources** — not touched (`TaskCount` is separate from `core/resource.py` accounting).
- **§10 configuration** — no key, default or kind changes.
- **§11 launch** — no launch-line change; `client_launch()` untouched.
- **§12 conventions** — docs with `versionchanged:: 2.9.0` in the same phase as the help text; no CLI
  option change (no completion change); new tests tagged `unit`/`integration`; Python 3.11–3.14.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| The receiver reads two counts (about once per run) and calls into the scheduler | R3 needs something to notice the finishing result during a back-off | A wake per bundle (or a capped back-off) changes the back-off while work remains, and a count after every bundle is a read per landed bundle — both the GOAL's cut ([`research/02`](research/02-server-finish-wake.md)) |
| A shared in-flight count across the client's scheduler and collector | R1 needs "nothing running, nothing waiting"; no existing state says it race-free | Queue emptiness, executor FSM state and resource accounting each miss held or in-hand tasks ([`research/01`](research/01-client-bundling.md) § 2) |

## 4. Rabbit holes (resolved)

- **Does R3 trip the cut?** → No, with the counted arm: no read per bundle, no back-off change while work
  remains; prototype red → green ([`research/02`](research/02-server-finish-wake.md)).
- **A race-free "idle" signal in the client** → the in-flight count, lowered by the collector itself
  ([`research/01`](research/01-client-bundling.md)).
- **Can a test show R1 and R3 on the base without new API?** → yes: `LocalCluster` integration tests that
  measure landing and FINAL times, red on `f3fb4dd2` by assertion under the landed `red_check.sh`
  (dry-run: 2 of 2 RED), green on the prototype; the R7 injection test is red by `AccessDenied` there.
- **Is 3 s at 2004 tasks reachable?** → prototype run 1.54 s ([`evidence/plan-probe/`](evidence/plan-probe/)).
- **Bounding on a host without `timeout(1)`** → `drive/bounded.py` ([`research/03`](research/03-relay-tests-and-drive.md)).

## 5. Risks & open questions

- **Sign-off: the cut.** The plan judges R3 inside the appetite: the receiver's reads are gated to
  about one check per run. If the foreman reads any receiver-side count as the cut's "database read",
  the fallback is the GOAL's: drop P2, narrow R5's bound, drop R6's `server.poll` line and seed R3.
- **Sign-off: R1 changes every `hs client` with `-b` above 1** (ruled at the GOAL gate); mid-run, a client
  starved by the server returns partial bundles at once, so the server sees more, smaller result bundles
  then. The release note says so.
- **Sign-off: man pages.** Committed man pages change only in `[release]` rebuilds; this cycle checks
  the man builder's output from its sources. If the foreman wants R6's man-page clause met in-tree,
  P5 adds a `sphinx-build -b man` regeneration of `share/man/man1/` (a large generated diff).
- **Residual R3 edge:** a task cancelled or deleted with `hs update` during the final back-off; the run
  then ends when that back-off runs out (today's behaviour), and the next pass re-arms with the right
  count. Not documented to users; recorded here for the review (sign-off item if R3 is read to cover it).
- **Tests-first commit leaves the branch red** until P2–P4 land (never pushed in between).
- **Timing of the drives.** `end_drive.sh finish` relies on the back-off sequence 1, 2, 4, 8 s; the
  margins measured were ≥ 3.6 s (fixed) and the base past its bound in 3 of 3.
- **`-w` default.** `-w` takes its default from `submit.bundlewait`, not `client.bundlewait`
  (pre-existing, out of scope); R6's text must not imply otherwise.

## 6. Verification strategy

- **Red list on `f3fb4dd2`** (P1): [`drive/red.txt`](drive/red.txt), checked by the landed
  `spec/relay-hardening/drive/red_check.sh` (read-only): R1 and R3 `LocalCluster` integration tests (by
  assertion), the R7 injection test (`raises=AccessDenied`).
- **CLI drives** (`drive/end_drive.sh`, in a `temp_site`): `idle` (3 tasks, `-N 2 -b 8 -w 60 -Q 1`, ≤ 15 s;
  base killed at 15 s, prototype 1.7–1.9 s), `finish` (`sleep 9`, the result lands in the 8 s back-off,
  ≤ 13.5 s; base killed at 13.5 s 3 of 3, prototype 9.7–9.9 s), `nodb` (a guard, passes on base).
- **Unit tests** of the new pieces (red only by absence on the base): collector held / by size / by wait
  / idle / no count / finalize; scheduler raises the count; receiver wakes on the finishing result, not
  with work left, not with a retry pending, never `--forever`, not lost when it lands before the wait.
- **Suites** around each change, then the whole suite serially; docs build with `-W` and the man build.
- **R5** laptop runs judged by `drive/tail_bound.py`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
