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

Some seeds cite a maintainer-local record under `.local/`: `IMPROVEMENTS.md`, for the GNU Parallel
comparison's finding ids (H\*, M\*, L\*). That directory is gitignored: the named review and the
ids are stable, the path is not resolvable from a clone.

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

## Date-like task arguments are turned into datetimes on the wire

The bundle decoder converts any string that parses as an ISO date into a datetime, whatever field
it is in. A task whose argument line is `2026-11-02` or `20261102`, an ordinary `hsx` input, reaches
the client as a datetime, template expansion fails on it, and the task is recorded as a template
error without running. Decoding by declared column type, not by guessing, removes the class.

*Horizon: near-term · Depends on: — · Refs: found by `writeback-scoped-conditional`'s research*
**Seed:** [`issues/date-like-args-coerced.md`](issues/date-like-args-coerced.md)

## Unguarded task start kills the executor and poisons the task

An `OSError` while opening capture files or spawning the subprocess propagates out of the FSM and
ends the executor thread: declared resources are never released, the task stays in-flight until a
revert re-runs it into the same crash, and the client degrades executor-by-executor in silence.
Template and resource errors are already handled gracefully in the same file — spawn failure just
never got the same treatment.

*Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/task-start-spawn-guard.md`](issues/task-start-spawn-guard.md)

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
rather than documenting it as a hazard. The end goal is that any node (server, client or relay) can
be bounced without bringing the system down and recovers gracefully, behind one retry/backoff
mechanism; not before SC26.

*Horizon: after SC26 · Depends on: — · Refs: relay built the first instance of this machinery (#82:
`core/reconnect.py`, the relay's upstream link); lift it into the shared mechanism here; shares the
`data/core.py` provider seam with turso*
**Seed:** [`issues/coordinator-resilience-retry-backoff.md`](issues/coordinator-resilience-retry-backoff.md)

## A client cut off partway through connecting hangs until evicted

A client opens several queue connections as it starts. When the server accepts some and drops a
later one, the thread that lost its connection dies with `EOFError`, but the client process keeps
running and holds its confirmed tasks until the server evicts it (600 s by default). The client
should exit promptly and non-zero so its tasks are recovered, or retry the connection within a
bound. Seen at the open-file limit, where the server refuses new connections, with the same client
behaviour on `b819de6`.

*Horizon: near-term · Depends on: — · Refs: coordinator resilience (a reconnect path may subsume
it); `spec/zone-queue-primitive/` (review cycle 2, C3)*
**Seed:** [`issues/client-partial-connect-hang.md`](issues/client-partial-connect-hang.md)

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

*Horizon: mid-term · Depends on: — · Refs: zones, shipped by `zone-dispatch` (#78), for the routing
half*
**Seed:** [`issues/resource-error-terminal-and-retry-defaults.md`](issues/resource-error-terminal-and-retry-defaults.md)

## A restarted run stops before a pending retry

`--restart` into a database whose only unfinished work is a retryable failure stops at START,
because the "anything left?" check counts only incomplete rows. The retry is never made and the run
exits 0. LOAD now holds a run open for a pending retry; START does not. Pre-existing, and found by
the review of the group-advance fix.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-retry-gate/` (review P1)*
**Seed:** [`issues/restart-pending-retry.md`](issues/restart-pending-retry.md)

## Eager mode can bundle the same retry twice

With eager retries and room left in the bundle, `Task.next` lists each new retry row twice: it
creates the retry, then tops the bundle up with unscheduled rows, and the fresh retry is one of
them. A client may receive the same task twice in one bundle. Whether it then runs twice is
untested; the seed asks for that test first.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-retry-gate/`*
**Seed:** [`issues/eager-retry-double-bundle.md`](issues/eager-retry-double-bundle.md)

## Refuse a submission below the group the schedule has reached

A `hs submit` into a group lower than the one an active schedule has already reached cannot keep
the documented ascending order. After `group-advance-strands-lower-group`, such tasks run late with
a warning. The maintainer wants them refused at submit time instead, all or nothing and with an
error naming both groups. The same rule covers a task file run onto an advanced database
(`--restart`, `--update`, `--repeat`). Pipes into `hs cluster`/`hs server` stay best effort. This is
a behaviour change, not a defect fix, so it ships on `develop` only (2.9).

*Horizon: near-term · Depends on: `group-advance-strands-lower-group` (landed, #74) · Refs: overlaps
`issues/ingest-robustness.md` (same ingest path)*
**Seed:** [`issues/submit-refuses-lower-group.md`](issues/submit-refuses-lower-group.md) · *status: shaped*

## A run fed by a pipe can stop before the pipe ends

`hs cluster` and `hs server` given stream input can reach FINAL and exit 0 while the stream is
still feeding tasks. Once the first bundle is scheduled, the stop branch no longer asks whether the
submitter is still reading, so a pause in the input longer than the committed work ends the run
and later tasks are never scheduled. `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1` runs only
`echo a`. File input is immune once it is submitted in full first. The likely fix is one conjunct
on `develop`; 2.8.x lacks the submitter reference it needs. Pre-existing, and found by the plan of
the group-order fix.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-strands-lower-group/` (PLAN §5 risk 1)*
**Seed:** [`issues/stream-run-stops-before-input-ends.md`](issues/stream-run-stops-before-input-ends.md) · *status: unshaped*

## An empty task file never finishes

`hsx` given an empty task file on an empty database never exits: the server logs `Empty database -
waiting for initial tasks` and waits for tasks that cannot arrive. The stop branch of
`Scheduler.load_bundle` needs at least one task in the database, so a run that submits none on an
empty database never reaches FINAL. The same holds before and after
`group-advance-strands-lower-group`. Pre-existing, and found by that cycle's review.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-strands-lower-group/` (REVIEW cycle 1)*
**Seed:** [`issues/empty-task-file-hangs.md`](issues/empty-task-file-hangs.md) · *status: unshaped*

## A "database is locked" error while a task file is read hangs the run

On SQLite, when another connection holds the database locked while `hsx` or `hs cluster` submits a
task file, the submitter's committer dies with `database is locked` and the run waits until it is
killed. The loader then blocks on its full queue, so the server never gets past waiting for the
submitter, and nothing passes the committer's error on to the exit status. The same holds before
and after `group-advance-strands-lower-group`. Pre-existing, and found by that cycle's review.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-strands-lower-group/` (REVIEW cycle 2)*
**Seed:** [`issues/database-locked-during-ingest-hangs.md`](issues/database-locked-during-ingest-hangs.md) · *status: unshaped*

## A non-integer task group is accepted at submit time

The inline `group:` tag, `-t group:…` and a `--from-json` record's `group` key accept any value.
SQLite stores a non-integer one as text in the integer column, while PostgreSQL rejects it at
insert. 2.8.3 runs text groups after every integer group. `group-advance-strands-lower-group`
needed two guards in the scheduler against them, and `group:nan` is stored as NULL, which kills
the committer on `NOT NULL` and hangs the run, before and after that fix. The maintainer wants an
integer group enforced at submit time: a whole file read ahead and refused before anything is
inserted, and on a pipe an error on just the offending line. This is a behaviour change, and
whether it ships in a 2.8.x patch or 2.9 only is open. Pre-existing, and found by that cycle's
review.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-strands-lower-group/` (REVIEW
cycles 2 and 3); same committer death as `issues/database-locked-during-ingest-hangs.md`*
**Seed:** [`issues/group-tag-integer-validation.md`](issues/group-tag-integer-validation.md) · *status: unshaped*

## Concurrent first submits can collide on the reserved source row

`Source.reserved` gets or creates the fixed `<direct>`/`<stdin>` row with no conflict handling, so
`hs submit`s started together against an empty `source` table can both insert it, and one exits
rc=5 with a traceback and its tasks unsubmitted. Seen in 1 of 20 runs on `develop` and 2 of 10 on
an upgraded 2.8.3 database, which starts with an empty `source` table. Pre-existing, and found by
the review of the schema-upgrade fix.

*Horizon: near-term · Depends on: — · Refs: `spec/schema-upgrade-step/` (review cycle 2)*
**Seed:** [`issues/source-reserved-race.md`](issues/source-reserved-race.md)

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
elsewhere; and `hs update --cancel` marks rows terminal without touching the process. Both need
a server→client control path — and `zone-dispatch`'s request-return surface (#78) is meant to be
that seam, so this waited rather than building one twice.

*Horizon: mid-term · Depends on: `zone-dispatch`'s request path (landed, #78) · Refs: the corruption
half was closed separately by `writeback-scoped-conditional` (#70)*
**Seed:** [`issues/evicted-client-fencing-and-cancel-reach.md`](issues/evicted-client-fencing-and-cancel-reach.md)

## A task whose confirmation has not landed is invisible to orphan recovery

Orphan recovery finds an evicted client's rows by `client_id`, which only the client's confirmation
sets, and confirmations are not ordered against eviction. A client evicted before its confirmation
is applied leaves its rows in flight with a NULL `client_id`, never reverted or redispatched; a late
confirmation then names the already-evicted client. Either way the row waits until restart.

*Horizon: mid-term · Depends on: — · Refs: `writeback-scoped-conditional` (#70, same token); relay*
**Seed:** [`issues/stale-confirm-hides-orphan.md`](issues/stale-confirm-hides-orphan.md)

## Orphan recovery can discard a result that lands while it runs

Reverting an evicted client's tasks reads the in-flight rows, then reverts them by id in a separate,
unconditional statement. A legitimate result committed in between is wiped and the task runs again,
with no sign to the operator. Making the revert conditional on the row still being in flight closes
it.

*Horizon: mid-term · Depends on: — · Refs: `writeback-scoped-conditional` (#70, same shape); resilience*
**Seed:** [`issues/orphan-revert-loses-result.md`](issues/orphan-revert-loses-result.md) · *status: adopted:relay-hardening*

## Contended Scheduler and `hs update` writes fail on a TimescaleDB hypertable

On TimescaleDB 2.29+, a `task`-row UPDATE that waited on another writer's row lock matches 0 rows
although the row still qualifies. The server's Confirm, revert and result writes now re-run in that
case; the Scheduler's dispatch stamp and `retried` mark, `Task.cancel_all` and every `hs update`
write do not. A contended stamp or mark exits the server mid-run (a late confirmation is an
in-server contender), and an unmarked failed row would be retried twice; `hs update` reports a
false "not found", commits partially, or silently skips rows.

*Horizon: near-term · Depends on: — · Refs: hypertable-update-retry (the pattern); resilience*
**Seed:** [`issues/hypertable-contended-scheduler-and-cli-writes.md`](issues/hypertable-contended-scheduler-and-cli-writes.md) · *status: unshaped*

## A relay can abort when its own server meets a row the relay removed

A relay deletes rows under its own server: completed rows leave for its outbox, and rows not yet
scheduled are dropped when the upstream server restarts. Two of that server's writes still raise on
such a row: the eviction revert, which reads a client's in-flight rows and then reverts them
unconditionally, and the scheduler's dispatch stamp, an ORM flush. Either ends a thread and the
relay exits 5; the upstream server puts its tasks back only after its eviction period. No result is
lost. The eviction half is the same conditional revert that closes the lost-result window above.

*Horizon: near-term, before 2.9.0 · Depends on: — · Refs: orphan revert (one fix for the eviction
half); contended Scheduler writes (`Task.next` off the ORM flush); `spec/relay/` C7-1, C7-2*
**Seed:** [`issues/relay-abort-races.md`](issues/relay-abort-races.md) · *status: adopted:relay-hardening*

## 2.8 and 2.9 clients and servers cannot work together, and fail unclearly

The 2.9 line added task columns that a 2.8.3 peer cannot read, and nothing on the wire names a
version. A 2.8.3 client exits on its first bundle from a 2.9 server with a `TypeError`; a 2.9 client
against a 2.8.3 server runs its tasks, but the server records no result and the run hangs. Every 2.9
pre-release is affected. The fix is a clean, prompt version error in both directions, and a line in
the 2.9.0 release notes; `zone-dispatch`'s version handshake (#78) is meant to be that error and
should be verified against 2.8.3.

*Horizon: near-term, before 2.9.0 · Depends on: `zone-dispatch`'s version handshake (landed, #78;
R5 of `spec/zone-dispatch/GOAL.md`) · Refs: release notes*
**Seed:** [`issues/wire-compat-2-8.md`](issues/wire-compat-2-8.md)

## `hs server --initdb` never initializes the database

`hs server` accepts `--initdb`, but `ServerApp.run` calls `ensuredb()` without it
(`src/hypershell/server.py:1664`; the flag is defined at `:1626-1629` and never read), so on
PostgreSQL and TimescaleDB the server only checks the schema and refuses a database that was never
initialized; under the `timescale` provider the hypertable pass never runs from it. `hs submit` and
`hs cluster` honour the same flag. The docs and the server's own help still promise it.
Pre-existing since 2023 (`70bcc63`).

*Horizon: near-term, small · Depends on: — · Refs: `timescale` review cycle 2 (F8)*
**Seed:** [`issues/server-initdb-ignored.md`](issues/server-initdb-ignored.md) · *status: adopted:server-initdb*

## The remaining duration options still take bare seconds only

`--bundlewait`, `--signalwait`, `--delay-start`, the autoscaling `--period` and the `--interval` on
`hs wait` and `hs run` still take a bare number of seconds, so `--period 5m` is an argument error
while `-W 5m` works. `--delay-start` gives a negative value its own meaning (a random wait), which
the strict parser refuses.

*Horizon: near-term, small · Depends on: `size-and-duration-units` (landed, #96) · Refs: its GOAL's Non-goals*
**Seed:** [`issues/duration-units-remaining-options.md`](issues/duration-units-remaining-options.md) · *status: adopted:duration-units-remaining*

## Duration settings read only from configuration still take bare seconds only

`server.evict`, `client.heartrate` and the relay's duration keys (`bundlewait`, `poll`,
`retention`, `timeout`, and the upstream `heartrate`, `evict_hint`, `deadline` and `backoff`) have no
command-line option and are read straight from configuration, so `evict = "10m"` is refused or
misread while `autoscale.period = "5m"` works.

*Horizon: near-term, small · Depends on: `duration-units-remaining` · Refs: its GOAL's Non-goals*
**Seed:** [`issues/duration-units-config-keys.md`](issues/duration-units-config-keys.md) · *status: unshaped*

## `auth_env` and `auth_eval` in a config file break every command

`docs/security.rst` recommends `auth_eval` / `auth_env` to keep the authkey out of the config file,
but the built-in defaults already define `server.auth` (`src/hypershell/core/config.py:134`), and
cmdkit refuses a key that has more than one variant. With either suffix in a config file, every
`hs` command, `hs config get` included, dies at import with `'auth' has more than one variant`.
A variant should take precedence over the default, and an unset variable should be an error, never
a fallback to the default key. Pre-existing since 2.8.0.

*Horizon: near-term, small · Depends on: — · Refs: `docs/security.rst`*
**Seed:** [`issues/server-auth-variant-collision.md`](issues/server-auth-variant-collision.md) · *status: adopted:server-auth-variant-collision*

## The queue key can still be given on a command line

Since 2.9.0a5 and 2.8.5, `hs cluster` hands its key to the clients it launches in their
environment, never on a command line, where a process listing shows it to every user on the host.
The `-k`/`--auth` option of `hs client`, `hs server` and `hs submit -q` still puts it there, on the
long-lived processes a person starts by hand, and the help, man pages, completions and tutorials
all show it. Nothing needs the option: each command already reads `HYPERSHELL_SERVER_AUTH` or
`server.auth`. On 2.8 the option should warn; on 2.9 it should be refused. Pre-existing in
`develop` and on the 2.8 line.

*Horizon: near-term, small · Depends on: — · Refs: the key hand-off hardening (#80, #81)*
**Seed:** [`issues/key-option-on-command-line.md`](issues/key-option-on-command-line.md) · *status: adopted:key-option-refused*

## A mistyped option can print its value, a key included, to stderr and the log

The parser's error for an unknown or ambiguous option repeats the arguments it could not place, and
every command logs that error at CRITICAL after logging is set up, so a value given to a mistyped
option reaches stderr and any log file. `-k`/`--auth` itself is refused without an echo since
`key-option-refused`; other forms, such as `hs server --au=KEY`, still echo. The general fix belongs
in cmdkit's argument-error path.

*Horizon: near-term, small · Depends on: `key-option-refused` · Refs: its plan's refusal-mechanism
probes; cmdkit*
**Seed:** [`issues/parser-echoes-option-values.md`](issues/parser-echoes-option-values.md) · *status: unshaped*

## Environment variables cannot reach every configuration key

The guide says every option can be set by a `HYPERSHELL_*` variable, but every underscore in the
name is a level of nesting and every value is coerced (`src/hypershell/core/config.py:247-249`, by
cmdkit). A key with an underscore of its own is out of reach: `server.tls.min_version`,
`database.timescale.chunk_interval`, `connect_args`, and every `_env` / `_eval` variant, so
`HYPERSHELL_SERVER_AUTH_ENV` makes `server.auth` a table. And a string key cannot be given `false`,
`true`, `null`, an empty value or digits: an all-digit `HYPERSHELL_SERVER_AUTH` becomes an `int` and
crashes `hs server`. `[export]` values reach tasks re-stringified (`007` becomes `7`). Every documented
key should be settable, with the text given. Pre-existing since 2.7.0.

*Horizon: near-term · Depends on: — · Refs: the `auth_env` collision above (shape together); relay
(its zone tables add more keys)*
**Seed:** [`issues/env-config-key-mapping.md`](issues/env-config-key-mapping.md) · *status: unshaped*

## `hs cluster` waits forever when every launched client fails

When every client a cluster launched exits early, from an authkey or TLS mismatch, an unreachable
address or a bad remote executable, `hs cluster` ignores their exit statuses and joins a server
that has work left and no one to run it (`src/hypershell/cluster/remote.py:309-310`,
`src/hypershell/cluster/ssh.py:310-312`). It never exits and names no failure; under autoscaling it
relaunches into the same failure instead. It should exit non-zero with a clear error once every
client has gone and work remains. Pre-existing since 2.8.0.

*Horizon: near-term · Depends on: — · Refs: the advertised-host fix below (one way every client
fails); failure-budget halt policy (the autoscaling bound)*
**Seed:** [`issues/cluster-waits-when-every-client-fails.md`](issues/cluster-waits-when-every-client-fails.md) · *status: unshaped*

## `--ssh` and autoscaling tell clients the host's own name, whatever `-H` binds

Launcher and `--mpi` clusters tell their clients the bind address unless it is `0.0.0.0`
(`src/hypershell/cluster/remote.py:286`), but `--ssh` (`src/hypershell/cluster/ssh.py:283`) and
autoscaling (`src/hypershell/cluster/remote.py:816`) always pass the host's own name. A server bound
to one interface on purpose then tells its clients a name that may not reach it, and there is no
option to name the advertised address apart from the bind, which TLS certificate names also need.
One rule for every mode, and an override such as `--advertise`, would also serve the relay's own
downstream address.

*Horizon: near-term, small · Depends on: — · Refs: relay (its downstream listener needs the same
rule)*
**Seed:** [`issues/advertised-host-ignores-bind.md`](issues/advertised-host-ignores-bind.md) · *status: unshaped*

## Relay rough edges: a TLS mismatch retried forever, idle zones ended early, a slow finish

A relay whose TLS setting differs from its upstream server's retries forever, where a client fails
at once. In a relay serving several zones, a zone with no site client at the moment upstream
finishes can end before taking rows still staged for it. Some runs finish 6–9 s late, once about a
minute, for a reason not yet found. Eviction ages a relay's heartbeat by the relay's own clock, so
cross-site skew evicts a live relay. And the man page's relay synopsis warns at build and renders a
stray blank line.

*Horizon: near-term · Depends on: — · Refs: the advertised-host fix above (a relay's own
downstream address); `spec/relay/` C4-2, C5-1*
**Seed:** [`issues/relay-hardening.md`](issues/relay-hardening.md) · *status: adopted:relay-hardening*

## A role's first upstream call after a server restart can fail as a remote error

When the upstream server restarts at the same address, a relay's link roles should see the lost
connection and reconnect. The first call a role makes on a new worker thread, through a client
created before the restart, can instead fail with a remote `KeyError`: the new thread dials the
restarted server with a proxy id the old server minted, and the link treats it as a non-transport
error. In the puller it ends the relay with exit status 5; in the hand-back role it refuses the
hand-back and leaves the tasks to eviction, although the restarted server has already requeued them.
The window in a whole relay is narrow, and a small harness reproduces it every time. A stale-proxy
error on a role's first call after a reconnect should count as a transport loss, or the roles'
proxies should be re-created on reconnect.

*Horizon: near-term, small · Depends on: — · Refs: `spec/relay-retire/REVIEW.md` (O1); the relay
abort races and rough edges above*
**Seed:** [`issues/upstream-link-restart-proxy.md`](issues/upstream-link-restart-proxy.md) · *status: adopted:relay-hardening*

## A relay run can still end up to about 5 s late

After the relay hardening fixes, a relay run's tail (last completion to upstream exit) is about 1.7 s
when nothing waits, but three of five runs on the fixed head ended about 7 s late. The relay launches
its site clients with `relay.bundlewait` as their own result bundle wait, so the last results can sit
in a site client until it expires: a client should flush when it holds nothing left to run, or the
relay should give site clients a shorter wait. Separately, a result landing does not wake the upstream
scheduler, whose back-off doubles toward `server.poll`; one diagnostic run lost 6 s to it.

*Horizon: near-term, small · Depends on: — · Refs: `spec/relay-hardening/` R8 and its laptop evidence;
the relay rough edges above*
**Seed:** [`issues/relay-shutdown-tail.md`](issues/relay-shutdown-tail.md) · *status: unshaped*

## `rotatedb` lost-update window

The clone-then-delete rotation concedes its own race in a source comment, and a crash between the
`VACUUM INTO` and the delete leaves rows in both files or neither. Documenting it as offline-only is
cheap now; the mechanism is reworked by the turso rotation work anyway.

*Horizon: mid-term, low priority · Depends on: — · Refs: turso R8–R11*
**Seed:** [`issues/rotatedb-lost-update-window.md`](issues/rotatedb-lost-update-window.md)

## A queue proxy keeps the stock decref finalizer until its constructor returns

In a process that serves queues in-process, a queue client's proxy gets the decref hand-off only
after the stock proxy constructor returns. If a connection inside that constructor fails while the
server is alive, the half-made proxy keeps the stock finalizer, and a collection on the server's
accept loop can then stall it. Seen only with an injected failure; the window predates the hand-off.

*Horizon: mid-term, low priority · Depends on: the in-process queue server (`zone-queue-primitive`,
landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4, P4)*
**Seed:** [`issues/queue-proxy-finalizer-swap-window.md`](issues/queue-proxy-finalizer-swap-window.md)

## Handed-off decrefs can outlive their server and reach a same-port successor

A handed-off decref waits for the one sender thread, which under churn falls behind, so it can
reach a successor server on the same port instead of its own and release a live client's queue.
Seen statistically under same-port restart stress in one process; no CLI path restarts that way.

*Horizon: mid-term, low priority · Depends on: the in-process queue server (`zone-queue-primitive`,
landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4, P5); the same-port restart entry below*
**Seed:** [`issues/queue-decref-handoff-delay.md`](issues/queue-decref-handoff-delay.md)

## A same-key, same-port restart in one process receives the old server's decrefs

A dropped client of a stopped in-process server, collected after a same-key successor starts on
the same port, sends its decrefs to the successor, which can release a queue a live client holds.
A client that dials the restarted server by another name than its bind host can also reuse a
connection cached for the old server. It came with the in-process server's same-port restart; no
CLI path uses the same key and port.

*Horizon: mid-term, low priority · Depends on: the in-process queue server (`zone-queue-primitive`,
landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4, Observations); the hand-off delay entry above*
**Seed:** [`issues/queue-stale-decref-same-port-restart.md`](issues/queue-stale-decref-same-port-restart.md)

## A forked child of a serving process keeps the queue server's port

A child forked from a process that serves queues in-process inherits the listening socket, so after
the server shuts down the port cannot be bound again and new clients hang until the child exits. No
CLI path forks a serving process; only library use sees it.

*Horizon: mid-term, low priority · Depends on: the in-process queue server (`zone-queue-primitive`,
landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 5, Observations)*
**Seed:** [`issues/queue-forked-child-keeps-listener.md`](issues/queue-forked-child-keeps-listener.md)

## Server-owned queue proxies without reference counting

The in-process queue server closes its "finalizer dials the server" stalls with thread rules that
rest on CPython timing and private internals. The shared queues live as long as the server, so a
client's proxies for them may not need reference counting at all. Proxies built as server-owned
never send a decref, which would make the stall impossible by construction and could let the
hand-off machinery retire. It needs a check of server-side refcount bookkeeping across reconnects
first.

*Horizon: mid-term · Depends on: the in-process queue server (`zone-queue-primitive`, landed, #76) ·
Refs: `spec/zone-queue-primitive/` (review cycles 3–6: C5, C7–C10)*
**Seed:** [`issues/queue-proxies-without-refcount.md`](issues/queue-proxies-without-refcount.md)

## `hs client -b` reads the submit section's key, not the client section's

`hs client` takes its `-b`/`-w` defaults from `submit.bundlesize`/`submit.bundlewait`, while the
configuration reference documents `[client]`. Since zone-dispatch a client advertises an explicit
`-b` as the size it requests, so a `submit.bundlesize` set for `hs submit` is advertised by every
client too.

*Horizon: near-term, low priority · Depends on: zone-dispatch (Z2, landed, #78) · Refs:
`spec/zone-dispatch/` (plan gate ruling N4)*
**Seed:** [`issues/client-bundlesize-config-key.md`](issues/client-bundlesize-config-key.md)

## `hs submit -q` cannot feed a server with a database

`hs submit -q` puts bundles on a server's live queue, which only a server without a database
serves. Against a server with a database, 2.8.x and 2.9.0a2 ran the tasks, but the server's Confirm
and Receiver threads each died on an uncaught `StaleDataError`, after which it recorded no results
for the rest of the run; at the unreleased `ec75169` only the Confirm thread died and those tasks'
results were refused as stale. In zone-dispatch, before its review-1 remediation, the submission
hung; it is now refused at once with a clear error. A host that can reach the server but not its
database cannot add work to it.

*Horizon: near-term, low priority · Depends on: zone-dispatch (Z2, landed, #78) · Refs:
`spec/zone-dispatch/REVIEW.md` (cycle 1, F4; cycle 2, F6)*
**Seed:** [`issues/queue-submit-database-server.md`](issues/queue-submit-database-server.md)

## The server cannot listen on IPv6

Both transports listen on IPv4 only, so a server is unreachable on an IPv6-only host or network and
an IPv6 bind address fails. Not a fidelity defect, but recorded here because the TLS address-family
fix aligns the client with the IPv4-only listener rather than widening it; dual-stack serving would
have to move both ends together.

*Horizon: long-term · Depends on: — · Refs: `spec/tls-client-address-family/`*
**Seed:** [`issues/ipv6-server-listener.md`](issues/ipv6-server-listener.md)

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

## Server CPU per task bounds throughput at small bundle sizes

At bundle size 1, the server's main process runs its own scheduling, confirm and writeback
statements for every task, and the evidence points at its CPU as the throughput ceiling.
`writeback-prebuilt-update` recovers the writeback's share by building that statement once. The
scheduler and confirm paths may carry the same per-call cost, and batching results across bundles
is a further option.

*Horizon: mid-term · Depends on: `writeback-prebuilt-update` (landed, #73; the measurement method) ·
Refs: —*
**Seed:** [`issues/server-per-task-statement-overhead.md`](issues/server-per-task-statement-overhead.md) · *status: unshaped*

---

# Part II — The 2.9.0 feature cycle

A shaped GOAL, already negotiated with dated clarifications, an agreed appetite, non-goals, and
drafted R-IDs. It carries `status: shaped`: the shaping conversation happened, but acceptance into a
cycle has not. Promotion should re-confirm scope against current `develop` and cite anything that has
drifted, then adopt largely as written. The last four entries are `unshaped` test-infrastructure
seeds: three split out of shipped cycles, one found out of cycle.

## Turso provider, partition-attached listing, and S3 captured output

Rotation partitions are write-only archives today — `hs list` cannot see them, and SQLite's `ATTACH`
cap forecloses ever querying a long campaign's history as one thing. Turso holds SQLite's contracts
from our side of the code and removes that cap; the official dialect already ships, so the work is
adoption, validation, and the two capabilities it unlocks. Independently, the `[s3]` extra un-maroons
captured output from the execution host.

*Horizon: mid-term · Depends on: the sequencing after zone, cleared (zone shipped as
`zone-autoscale`, #99; relay, the other half, in #82); R4's per-provider seam in `data/core.py`
shares ground with the resilience feature's pragma defaults · Refs: R13–R15 soften relay's
cross-site output non-goal*
**Seed:** [`issues/turso.md`](issues/turso.md) · *status: shaped*

## The whole test suite on a server database

The suite runs on SQLite only. CI's PostgreSQL job runs three modules across five entries: the
upgrade tests, the writeback's result-to-dispatch match at the data layer
(`tests/test_writeback_postgres.py`) and the TimescaleDB scenarios (`tests/test_timescale.py`). The
rest of the suite never runs on a server, so the scheduler, the writeback end to end, retries and
most of the CLI are never exercised on PostgreSQL or TimescaleDB. A harness selected by
an environment variable would run the whole suite against a server, isolated per test, with the
SQLite-only tests marked and skipped. Split out of TimescaleDB's R9, which ships a dedicated
TimescaleDB scenario module instead.

*Horizon: mid-term · Depends on: — · Refs: split from `timescale` R9 (ruling 2026-11-02)*
**Seed:** [`issues/server-db-test-suite.md`](issues/server-db-test-suite.md) · *status: unshaped*

## A hung integration test costs the whole CI job timeout

Integration tests run each command as a subprocess with no timeout and no per-test time limit, so
a hang runs the CI job to its `timeout-minutes` and the log names no failing test.
`group-advance-strands-lower-group` hit this with a hang in about 1–3% of grouped runs under load.
A subprocess or per-test timeout would turn a hang into a fast, named failure.

*Horizon: near-term · Depends on: — · Refs: `spec/group-advance-strands-lower-group/`*
**Seed:** [`issues/integration-test-hang-timeout.md`](issues/integration-test-hang-timeout.md) · *status: unshaped*

## Test teardown by SIGKILL leaks a 2.8 server's named semaphores on macOS

Tests and the queue bench end each `hs` session with `os.killpg(..., SIGKILL)`, which also kills
multiprocessing's resource tracker before it can unlink the process's POSIX named semaphores. A 2.8
server holds several, so on macOS each kill leaks them until reboot, and the pool runs out:
`multiprocessing.Lock()` then fails with ENOSPC and no 2.8 server starts. A 2.9 server makes none,
so on `develop` the leak is live only in the bench's `skew` runs against a 2.8 build. Teardown should
send SIGTERM first and SIGKILL only what is left.

*Horizon: near-term, small · Depends on: — · Refs: `tests/test_cluster_launch.py`,
`tests/bench/bench_queue.py`; the 2.8 line (backport)*
**Seed:** [`issues/test-teardown-sigkill-leaks-semaphores.md`](issues/test-teardown-sigkill-leaks-semaphores.md) · *status: unshaped*

## Relay tests can pass a broken relay, and lose a port race to themselves

The R21 judge passes on records that cannot show what four of its checks name; two reconnect
behaviours survive mutation; and the relay workload tests draw a free port, release it, and hand it
to a relay that binds it later, so a parallel copy can take it first. Smaller: the group check's
cancel and `retried` filters are unpinned, and one start-up test resolves a path under the
developer's own database location. Split out of `relay`'s review.

*Horizon: near-term, small · Depends on: — · Refs: `spec/relay/` C4-3, C4-4; the relay abort races
and rough edges in Part I*
**Seed:** [`issues/relay-test-strength.md`](issues/relay-test-strength.md) · *status: unshaped*

# Part III — Turso follow-ons

What the Turso provider's vacuum work left open. Turso's multi-process mode is experimental, and the
vacuum went through six review cycles. Each cycle exposed a deeper limit in the engine, and 2.9.0a7
ships `hs initdb --vacuum` under Turso with one known risk flagged in the docs. The first entry
gathers the evidence and the second acts on it.

## Reproduce Turso's crash after a killed vacuum without HyperShell

With multi-process Turso, when a vacuum is killed while it folds its compacted image into the
database, the processes that open the database right after it can panic or read an unreadable row.
This happens well below the shared WAL index's capacity. So far it has only been reproduced through
HyperShell's own kill tests, and HyperShell resets Turso's shared-memory file around the vacuum. A
script that uses only `pyturso`, run on macOS and Linux and checked by someone independent, settles
what the claim is. That makes it the headline of the issue we file with the Turso team. The issue
lists the other Turso findings in a table, with an offer to file each one separately.

*Horizon: now · Depends on: — (the upstream draft it rewrites lands with `turso`) · Refs: feeds the
entry below*
**Seed:** [`issues/turso-upstream-repro.md`](issues/turso-upstream-repro.md) · *status: shaped*

## A killed Turso vacuum must not crash the processes that open the database after it

Today an administrator using multi-process Turso has to pause scheduling for a vacuum, and run it
again if it was interrupted. The fix makes a vacuum that is killed at any point leave every process
that opens the database afterwards correct, with scheduling live. Depending on what the
reproduction shows, the route is a Turso release that fixes it, HyperShell serialising the first
opens after an interrupted vacuum, or Turso supporting in-place `VACUUM` in its multi-process mode.

*Horizon: when there is evidence or an upstream fix · Depends on: turso-upstream-repro, or a Turso
release · Refs: `spec/turso/` P3, review cycles 4–6*
**Seed:** [`issues/turso-vacuum-concurrent-recovery.md`](issues/turso-vacuum-concurrent-recovery.md) · *status: unshaped*

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
