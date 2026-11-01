# HyperShell Roadmap

HyperShell is a distributed, high-throughput many-task engine for HPC and embarrassingly parallel
workloads: you give it a flat list of shell commands, a server pulls pending tasks from a SQL
database and pushes bundles over an encrypted TCP queue to elastic clients that spawn subprocesses
and return results. This document records the work still intended — the fidelity fixes that make the
engine trustworthy at scale, and the features that extend what it can schedule and where it can
store state.

This is a **forward-looking index, not an implementation plan.** Each entry states a problem and the
intention behind solving it; the *how* belongs to `/hs-plan`. As-built architecture and the
load-bearing invariants live in [`AGENTS.md`](AGENTS.md), which stays ground truth.

Each entry's **Seed** points at an [`issues/{slug}.md`](issues/) file carrying the evidence in
enough detail that a future session does not have to re-derive it. Those are **candidates, not
contracts**: `/hs-feature` promotes one into `spec/{slug}/GOAL.md`, and that promotion is where a
human negotiates appetite, non-goals, and the R-IDs `hs-review` will grade. The `status:` field on
each issue is the guard — `unshaped` (raw deferral), `shaped` (negotiated, not yet accepted into a
cycle), `adopted:{slug}` (promoted, and retired from this index by `/hs-roadmap` once the cycle lands
on `develop`). Two further values close a deferral **without** shipping — `declined` and
`accepted-behaviour` — and those are listed under *Settled questions* below rather than as cycles.
See the deferral table in [`AGENTS.md`](AGENTS.md) for which lane a deferral belongs in.

**Order is deliberate.** Correctness and fidelity work comes first, because several items are
prerequisites the feature cycles depend on rather than merely being urgent — those dependencies are
named in each entry. Horizons are indicative; the stated dependencies are the hard constraints.

Some seeds cite maintainer-local records under `.local/` — `IMPROVEMENTS.md` for the GNU Parallel
comparison's finding ids (H\*, M\*, L\*), `timescale-ideas-transcript.txt` for the TimescaleDB notes.
That directory is gitignored: the named review and the ids are stable, the paths are not resolvable
from a clone.

---

# Part I — Task fidelity

Findings from a comparative review against GNU Parallel (2026-08), which looked specifically for
failure modes dangerous to task fidelity. Triaged rather than adopted wholesale: some items are
answered by feature work further down this file, and two were judged not to be defects at all
(see *Settled questions*).

## Bare `#` silently truncates the submitted command

Inline-tag parsing falls through to a quoting-blind `#` strip, so `echo 'issue #42'` is stored and
executed as `echo 'issue`. URL fragments, awk scripts, and hex literals corrupt the same way, with
no error at submit time. Silent corruption at ingest is the one class of bug a many-task engine
must not have, and it contradicts our own "the shell line is the contract" design — the shell
already implements comment semantics correctly.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/submit-comment-truncation.md`](issues/submit-comment-truncation.md)

## Unguarded task start kills the executor and poisons the task

An `OSError` while opening capture files or spawning the subprocess propagates out of the FSM and
ends the executor thread: declared resources are never released, the task stays in-flight until a
revert re-runs it into the same crash, and the client degrades executor-by-executor in silence.
Template and resource errors are already handled gracefully in the same file — spawn failure just
never got the same treatment.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/task-start-spawn-guard.md`](issues/task-start-spawn-guard.md)

## Result writeback is last-writer-wins over every column

A returning result rewrites *every* column from the client's wire copy of the row — including
`schedule_time`, `attempt`, `retried`, and the UNIQUE retry chain. Combined with the fact that an
evicted client is never told to stop, a stale bundle can stamp a row "done" while another client is
still running that command. Scoping the write to result columns and making it conditional on the
row's owner and in-flight state closes the corruption without needing any new transport.

*Horizon: near-term · Depends on: — · Refs: prerequisite for relay (R5/R6 inherit the return path)*
**Seed:** [`issues/writeback-scoped-conditional.md`](issues/writeback-scoped-conditional.md)

## No process-group ownership; SIGTERM and Ctrl-C tear down without cleanup

The kill escalation ladder is well built but targets only the `sh` wrapper, so walltime does not
actually bound node usage for any task that is a pipeline or backgrounds children. Meanwhile a plain
`kill` — what Slurm epilogues and most supervisors send — hits Python's default disposition, and
`KeyboardInterrupt` slips past the `except Exception` cleanup idiom. The result is orphaned compute
on every node plus guaranteed duplicate re-execution on restart. The two must be fixed together:
Ctrl-C currently kills tasks only *because* they share the client's process group.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/process-group-signal-teardown.md`](issues/process-group-signal-teardown.md)

## A run with failed tasks exits 0

The cluster entry points return nothing, so cmdkit exits 0 regardless of outcomes — every CI
pipeline, Makefile, and Slurm chain wrapping `hsx` sees success while jobs failed. The information
is in the database; the process contract discards it. Under `--no-db` it is not recorded anywhere at
all.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/run-exit-status-failure-aware.md`](issues/run-exit-status-failure-aware.md)

## Transient coordinator failures should degrade to retry, not process death

A database lock, a server restart, or a network blip is currently fatal — and takes in-flight
results with it. SQLite runs on library defaults with no WAL, no busy timeout, and no retry, so a
routine `database is locked` under submit-while-running kills the server mid-run; clients have no
reconnect path at all, so one coordinator blip terminates the whole fleet; and completed-but-uncommitted
results die with whichever process was holding them. One invariant covers all three: **a transient
failure of the coordinator path degrades to retry-with-backoff, never to process death and never to
lost results.** Clients park unsent results and redeliver on reconnect, which closes the ack window
rather than documenting it as a hazard.

*Horizon: near-term · Depends on: — · Refs: relay R9 is verbatim this machinery — build it once here
and the relay's upstream connector inherits it; shares the `data/core.py` provider seam with turso*
**Seed:** [`issues/coordinator-resilience-retry-backoff.md`](issues/coordinator-resilience-retry-backoff.md)

## Ingest robustness: all-or-nothing submission, no length awareness

One undecodable byte aborts a whole task file, and one malformed tag raises out of the loader after
part of the file is already submitted — leaving a partial, unlabelled submission with no per-line
diagnosis. A line too long to ever spawn is not caught until some node tries it.

*Horizon: near-term · Depends on: sequence after the `#`-truncation fix (same path) · Refs: —*
**Seed:** [`issues/ingest-robustness.md`](issues/ingest-robustness.md)

## Client I/O hygiene

Five small independent defects in the task-output path: binary captured output crashes retrieval on
a `UnicodeDecodeError` though the file itself is byte-faithful; a full disk truncates output while
the task records success; tasks inherit the client's stdin and race fd 0; concurrent crash logs
overwrite each other; and an uncalled transport in `core/remote.py` drops tail output and busy-spins.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/client-io-hygiene.md`](issues/client-io-hygiene.md)

## Templating and remote-env contract

`{}` splices arguments unquoted into a `shell=True` line, with no safe alternative — the `eval`
forms stay by design, but a quoted token should exist and the contract should be stated. Separately
`compile_env` builds environment assignments without shell quoting, so a value containing a space or
`$` corrupts the remote launch line, and there is no dry run to preview what would actually execute.

*Horizon: mid-term · Depends on: — · Refs: —*
**Seed:** [`issues/templating-and-remote-env-contract.md`](issues/templating-and-remote-env-contract.md)

## Deterministic sentinels retry to exhaustion, then stall the group

Both never-ran sentinels pass the failure filters, so a deterministic template error burns its whole
retry budget and then pins the group. Retry defaults also diverge between standalone and cluster
launch paths for no documented reason. The heterogeneous-fleet half of this finding — requeueing an
over-large task to a fitter node — is answered by zones, not by fleet-capability introspection.

*Horizon: mid-term · Depends on: — · Refs: zone (for the routing half)*
**Seed:** [`issues/resource-error-terminal-and-retry-defaults.md`](issues/resource-error-terminal-and-retry-defaults.md)

## Docs and runtime surfacing

Behaviour that is correct or deliberate but unannounced: `--no-confirm` silently disables orphan
recovery, captured output is stranded on client hosts under an undocumented shared-filesystem
assumption, our `{.}` means *dirname* where GNU Parallel's strips the extension (a silent
porting trap worth settling before v3 freezes the token names), the absence of input combinators is
a stance rather than an omission, and cross-host timing aggregates assume synchronised clocks.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/docs-and-runtime-surfacing.md`](issues/docs-and-runtime-surfacing.md)

## Eviction and cancellation cannot reach a running client

Two symptoms of one gap: the server has no channel on which to say anything to a client. An evicted
client is never told to stop, so a merely-partitioned node keeps executing while its tasks run
elsewhere; and `hs task update --cancel` marks rows terminal without touching the process. Both need
a server→client control path — and `zone`'s request-return dispatch surface is that seam, so this
waits rather than building one twice.

*Horizon: mid-term · Depends on: zone (dispatch RPC) · Refs: the corruption half is closed
separately by the writeback fix*
**Seed:** [`issues/evicted-client-fencing-and-cancel-reach.md`](issues/evicted-client-fencing-and-cancel-reach.md)

## `rotatedb` lost-update window

The clone-then-delete rotation concedes its own race in a source comment, and a crash between the
`VACUUM INTO` and the delete leaves rows in both files or neither. Documenting it as offline-only is
cheap now; the mechanism is reworked by the turso rotation work anyway.

*Horizon: mid-term, low priority · Depends on: — · Refs: turso R8–R11*
**Seed:** [`issues/rotatedb-lost-update-window.md`](issues/rotatedb-lost-update-window.md)

## Merged output is interleave-corruptible

Concurrent executors write to one shared file description with no line discipline, so long lines
interleave mid-line; there is no grouped, line-buffered, keep-order, or tagged mode. Real, but ranked
last of the fidelity findings on purpose: unlike GNU Parallel, merged stdout is not this tool's
product — the database and the per-task capture files are, and both are byte-faithful today.
Document the current contract now; shape the emitter later.

*Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/output-interleaving-line-atomic.md`](issues/output-interleaving-line-atomic.md)

## Failure-budget halt policy

Group-gating is the only early stop. There is no "abort after N failures", no percentage budget, and
no drain-versus-stop-now distinction, so a campaign whose first thousand tasks all fail for one
environmental reason keeps burning allocation until a human notices. Of the unscheduled items this
is the one most likely to be asked for first.

*Horizon: long-term · Depends on: the stop-now variant needs the control path; drain-only does not ·
Refs: —*
**Seed:** [`issues/failure-budget-halt-policy.md`](issues/failure-budget-halt-policy.md)

## Live progress and ETA

Watching a campaign means polling `hs list --count` against the database the scheduler is contending
on. The numbers are already computed server-side for the autoscaler's pressure signal; a periodic log
line costs almost nothing, and an `hs top`-style view is the larger version.

*Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/progress-eta-surface.md`](issues/progress-eta-surface.md)

## Head-of-line blocking on unstartable tasks

An executor that cannot acquire resources for its current task retries that same task rather than
yielding the slot, eroding throughput when declarations are mis-sized — and looking busy while it
does. Zones reduce how often this happens without fixing the mechanism.

*Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/head-of-line-blocking.md`](issues/head-of-line-blocking.md)

---

# Part II — The 2.9.0 feature cycle

Four shaped GOALs, already negotiated with dated clarifications, agreed appetites, non-goals, and
drafted R-IDs. They carry `status: shaped`: the shaping conversation happened, but acceptance into a
cycle has not. Promotion should re-confirm scope against current `develop` and cite anything that has
drifted, then adopt largely as written.

## Zone-aware demand-driven dispatch on a lighter queue primitive

Real campaigns are rarely one resource shape end to end — a screening pipeline has CPU steps, GPU
steps, and high-memory steps — and today there is no way to say "run *these* tasks on *those*
clients". Dispatch is anonymous and uniformly sized: every client pulls from one shared queue and a
4-core client gets the same bundle as a 112-core one. Zones add a user-defined placement label on
both submission and client, targeted delivery sized to the requesting client, and per-zone
autoscaling — while the homogeneous founding case behaves exactly as it does today. Underneath, the
managed queues move to the lighter threading primitive so per-client dispatch stops costing a pipe
and a feeder thread each.

*Horizon: near-term · Depends on: — · Refs: R1 (the queue-primitive swap) is independently shippable
and should land first as its own de-risking phase*
**Seed:** [`issues/zone.md`](issues/zone.md) · *status: shaped*

## Relay: two-level scheduling across administrative sites

The single shared hand-off tops out on connection fan-out (~1,000 sockets against leadership-class
systems putting ~10,000 nodes on the floor) and on administrative boundaries (one elastic fleet
cannot span centers, so there is no way to prefer cheaper capacity or survive one center going
down). `hs relay` is a per-site service that is just a client upstream and a complete little cluster
inside — roughly 100 relays × 100 nodes behind ~100 upstream sockets. Security improves at scale:
upstream credentials stop at the relay.

*Horizon: mid-term · Depends on: zone (strictly) · Refs: leans on the scoped-writeback fix and
inherits the resilience feature's reconnect/redelivery machinery as its R9*
**Seed:** [`issues/relay.md`](issues/relay.md) · *status: shaped*

## Turso provider, partition-attached listing, and S3 captured output

Rotation partitions are write-only archives today — `hs list` cannot see them, and SQLite's `ATTACH`
cap forecloses ever querying a long campaign's history as one thing. Turso holds SQLite's contracts
from our side of the code and removes that cap; the official dialect already ships, so the work is
adoption, validation, and the two capabilities it unlocks. Independently, the `[s3]` extra un-maroons
captured output from the execution host.

*Horizon: mid-term · Depends on: sequenced after zone and relay; R4's per-provider seam in
`data/core.py` shares ground with the resilience feature's pragma defaults · Refs: R13–R15 soften
relay's cross-site output non-goal*
**Seed:** [`issues/turso.md`](issues/turso.md) · *status: shaped*

## TimescaleDB provider: hypertables out of the box

The `timescale` provider aliases exist and deliver nothing TimescaleDB-shaped — the schema comes up
as plain PostgreSQL tables and the code says so at every startup. Since hypertables can partition
directly on a UUIDv7 column, our primary key *is* the partition key, which makes HyperShell unusually
well suited to it. An idempotent post-`create_all` pass converges the `task` table to a hypertable
with compression and a policy, interrogating the environment rather than assuming it.

*Horizon: mid-term · Depends on: soft — conforms to (or establishes) turso R4's per-provider seam ·
Refs: **R1 is provider-independent and cheap** — dropping the `previous_id`/`next_id` UNIQUE
constraints could land any time and unblocks the rest*
**Seed:** [`issues/timescale.md`](issues/timescale.md) · *status: shaped*

---

# Settled questions

Recorded so they are not re-raised by the next review. These carry `status: declined` or
`status: accepted-behaviour` and are **not** queue entries — read one before re-filing the thing it
describes. Work that *shipped* leaves no entry here: the code refutes a re-filing on its own, and
`spec/{slug}/` holds the account.

- **`CANCEL_STATUS = -1` overlapping death-by-SIGHUP is intended.** Reported as high-severity silent
  task loss; judged a subtly useful property instead — `kill -1` on a task node reads as *cancel this
  task*, and the semantics that follow are the wanted ones. The residue is documentation, not
  behaviour. → [`issues/stance-cancel-status-signal-collision.md`](issues/stance-cancel-status-signal-collision.md)
- **Resource gating stays declared, not measured.** Measured memory/load gates and adaptive straggler
  timeouts fit GNU Parallel's single-machine model; under a batch scheduler the node's allocation is
  already enforced externally, and pressure-driven kill-and-requeue is actively wrong against our
  new-row retry chain. Declined as standing debt, not refused forever — the conditions that would
  change the answer are written down. → [`issues/stance-declared-resource-accounting.md`](issues/stance-declared-resource-accounting.md)

# A note on security work

Security hardening is tracked separately and is **not** listed here. Fixes land as ordinary public
commits and PRs, and `docs/security.rst` discloses known limitations honestly — but a standing,
ranked inventory of unremediated weaknesses in a released version is not something to publish. See
the deferral table in [`AGENTS.md`](AGENTS.md) for how the two lanes divide.
