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

**Target** names the release an entry is planned for (the maintainer's ruling, 2026-11-21), and its
seed carries the same value as `target:` in its frontmatter. `2.9.0` is work to land before the
2.9.0 release; `2.9.x` a 2.9 patch release; `2.10.0` the next minor, developed as `2.10.0aN` alphas;
`2.8.x` a patch on the 2.8 line; a combination such as `2.9.x, 2.8.x` a fix that is also backported;
and `unscheduled` an idea no release is planned for (Part V). An interface change (a new option or
configuration key, refusing input accepted today, a changed default or changed output) goes to a
minor release; an interface-neutral fix goes to a patch.

Some seeds cite a maintainer-local record under `.local/`: `IMPROVEMENTS.md`, for the GNU Parallel
comparison's finding ids (H\*, M\*, L\*). That directory is gitignored: the named review and the
ids are stable, the path is not resolvable from a clone.

---

# Part I — Task fidelity

Findings from a comparative review against GNU Parallel (2026-08), which looked specifically for
failure modes dangerous to task fidelity. Triaged rather than adopted wholesale: some items are
answered by feature work further down this file, two were judged not to be defects at all, and one
was declined (see *Settled questions*).

## Unguarded task start kills the executor and poisons the task

An `OSError` while opening capture files or spawning the subprocess propagates out of the FSM and
ends the executor thread: declared resources are never released, the task stays in-flight until a
revert re-runs it into the same crash, and the client degrades executor-by-executor in silence.
Template and resource errors are already handled gracefully in the same file — spawn failure just
never got the same treatment.

*Target: 2.10.0 · Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/task-start-spawn-guard.md`](issues/task-start-spawn-guard.md)

## No process-group ownership; SIGTERM and Ctrl-C tear down without cleanup

The kill escalation ladder is well built but targets only the `sh` wrapper, so walltime does not
actually bound node usage for any task that is a pipeline or backgrounds children. Meanwhile a plain
`kill` — what Slurm epilogues and most supervisors send — hits Python's default disposition, and
`KeyboardInterrupt` slips past the `except Exception` cleanup idiom. The result is orphaned compute
on every node plus guaranteed duplicate re-execution on restart. The two must be fixed together:
Ctrl-C currently kills tasks only *because* they share the client's process group.

*Target: 2.10.0 · Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/process-group-signal-teardown.md`](issues/process-group-signal-teardown.md)

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

*Target: 2.10.0 · Horizon: after SC26 · Depends on: — · Refs: relay built the first instance of this
machinery (#82: `core/reconnect.py`, the relay's upstream link); lift it into the shared mechanism
here; shares the `data/core.py` provider seam with turso*
**Seed:** [`issues/coordinator-resilience-retry-backoff.md`](issues/coordinator-resilience-retry-backoff.md)

## A client cut off partway through connecting hangs until evicted

A client opens several queue connections as it starts. When the server accepts some and drops a
later one, the thread that lost its connection dies with `EOFError`, but the client process keeps
running and holds its confirmed tasks until the server evicts it (600 s by default). The client
should exit promptly and non-zero so its tasks are recovered, or retry the connection within a
bound. Seen at the open-file limit, where the server refuses new connections, with the same client
behaviour on `b819de6`.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs: coordinator resilience (a reconnect path
may subsume it); `spec/zone-queue-primitive/` (review cycle 2, C3)*
**Seed:** [`issues/client-partial-connect-hang.md`](issues/client-partial-connect-hang.md)

## Ingest robustness: all-or-nothing submission, no length awareness

One undecodable byte aborts a whole task file, and one malformed tag raises out of the loader after
part of the input is already submitted — leaving a partial, unlabelled submission with no per-line
diagnosis. Since `submit-input-checks` (#118), input read in full (a named file, or anything
`hs submit` takes with a database) is checked before anything is inserted, so the partial
submission remains where input is streamed, as into `hs cluster` or `hs server`. A line too long to
ever spawn is not caught until some node tries it.

*Target: 2.10.0 · Horizon: near-term · Depends on: sequence after the `#`-truncation fix (same path;
cleared: it landed in `submit-input-checks`, #118) · Refs: —*
**Seed:** [`issues/ingest-robustness.md`](issues/ingest-robustness.md)

## Client I/O hygiene

Five small independent defects in the task-output path: binary captured output crashes retrieval on
a `UnicodeDecodeError` though the file itself is byte-faithful; a full disk truncates output while
the task records success; tasks inherit the client's stdin and race fd 0; concurrent crash logs
overwrite each other; and an uncalled transport in `core/remote.py` drops tail output and busy-spins.

*Target: 2.10.0 · Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/client-io-hygiene.md`](issues/client-io-hygiene.md)

## Templating and remote-env contract

`{}` splices arguments unquoted into a `shell=True` line, with no safe alternative — the `eval`
forms stay by design, but a quoted token should exist and the contract should be stated. Separately
`compile_env` builds environment assignments without shell quoting, so a value containing a space or
`$` corrupts the remote launch line, and there is no dry run to preview what would actually execute.

*Target: 2.10.0 · Horizon: mid-term · Depends on: — · Refs: its quoted token (R1) is a prerequisite
of client-side sandboxing (Part IV)*
**Seed:** [`issues/templating-and-remote-env-contract.md`](issues/templating-and-remote-env-contract.md)

## A restarted run stops before a pending retry

`--restart` into a database whose only unfinished work is a retryable failure stops at START,
because the "anything left?" check counts only incomplete rows. The retry is never made and the run
exits 0. LOAD now holds a run open for a pending retry; START does not. Pre-existing, and found by
the review of the group-advance fix.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs: `spec/group-advance-retry-gate/` (review
P1)*
**Seed:** [`issues/restart-pending-retry.md`](issues/restart-pending-retry.md)

## Eager mode can bundle the same retry twice

With eager retries and room left in the bundle, `Task.next` lists each new retry row twice: it
creates the retry, then tops the bundle up with unscheduled rows, and the fresh retry is one of
them. A client may receive the same task twice in one bundle. Whether it then runs twice is
untested; the seed asks for that test first.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs: `spec/group-advance-retry-gate/`*
**Seed:** [`issues/eager-retry-double-bundle.md`](issues/eager-retry-double-bundle.md)

## A client that times out during a pause in a stream leaves a late line unrun

A run fed by a stream now keeps scheduling until its input ends. A client whose `-T` is shorter
than a pause in that input exits 0 during the pause, and the next line is staged for a client that
never comes: `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1 -T 3` prints `a` and then waits with
`echo b` staged. Launcher modes keep waiting by design on all-zero exits; autoscaling relaunches and
is not affected. Before `task-input-fidelity` the same run dropped the line silently. Scheduled after
the agreed pre-2.9.0 list.

*Target: 2.9.x · Horizon: near-term · Depends on: `task-input-fidelity` (landed, #110) · Refs:
`spec/task-input-fidelity/` (GOAL Clarifications, plan-gate ruling 3)*
**Seed:** [`issues/stream-pause-client-timeout.md`](issues/stream-pause-client-timeout.md) · *status: unshaped*

## With `-b N>1`, a line read before a pause waits for the next one

The database committer checks `--bundlewait` only when a task arrives, so with a bundle size above
one, a line read just before a pause in a stream stays uncommitted until the next line or the end of
the input. It is committed and run eventually, so no stop depends on it. Pre-existing, also in
`hs submit`, and found by the plan of `task-input-fidelity`.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs:
`spec/task-input-fidelity/research/02-stream-stop.md`*
**Seed:** [`issues/stream-bundlewait-commit-wait.md`](issues/stream-bundlewait-commit-wait.md) · *status: unshaped*

## No test pins the re-count before a quiet stop when a failed group ends a stream run

When the input has ended and a failed group holds no task in another group, the scheduler counts the
remaining tasks once more before it stops quietly, so a row another writer commits in that instant
is still scheduled or halts the run. The behaviour is right, but removing the re-count passes every
test. A deterministic interleaving test should pin it. Filed after `task-input-fidelity` (#110).

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/task-input-fidelity/`
(REVIEW cycle 3, N1)*
**Seed:** [`issues/stream-failed-group-recount-test.md`](issues/stream-failed-group-recount-test.md) · *status: unshaped*

## `hs server --forever --no-db` exits once its only relay drains

An in-memory server started with `--forever` should keep serving until signalled, but with one relay
it exits 0 once that relay has drained; the same run on a database stays up. Found by the plan of
`relay-advertise`; not yet narrowed to a cause.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/relay-advertise/`*
**Seed:** [`issues/forever-nodb-server-exits-when-relay-drains.md`](issues/forever-nodb-server-exits-when-relay-drains.md) · *status: unshaped*

## SIGUSR1 stops an autoscaling cluster's scheduling but not the cluster

After SIGUSR1, an autoscaling `hs cluster` stops scheduling and its clients drain and disconnect, but
the process stays up until SIGTERM: it waits on the autoscaler, which never looks at the signal. A
program that owns such a cluster cannot ask it to drain, so it terminates it, killing any client
still connected (the client's Slurm job ends `FAILED`).

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `hypershell/hypershell-nextflow`
(`DESIGN.md` item 6)*
**Seed:** [`issues/autoscaling-cluster-ignores-usr1.md`](issues/autoscaling-cluster-ignores-usr1.md) · *status: unshaped*

## `hs update group=…` stores a non-integer group

`hs update` accepts any value for `group` and stores it, so a text group reaches the scheduler even after submit-time validation (`submit-input-checks`) refuses one. Apply the submit-time integer rule to `hs update group=…`, refusing with exit 2 before anything is written.

*Target: 2.9.0 · Horizon: near-term, small · Depends on: — · Refs: `spec/submit-input-checks/GOAL.md` (Non-goals)*
**Seed:** [`issues/update-group-stores-text.md`](issues/update-group-stores-text.md) · *status: unshaped*

## An empty `logging.style` makes every command exit 3 until the file is hand-edited

`hs config set logging.style ''` writes an empty style, after which every command, `hs config` included, exits 3, so the only way out is to edit the file by hand. Either refuse an empty or unknown style in `hs config set` (it takes a fixed word), or let `hs config` itself start with a bad logging style so the user can fix it.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/config-set-and-unread/GOAL.md` (Non-goals)*
**Seed:** [`issues/config-empty-logging-style.md`](issues/config-empty-logging-style.md) · *status: unshaped*

## A local `hs cluster`'s in-process client ignores `client.heartrate`

Under a local `hs cluster`, the in-process client uses the built-in heartrate rather than `client.heartrate`; the reference names only `hs client` as the key's reader. Pass the configured heartrate to the local cluster's client, or document that it applies only to launched clients.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/config-set-and-unread/GOAL.md` (Non-goals)*
**Seed:** [`issues/cluster-local-client-heartrate.md`](issues/cluster-local-client-heartrate.md) · *status: unshaped*

## A text `attempts = "3"` in a file crashes every command at import

A configuration file holding `server.attempts` as a string makes every command fail at import with a traceback instead of a configuration error. Read typed keys from a file by the key's declared kind, as the environment layer does since #113, and refuse a value that does not convert with exit 3 naming the key and file.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/env-config-keys/` (the typed reader); `spec/config-set-and-unread/research/`*
**Seed:** [`issues/config-text-attempts-crashes.md`](issues/config-text-attempts-crashes.md) · *status: unshaped*

## `hs config set` writes integers wider than 64 bits to TOML

An integer value wider than 64 bits is written to the configuration file, which TOML readers then refuse or misread. Refuse an integer outside TOML's 64-bit range in `hs config set` with exit 2 naming the key.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/config-set-and-unread/research/`*
**Seed:** [`issues/config-set-wide-integers.md`](issues/config-set-wide-integers.md) · *status: unshaped*

## The `--eager` help says "greater than one" where it means zero

The `--eager` help text describes the retry condition as attempts "greater than one"; the behaviour turns on a retry count above zero. Correct the help text and its docs snippet.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/config-set-and-unread/research/`*
**Seed:** [`issues/eager-help-wording.md`](issues/eager-help-wording.md) · *status: unshaped*

## An empty task file never finishes

`hsx` given an empty task file on an empty database never exits: the server logs `Empty database -
waiting for initial tasks` and waits for tasks that cannot arrive. The stop branch of
`Scheduler.load_bundle` needs at least one task in the database, so a run that submits none on an
empty database never reaches FINAL. The same holds before and after
`group-advance-strands-lower-group`. Pre-existing, and found by that cycle's review.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs:
`spec/group-advance-strands-lower-group/` (REVIEW cycle 1)*
**Seed:** [`issues/empty-task-file-hangs.md`](issues/empty-task-file-hangs.md) · *status: unshaped*

## A "database is locked" error while a task file is read hangs the run

On SQLite, when another connection holds the database locked while `hsx` or `hs cluster` submits a
task file, the submitter's committer dies with `database is locked` and the run waits until it is
killed. The loader then blocks on its full queue, so the server never gets past waiting for the
submitter, and nothing passes the committer's error on to the exit status. The same holds before
and after `group-advance-strands-lower-group`. Pre-existing, and found by that cycle's review.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs:
`spec/group-advance-strands-lower-group/` (REVIEW cycle 2)*
**Seed:** [`issues/database-locked-during-ingest-hangs.md`](issues/database-locked-during-ingest-hangs.md) · *status: unshaped*

## On a fresh SQLite site, the Python API's `run_local` fails and then hangs

The commands create the schema before they submit; `run_local` and `LocalCluster` do not. On a
site whose SQLite database was never initialized, the submitter's committer dies on its first
insert with `no such table: task`, no task runs, and the run hangs as in the entry above:
`run_local` never returns, and `stop()` blocks on the submitter's full queue. Confirmed by a probe
on `develop`. Pre-existing; found by `update-client-retry` at plan time.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: same committer death as
`issues/database-locked-during-ingest-hangs.md`*
**Seed:** [`issues/run-local-fresh-sqlite.md`](issues/run-local-fresh-sqlite.md) · *status: unshaped*

## The legacy single-task tag edit can lose a concurrent tag change

`hs update ID tag KEY:VALUE` reads the task's tags and writes back the merge, so a tag change
committed in between is overwritten, on every database. The filtered form writes tag changes in one
statement. Pre-existing; out of scope for `hs-update-contended-writes` (#111).

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs:
`spec/hs-update-contended-writes/` (Non-goals)*
**Seed:** [`issues/update-legacy-tag-lost-write.md`](issues/update-legacy-tag-lost-write.md) · *status: unshaped*

## The single-task `hs update ID FIELD VALUE` form ignores filter options

The three-argument form writes the named task and never reads `-w`, `-t`, `-g`, `--limit` or the
filter aliases given with it, so `hs update ID args … -F` writes whether or not the task failed. The
filtered form honours the same options. The fix applies the filters or refuses the combination;
refusing would be an interface change, which shaping weighs against the Target. Pre-existing; named
by `update-client-retry`'s shaping, which takes the same form's action options.

*Target: 2.9.0 · Horizon: near-term, small · Depends on: — (sequence after `update-client-retry`,
same routing) · Refs: `issues/update-legacy-tag-lost-write.md`*
**Seed:** [`issues/update-legacy-form-ignores-filters.md`](issues/update-legacy-form-ignores-filters.md) · *status: unshaped*

## Every numeric setting has one declared range, one parser and one error

Bounds on numeric input were added one cycle at a time (a period above zero, an evict cap, a rate
floor, 32-bit groups), and each review finds the next absurd value that slips through. One table of
ranges beside each key's kind, one parser per kind for options, files and the environment, one
error, and a start-up backstop that turns any stray numeric error into a clean refusal would end
that. It absorbs the absurd-input seeds filed during the 2.9.0 cycles.

*Target: 2.9.0 · Horizon: near-term · Depends on: `config-set-and-unread` (landed, #117),
`submit-input-checks` (landed, #118), `update-client-retry` · Refs: `core/config_schema.py`;
review rubric (Scope)*
**Seed:** [`issues/bounded-inputs.md`](issues/bounded-inputs.md) · *status: unshaped*

## An unrecognized configuration key is silently ignored

A misspelled key in a configuration file (`[server] attempt = 3`) or a `HYPERSHELL_*` variable that
names no key loads without a word, and the command runs with the default. Since #113 the schema
declares every key and how each table reads names past it, so an unknown key under a closed table
can warn, naming its file and the closest declared key, and otherwise be ignored as today. Open
tables, `[export]`, zone tables and the `_env`/`_eval` forms are exempt. A warning is patch-safe; a
strict mode that refuses unknown keys would be a 2.10.0 interface change.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs:
`src/hypershell/core/config_schema.py` (`KEYS`, `TABLES`); `issues/bounded-inputs.md` for values*
**Seed:** [`issues/config-unknown-key-warning.md`](issues/config-unknown-key-warning.md) · *status: unshaped*

## On PostgreSQL, a task cannot ask for 2 GB of memory or more

`memory` is a byte count in an `INTEGER` column: 64-bit on SQLite, 32-bit on PostgreSQL. So on
PostgreSQL `hs submit -m 4GB` is refused at insert, and then hangs (the committer death above). A
retry is then refused as "already submitted", though nothing was stored. Every submitter that sets
memory meets it, `hypershell-mcp` and `nf-hypershell` included. The fix is `BIGINT` and an upgrade
step that retypes the column in place, which the upgrade step does not do yet.

*Target: 2.9.0 · Horizon: near-term · Depends on: — · Refs: same committer death as
`issues/database-locked-during-ingest-hangs.md`; `issues/ingest-robustness.md`*
**Seed:** [`issues/memory-column-overflows-postgres.md`](issues/memory-column-overflows-postgres.md) · *status: unshaped*

## Docs and runtime surfacing

Behaviour that is correct or deliberate but unannounced: `--no-confirm` silently disables orphan
recovery, captured output is stranded on client hosts under an undocumented shared-filesystem
assumption, our `{.}` means *dirname* where GNU Parallel's strips the extension (a silent
porting trap worth settling before v3 freezes the token names), the absence of input combinators is
a stance rather than an omission, and cross-host timing aggregates assume synchronised clocks.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs: —*
**Seed:** [`issues/docs-and-runtime-surfacing.md`](issues/docs-and-runtime-surfacing.md)

## Eviction and cancellation cannot reach a running client

Two symptoms of one gap: the server has no channel on which to say anything to a client. An evicted
client is never told to stop, so a merely-partitioned node keeps executing while its tasks run
elsewhere; and `hs update --cancel` marks rows terminal without touching the process. Both need
a server→client control path — and `zone-dispatch`'s request-return surface (#78) is meant to be
that seam, so this waited rather than building one twice.

*Target: 2.10.0 · Horizon: mid-term · Depends on: `zone-dispatch`'s request path (landed, #78) ·
Refs: the corruption half was closed separately by `writeback-scoped-conditional` (#70)*
**Seed:** [`issues/evicted-client-fencing-and-cancel-reach.md`](issues/evicted-client-fencing-and-cancel-reach.md)

## `Task.cancel_all` overwrites a result that lands before it

The model's cancel writes each row by id whatever its state, so a caller that reads its unfinished
tasks and then cancels them overwrites any result that lands in between with `-1`. `hypershell-mcp`
does exactly that; `nf-hypershell` keeps its own conditional copy. `hs update --cancel` is not
affected: its filter is part of the UPDATE.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: prerequisite for the submitter
lease (Part V); `hypershell/hypershell-nextflow` (`DESIGN.md` item 7)*
**Seed:** [`issues/cancel-all-conditional.md`](issues/cancel-all-conditional.md) · *status: unshaped*

## A task whose confirmation has not landed is invisible to orphan recovery

Orphan recovery finds an evicted client's rows by `client_id`, which only the client's confirmation
sets, and confirmations are not ordered against eviction. A client evicted before its confirmation
is applied leaves its rows in flight with a NULL `client_id`, never reverted or redispatched; a late
confirmation then names the already-evicted client. Either way the row waits until restart.

*Target: 2.10.0 · Horizon: mid-term · Depends on: — · Refs: `writeback-scoped-conditional` (#70, same
token); relay*
**Seed:** [`issues/stale-confirm-hides-orphan.md`](issues/stale-confirm-hides-orphan.md)

## On the 2.8 line, the queue key can still be given on a command line without a warning

Since #104, 2.9 refuses `-k`/`--auth` on `hs client`, `hs server` and `hs submit -q`, and its docs
no longer show it. The 2.8 line (2.8.7) still takes the option silently, so a long-lived server or
client started by hand shows its key to every user on the host, and the 2.8 help, man pages,
completions and docs still offer it. This is the 2.8.x backport: keep the option there for
compatibility, but warn each time, without the key, naming `HYPERSHELL_SERVER_AUTH` and
`server.auth`, and mark it deprecated in the 2.8 docs. Whether and when to ship it is the
maintainer's call. Pre-existing on the 2.8 line.

*Target: 2.8.x · Horizon: maintainer's call (a 2.8.x hotfix against `master`) · Depends on: — ·
Refs: `key-option-refused` (landed, #104: the 2.9 half); the key hand-off hardening (#80, #81)*
**Seed:** [`issues/key-option-on-command-line.md`](issues/key-option-on-command-line.md) · *status: unshaped*

## `rotatedb` lost-update window

The clone-then-delete rotation concedes its own race in a source comment, and a crash between the
`VACUUM INTO` and the delete leaves rows in both files or neither. Documenting it as offline-only is
cheap now; the mechanism is reworked by the turso rotation work anyway.

*Target: 2.9.x · Horizon: mid-term, low priority · Depends on: — · Refs: turso R8–R11*
**Seed:** [`issues/rotatedb-lost-update-window.md`](issues/rotatedb-lost-update-window.md)

## A queue proxy keeps the stock decref finalizer until its constructor returns

In a process that serves queues in-process, a queue client's proxy gets the decref hand-off only
after the stock proxy constructor returns. If a connection inside that constructor fails while the
server is alive, the half-made proxy keeps the stock finalizer, and a collection on the server's
accept loop can then stall it. Seen only with an injected failure; the window predates the hand-off.

*Target: 2.9.x · Horizon: mid-term, low priority · Depends on: the in-process queue server
(`zone-queue-primitive`, landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4, P4)*
**Seed:** [`issues/queue-proxy-finalizer-swap-window.md`](issues/queue-proxy-finalizer-swap-window.md)

## Handed-off decrefs can outlive their server and reach a same-port successor

A handed-off decref waits for the one sender thread, which under churn falls behind, so it can
reach a successor server on the same port instead of its own and release a live client's queue.
Seen statistically under same-port restart stress in one process; no CLI path restarts that way.

*Target: 2.9.x · Horizon: mid-term, low priority · Depends on: the in-process queue server
(`zone-queue-primitive`, landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4, P5); the
same-port restart entry below*
**Seed:** [`issues/queue-decref-handoff-delay.md`](issues/queue-decref-handoff-delay.md)

## A same-key, same-port restart in one process receives the old server's decrefs

A dropped client of a stopped in-process server, collected after a same-key successor starts on
the same port, sends its decrefs to the successor, which can release a queue a live client holds.
A client that dials the restarted server by another name than its bind host can also reuse a
connection cached for the old server. It came with the in-process server's same-port restart; no
CLI path uses the same key and port.

*Target: 2.9.x · Horizon: mid-term, low priority · Depends on: the in-process queue server
(`zone-queue-primitive`, landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 4,
Observations); the hand-off delay entry above*
**Seed:** [`issues/queue-stale-decref-same-port-restart.md`](issues/queue-stale-decref-same-port-restart.md)

## A forked child of a serving process keeps the queue server's port

A child forked from a process that serves queues in-process inherits the listening socket, so after
the server shuts down the port cannot be bound again and new clients hang until the child exits. No
CLI path forks a serving process; only library use sees it.

*Target: 2.9.x · Horizon: mid-term, low priority · Depends on: the in-process queue server
(`zone-queue-primitive`, landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycle 5,
Observations)*
**Seed:** [`issues/queue-forked-child-keeps-listener.md`](issues/queue-forked-child-keeps-listener.md)

## Server-owned queue proxies without reference counting

The in-process queue server closes its "finalizer dials the server" stalls with thread rules that
rest on CPython timing and private internals. The shared queues live as long as the server, so a
client's proxies for them may not need reference counting at all. Proxies built as server-owned
never send a decref, which would make the stall impossible by construction and could let the
hand-off machinery retire. It needs a check of server-side refcount bookkeeping across reconnects
first.

*Target: 2.9.x · Horizon: mid-term · Depends on: the in-process queue server
(`zone-queue-primitive`, landed, #76) · Refs: `spec/zone-queue-primitive/` (review cycles 3–6: C5,
C7–C10)*
**Seed:** [`issues/queue-proxies-without-refcount.md`](issues/queue-proxies-without-refcount.md)

## `hs submit -q` cannot feed a server with a database

`hs submit -q` puts bundles on a server's live queue, which only a server without a database
serves. Against a server with a database, 2.8.x and 2.9.0a2 ran the tasks, but the server's Confirm
and Receiver threads each died on an uncaught `StaleDataError`, after which it recorded no results
for the rest of the run; at the unreleased `ec75169` only the Confirm thread died and those tasks'
results were refused as stale. In zone-dispatch, before its review-1 remediation, the submission
hung; it is now refused at once with a clear error. A host that can reach the server but not its
database cannot add work to it.

*Target: 2.10.0 · Horizon: near-term, low priority · Depends on: zone-dispatch (Z2, landed, #78) ·
Refs: `spec/zone-dispatch/REVIEW.md` (cycle 1, F4; cycle 2, F6)*
**Seed:** [`issues/queue-submit-database-server.md`](issues/queue-submit-database-server.md)

## The server cannot listen on IPv6

Both transports listen on IPv4 only, so a server is unreachable on an IPv6-only host or network and
an IPv6 bind address fails. Not a fidelity defect, but recorded here because the TLS address-family
fix aligns the client with the IPv4-only listener rather than widening it; dual-stack serving would
have to move both ends together.

*Target: 2.10.0 · Horizon: long-term · Depends on: — · Refs: `spec/tls-client-address-family/`*
**Seed:** [`issues/ipv6-server-listener.md`](issues/ipv6-server-listener.md)

## Merged output is interleave-corruptible

Concurrent executors write to one shared file description with no line discipline, so long lines
interleave mid-line; there is no grouped, line-buffered, keep-order, or tagged mode. Real, but ranked
last of the fidelity findings on purpose: unlike GNU Parallel, merged stdout is not this tool's
product — the database and the per-task capture files are, and both are byte-faithful today.
Document the current contract now; shape the emitter later.

*Target: 2.10.0 · Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/output-interleaving-line-atomic.md`](issues/output-interleaving-line-atomic.md)

## Failure-budget halt policy

Group-gating is the only early stop. There is no "abort after N failures", no percentage budget, and
no drain-versus-stop-now distinction, so a campaign whose first thousand tasks all fail for one
environmental reason keeps burning allocation until a human notices. Of the unscheduled items this
is the one most likely to be asked for first.

*Target: 2.10.0 · Horizon: long-term · Depends on: the stop-now variant needs the control path;
drain-only does not · Refs: —*
**Seed:** [`issues/failure-budget-halt-policy.md`](issues/failure-budget-halt-policy.md)

## A submitter that owns its retries cannot opt its tasks out of the server's

Retries are one setting per server. A submitter that retries for itself (Nextflow, an agent) gets
its failed tasks run twice: once by HyperShell, in the old work directory, and once by itself. It
can attach only to a site run with `--max-retries 0`, which takes retries away from every other task
there. A per-task mark (a column or a reserved tag) would let it opt out alone.

*Target: 2.10.0 · Horizon: mid-term, small · Depends on: — · Refs: `hypershell/hypershell-nextflow`
(`DESIGN.md` item 9)*
**Seed:** [`issues/per-task-retry-opt-out.md`](issues/per-task-retry-opt-out.md) · *status: unshaped*

## Live progress and ETA

Watching a campaign means polling `hs list --count` against the database the scheduler is contending
on. The numbers are already computed server-side for the autoscaler's pressure signal; a periodic log
line costs almost nothing, and an `hs top`-style view is the larger version.

*Target: 2.10.0 · Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/progress-eta-surface.md`](issues/progress-eta-surface.md)

## Head-of-line blocking on unstartable tasks

An executor that cannot acquire resources for its current task retries that same task rather than
yielding the slot, eroding throughput when declarations are mis-sized — and looking busy while it
does. Zones reduce how often this happens without fixing the mechanism.

*Target: 2.9.x · Horizon: long-term · Depends on: — · Refs: —*
**Seed:** [`issues/head-of-line-blocking.md`](issues/head-of-line-blocking.md)

## A client takes work it cannot start while other clients have room

A client asks for work whenever an executor thread is free, and its request says nothing about free
cores, so it can hold a wide task it cannot start while another client could. On Gautschi the first
client to connect took all six 32-core tasks of a run; the sixth waited 20 s on that node while two
other nodes had cores free. The wait inside one client is the head-of-line entry above. Moving work
between clients needs capacity in the request, or a hand-back.

*Target: 2.10.0 · Horizon: mid-term · Depends on: — · Refs: `issues/head-of-line-blocking.md`;
`hypershell/hypershell-nextflow` (`DESIGN.md` item 10)*
**Seed:** [`issues/client-takes-unstartable-work.md`](issues/client-takes-unstartable-work.md) · *status: unshaped*

## Server CPU per task bounds throughput at small bundle sizes

At bundle size 1, the server's main process runs its own scheduling, confirm and writeback
statements for every task, and the evidence points at its CPU as the throughput ceiling.
`writeback-prebuilt-update` recovers the writeback's share by building that statement once. The
scheduler and confirm paths may carry the same per-call cost, and batching results across bundles
is a further option. Where a commit is slow (SQLite on Lustre), the commits set the ceiling instead:
`nf-hypershell` measured 38 tasks/s there against 77/s with the database on home.

*Target: 2.9.x · Horizon: mid-term · Depends on: `writeback-prebuilt-update` (landed, #73; the
measurement method) · Refs: —*
**Seed:** [`issues/server-per-task-statement-overhead.md`](issues/server-per-task-statement-overhead.md) · *status: unshaped*

## On PostgreSQL, the reached-group read walks a whole index once every task has finished

Since #118, `hs submit` with a database, `hs cluster FILE` and `hs server FILE` read the reached
group once. On PostgreSQL a table whose tasks have all finished makes that read walk the whole
index before it returns nothing: 0.23–0.79 s at 1M rows against about 0.15 s for the earlier form, and
about 2 s at 3M, with identical results. Reading the pass start first would make it one lookup.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs:
`spec/submit-input-checks/REVIEW.md` (cycle 2, *Triaged, not findings*)*
**Seed:** [`issues/reached-group-postgres-finished-scan.md`](issues/reached-group-postgres-finished-scan.md) · *status: unshaped*

---

# Part II — The Turso feature cycle, and test infrastructure

A shaped GOAL, already negotiated with dated clarifications, an agreed appetite, non-goals, and
drafted R-IDs. It carries `status: shaped`: the shaping conversation happened, but acceptance into a
cycle has not. Promotion should re-confirm scope against current `develop` and cite anything that has
drifted, then adopt largely as written. It was planned as 2.9.0's feature cycle; on 2026-11-21 the
maintainer moved Turso to 2.10.0, so 2.9.0 ships without it. The entries after it are `unshaped`
test-infrastructure seeds, split out of shipped cycles or found out of cycle, planned for 2.9.x
patches unless their Target says otherwise.

## Turso provider, partition-attached listing, and S3 captured output

Rotation partitions are write-only archives today — `hs list` cannot see them, and SQLite's `ATTACH`
cap forecloses ever querying a long campaign's history as one thing. Turso holds SQLite's contracts
from our side of the code and removes that cap; the official dialect already ships, so the work is
adoption, validation, and the two capabilities it unlocks. Independently, the `[s3]` extra un-maroons
captured output from the execution host.

*Target: 2.10.0 · Horizon: mid-term · Depends on: the sequencing after zone, cleared (zone shipped
as `zone-autoscale`, #99; relay, the other half, in #82); R4's per-provider seam in `data/core.py`
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

*Target: 2.9.x · Horizon: mid-term · Depends on: — · Refs: split from `timescale` R9 (ruling
2026-11-02)*
**Seed:** [`issues/server-db-test-suite.md`](issues/server-db-test-suite.md) · *status: unshaped*

## A hung integration test costs the whole CI job timeout

Integration tests run each command as a subprocess with no timeout and no per-test time limit, so
a hang runs the CI job to its `timeout-minutes` and the log names no failing test.
`group-advance-strands-lower-group` hit this with a hang in about 1–3% of grouped runs under load.
A subprocess or per-test timeout would turn a hang into a fast, named failure.

*Target: 2.9.x · Horizon: near-term · Depends on: — · Refs:
`spec/group-advance-strands-lower-group/`*
**Seed:** [`issues/integration-test-hang-timeout.md`](issues/integration-test-hang-timeout.md) · *status: unshaped*

## Test teardown by SIGKILL leaks a 2.8 server's named semaphores on macOS

Tests and the queue bench end each `hs` session with `os.killpg(..., SIGKILL)`, which also kills
multiprocessing's resource tracker before it can unlink the process's POSIX named semaphores. A 2.8
server holds several, so on macOS each kill leaks them until reboot, and the pool runs out:
`multiprocessing.Lock()` then fails with ENOSPC and no 2.8 server starts. A 2.9 server makes none,
so on `develop` the leak is live only in the bench's `skew` runs against a 2.8 build. Teardown should
send SIGTERM first and SIGKILL only what is left.

*Target: 2.9.x, 2.8.x · Horizon: near-term, small · Depends on: — · Refs:
`tests/test_cluster_launch.py`, `tests/bench/bench_queue.py`; the 2.8 line (backport)*
**Seed:** [`issues/test-teardown-sigkill-leaks-semaphores.md`](issues/test-teardown-sigkill-leaks-semaphores.md) · *status: unshaped*

## Relay tests can pass a broken relay, and lose a port race to themselves

The R21 judge passes on records that cannot show what four of its checks name; two reconnect
behaviours survive mutation; and the relay workload tests draw a free port, release it, and hand it
to a relay that binds it later, so a parallel copy can take it first. Smaller: the group check's
cancel and `retried` filters are unpinned, and one start-up test resolves a path under the
developer's own database location. Split out of `relay`'s review.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `spec/relay/` C4-3, C4-4;
`relay-hardening` (landed, #103: the relay abort races and rough edges, each with its own tests)*
**Seed:** [`issues/relay-test-strength.md`](issues/relay-test-strength.md) · *status: unshaped*

## The open-file-limit test fails intermittently and passes on a re-run

`test_queue_limits.py`'s open-file-limit test can wait forever for two distinct clients when one
client takes every quick task. It has cost re-runs on recent pull requests; the fix is small and
test-side. The relay site-key test that shared this entry was fixed by `relay-shutdown-tail` (#115).

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: CI on #104 and #110*
**Seed:** [`issues/queue-limits-open-file-test-flaky.md`](issues/queue-limits-open-file-test-flaky.md) · *status: unshaped*

## `pytest -n auto` fails at collection

`AGENTS.md` recommends a parallel local run, but some parametrized test ids differ between workers,
so pytest-xdist refuses to start and every run is serial (about 25 minutes). Stable ids, or an
honest `AGENTS.md`, would fix it.

*Target: 2.9.x · Horizon: near-term, small · Depends on: — · Refs: `AGENTS.md` § Commands*
**Seed:** [`issues/pytest-xdist-collection-ids.md`](issues/pytest-xdist-collection-ids.md) · *status: unshaped*

# Part III — Turso follow-ons

What the Turso provider's vacuum work left open. Turso's multi-process mode is experimental, and the
vacuum went through six review cycles. Each cycle exposed a deeper limit in the engine, and the
`turso` cycle ships `hs initdb --vacuum` under Turso with one known risk flagged in the docs. Both
entries move with Turso to 2.10.0. The first entry gathers the evidence and the second acts on it.

## Reproduce Turso's crash after a killed vacuum without HyperShell

With multi-process Turso, when a vacuum is killed while it folds its compacted image into the
database, the processes that open the database right after it can panic or read an unreadable row.
This happens well below the shared WAL index's capacity. So far it has only been reproduced through
HyperShell's own kill tests, and HyperShell resets Turso's shared-memory file around the vacuum. A
script that uses only `pyturso`, run on macOS and Linux and checked by someone independent, settles
what the claim is. That makes it the headline of the issue we file with the Turso team. The issue
lists the other Turso findings in a table, with an offer to file each one separately.

*Target: 2.10.0 · Horizon: now · Depends on: — (the upstream draft it rewrites lands with `turso`) ·
Refs: feeds the entry below*
**Seed:** [`issues/turso-upstream-repro.md`](issues/turso-upstream-repro.md) · *status: shaped*

## A killed Turso vacuum must not crash the processes that open the database after it

Today an administrator using multi-process Turso has to pause scheduling for a vacuum, and run it
again if it was interrupted. The fix makes a vacuum that is killed at any point leave every process
that opens the database afterwards correct, with scheduling live. Depending on what the
reproduction shows, the route is a Turso release that fixes it, HyperShell serialising the first
opens after an interrupted vacuum, or Turso supporting in-place `VACUUM` in its multi-process mode.

*Target: 2.10.0 · Horizon: when there is evidence or an upstream fix · Depends on:
turso-upstream-repro, or a Turso release · Refs: `spec/turso/` P3, review cycles 4–6*
**Seed:** [`issues/turso-vacuum-concurrent-recovery.md`](issues/turso-vacuum-concurrent-recovery.md) · *status: unshaped*

---

# Part IV — Clients that do not have to trust their server

Three entries, ruled on by the maintainer 2026-11-22. Today a client runs whatever command it is
handed, so whoever controls the server or its database runs code, as the user, on every connected
client. `docs/security.rst` says so: it is the design's trust model, not a hidden defect, so this
work is planned here, not in the security lane. The first entry is the prerequisite, planned for
2.9.0a10: a queue envelope that carries data, not pickled objects, so that a client can distrust its
server at all. Then a 2.10.0 pair, which can ship in either order: signing stops a server from
authoring work, and sandboxing bounds what a signed task can still do.

## The queue's RPC envelope is pickled in both directions

The queue is a `multiprocessing` manager, and every request and reply around HyperShell's own JSON
payloads is pickled. Unpickling can run code chosen by whoever produced the bytes, so each end of a
connection trusts the other with its process. The manager serializes only through its connection's
`send`/`recv`, the standard library already swaps those for its XML-RPC serializer, and HyperShell
owns both connection pairs. So a codec for a closed vocabulary of plain values (requests, five reply
kinds, proxy tokens, `queue.Empty`) replaces pickle with no change to the queues, the dispatcher or
any FSM. A pre-codec peer is refused with a clear error, and there is no pickle fallback. The envelope
is JSON, like the payloads. It adds microseconds per task, against milliseconds for the database, so
msgpack's extra dependency buys nothing.

*Target: 2.9.0 (2.9.0a10) · Horizon: near-term, big · Depends on: — · Refs: prerequisite of both
entries below; `docs/security.rst` ("Pickle-serialized RPC"); `tests/bench/bench_queue.py` (the
no-regression check)*
**Seed:** [`issues/queue-wire-codec.md`](issues/queue-wire-codec.md) · *status: unshaped*

## A sandbox template around each task confines nothing

A user can wrap each task in a sandbox through `-t/--template`, but `{}` is spliced unquoted into a
`shell=True` line, so the task's own shell syntax runs outside the wrapper:
`bwrap … {}` with `true; curl … | sh` sandboxes `true` and runs the pipeline unconfined. With the
templating contract's quoted token, a wrapper receives the whole task as one argument. The cycle
documents client-side sandboxing as a supported pattern: worked templates for `srt`, `bwrap`,
Landlock and Apptainer, a baseline credential-denying policy, and the whole-client alternative. A
`--sandbox` flag with a fail-closed startup probe is the stretch.

*Target: 2.10.0 · Horizon: near-term, in the 2.10.0 alphas · Depends on: the templating contract's
quoted token (R1 of `issues/templating-and-remote-env-contract.md`; this cycle pulls it in if it
has not shipped); the queue envelope above, for the client process itself · Refs: pairs with task
signing below*
**Seed:** [`issues/untrusted-server-client-sandbox.md`](issues/untrusted-server-client-sandbox.md) · *status: unshaped*

## Clients cannot tell a submitter's command from one the server made up

Neither credential HyperShell has speaks to where a task came from: the authkey is shared with the
server, and TLS authenticates the server, which is the party in question. The submitter would sign
each task with an Ed25519 key the server never holds. The server and database carry the signature
untouched, and a client under `--require-signed` runs only what verifies against its own local trust
file, before its expiry and for its audience. `hs update --sign` signs again, but only a command
given with it or a row whose current signature still verifies, never `args` read back from the
database. Open for shaping: the terminal state of a refused task, which today's failure and retry
queries must not pick up, and how much replay of a signed command under retry to allow.

*Target: 2.10.0 · Horizon: near-term, in the 2.10.0 alphas · Depends on: the queue envelope above,
to hold against a compromised server · Refs: pairs with
client-side sandboxing above; `spec/update-client-retry/GOAL.md` (#119: never-ran
sentinels are not retried)*
**Seed:** [`issues/untrusted-server-task-signing.md`](issues/untrusted-server-task-signing.md) · *status: unshaped*

---

# Part V — Beyond 2.9.0

Candidates beyond 2.9.0. Each may be taken up, reshaped or declined; an entry here records the idea
and its open problems, and promises nothing. Its Target says whether a release is planned for it:
the submitter lease is planned for 2.10.0, and relays brought up by `hs cluster` are unscheduled.

## `hs cluster` could bring up its own relays

A single server serves each client connection on its own thread and tops out near 1,000 connected
clients. Relays already lift that ceiling: one server with 100 relays of 100 clients each reaches
10,000. Inside one allocation, though, every relay is brought up by hand. A relay count on
`hs cluster` (and so on `hsx`) would start the whole tree and take it down. The open problems are
splitting the allocation's nodes among the relays, handing each relay its keys the way clients get
theirs, where each relay's database lives, and recovering the work of a relay that dies.

*Target: unscheduled · Horizon: after 2.9.0, maybe · Depends on: relay autoscaling (#100) · Refs:
`hs relay` (#82), key hand-off (#80)*
**Seed:** [`issues/cluster-relay-fanout.md`](issues/cluster-relay-fanout.md) · *status: unshaped*

## A shared site cannot tell a live submitter's tasks from a dead one's

Programs that submit tasks and stay to act on them (the Nextflow executor, hypershell-mcp, agents)
own those tasks, and when one dies its unfinished tasks run on: results nobody will read,
allocations held, and the same work run twice if the owner comes back. A tag can name the owner but
not say whether it is alive, so nothing can clean up unattended on a database many users share. A
*lease* would: a row the submitter claims (kind, key, time to live) and keeps renewing, referenced by
its tasks. A lapsed lease is swept (its unfinished tasks cancelled, never a completed one) and
recorded. A returning owner re-claims its key, with fencing so two live claimants never both own it.
Tasks without a lease behave as today. The open problems are whose clock judges expiry (SQLite
stores naive local times), who sweeps, and reaching tasks already running or held by a relay.

*Target: 2.10.0 · Horizon: after 2.9.0, maybe · Depends on: a conditional `Task.cancel_all`
(`issues/cancel-all-conditional.md`); cancel reach for running tasks (`issues/evicted-client-fencing-and-cancel-reach.md`) · Refs:
`hypershell/hypershell-nextflow` attached mode*
**Seed:** [`issues/submitter-lease.md`](issues/submitter-lease.md) · *status: unshaped*

## Retire the forked queue manager

`SecureManager.start` still serves the queue from a forked child, though every command serves
in-process through `QueueServer`. Only one test uses it, but it keeps a second server path in the
decref and handshake logic. `SecureManager` is public, so retiring the method is a 2.10.0 interface
change. Deferred by `queue-wire-codec`, which gives the path the new codec unchanged.

*Target: 2.10.0 · Horizon: after 2.9.0, small · Depends on: `queue-wire-codec` · Refs:
`src/hypershell/core/queue.py` (`SecureManager.start`, `_child_bootstrap`)*
**Seed:** [`issues/retire-forked-manager.md`](issues/retire-forked-manager.md) · *status: unshaped*

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
- **A run whose tasks failed still exits 0.** Reported as the process contract discarding failures;
  declined by the maintainer (2026-11-14): "a non-zero exit in HyperShell is an application failure.
  Failed tasks are an expected part of any pipeline and do not constitute a non-zero exit for
  HyperShell itself." → [`issues/run-exit-status-failure-aware.md`](issues/run-exit-status-failure-aware.md)

# A note on security work

Security hardening is tracked separately and is **not** listed here. Fixes land as ordinary public
commits and PRs, and `docs/security.rst` discloses known limitations honestly — but a standing,
ranked inventory of unremediated weaknesses in a released version is not something to publish. See
the deferral table in [`AGENTS.md`](AGENTS.md) for how the two lanes divide.
