# Research 07 — R8: staging the bounded end-of-run tail investigation

Read-only research at `2ffb5cc` (2.9.0a8). One reduced smoke was run (not a budget run, below).

## 1. What `relay_throughput.py` does today, and the gaps

`spec/relay/drive/relay_throughput.py` runs, on one host over loopback, two runs of the same task
file (`--tasks 2000 --sleep 0.2 --clients 4 --threads 8` = 32 executors): **direct** `hs cluster -N 32`,
then **relay**: `hs submit` into the upstream SQLite DB, `hs server --restart` (upstream, with DB
`<site>/task.db`) and `hs relay -N 4` (relay DB `<site>/relay.db`, `[relay] num_threads = 8`, site
clients launched through a loopback `env` wrapper). It prints each wall time (monotonic, start to
exit of `hs cluster` / the upstream server), the ratio and PASS/FAIL on 1.5×. Each run's site is
`tempfile.mkdtemp('relay-throughput-{direct,relay}-')` under `TMPDIR`, kept; logs are
`cluster.log`, `server.log`, `relay.log` (site clients inherit the relay's stderr, so their lines are
in `relay.log`).

Gaps for R8 (all confirmed in the smoke):
- **Logging cannot be raised from outside.** `prepare()` drops every caller `HYPERSHELL_*` and sets
  `HYPERSHELL_LOGGING_LEVEL=INFO`; `HYPERSHELL_LOGGING_LEVEL=DEBUG python relay_throughput.py` is
  silently INFO.
- **No timestamps.** The default style is `[level] [name] message`; only `HYPERSHELL_LOGGING_STYLE=system`
  carries `YYYY-mm-dd HH:MM:SS.mmm` (and the **host name** and app id: scrub before committing).
- **No tail.** No wall-clock exit time is recorded and nothing reads `completion_time`.
- `completion_time` is stored **naive** (the site client's stamp, copied upstream unchanged). With
  `TZ=UTC` on every process it compares directly with system-style log stamps.
- Key shutdown lines are TRACE, not DEBUG (`Waiting (heartbeat)`, scheduler `backing off N seconds`,
  server `Client disconnected`). DEBUG is enough to segment the tail (below); TRACE (a superset) adds
  the scheduler backoff and the upstream disconnect. Recommend TRACE as "DEBUG or finer", or DEBUG to
  the GOAL's letter; the planner rules.

**Measurement (spec).** Tail = `T_exit − T_last`, where `T_last` = `SELECT max(completion_time)` from
the relay run's `<site>/task.db` after exit (parsed as UTC), and `T_exit` = the stamp of the upstream
`server.log`'s last line (`[hypershell.server] Done`, ms resolution); cross-check with `time.time()`
taken when `watch()` sees the exit (0.5 s poll, reads ~0.3–0.6 s later). Same host in both the laptop
and the cluster job (the driver is single-host), so **no clock skew**; if roles were ever split across
nodes, apply relay-retire's 1 s `SKEW` allowance. Record the direct run's tail too (`cluster.log`) as a
control.

**Segments** (the id of the `max(completion_time)` row ties them together; all DEBUG unless noted):

| Seg | From → to (log) | Design bound |
|-----|-----------------|--------------|
| s1 hold | `T_last` → relay `Returned N results upstream` after it | `relay.bundlewait` 5 s + `poll` 0.1 |
| s2 land | → upstream `Completed task (<id>)` | ≲ 2 s (`DEFAULT_REMOTE_TIMEOUT`) |
| s3 sched | → upstream `Done (scheduler)` | next relay request ≲ 2 s; if not woken, backoff ≤ `server.poll` 30 s (TRACE `backing off`) |
| s4 signal | → upstream `Signaling clients` | ≤ 2 s: `HeartMonitor.get_next` blocks in `heartbeat.get(timeout=2)` before `switch_mode` sees `should_signal` |
| s5 heard | → relay `Upstream server is shutting down` (INFO) | request wait 2 s + gate 0.1 |
| s6 site-stop | → relay `Pulling over and site drained`, then relay-local `Signaling clients` | STOP_CHECK 1 s + the same ≤ 2 s heart-monitor wait |
| s7 site-exit | → client `Disconnect received` … relay `Upstream heartbeat - final` | ~1 s (client heartbeat `no_wait`, 1 s sleep); `CLIENT_GRACE` 5 s / `STOP_TIMEOUT` 10 s if a client misbehaves |
| s8 exit | → upstream `Done (heartbeat)`, `Done` | ≤ 2 s (heart-monitor get) + receiver/confirm sentinels |

**Hypotheses.** 6–9 s:
- **H0 the designed chain** (strongest, new): s1 ≤ 5.1 + s4 ≤ 2 + s6 ≤ 2 + small ≈ 9 s worst case,
  which brackets 6–9 s exactly. Confirmed if every segment sits within its bound and the tail tracks
  s1. The review's `RELAY_BUNDLEWAIT=1` runs (envelope ~5 s) argue against it only if those were the
  tailed runs, which the review does not say.
- H1 upstream scheduler not woken (s3 large; TRACE `backing off 1/2/…/30`). Review: `SERVER_POLL=1` did not remove it.
- H2 heart-monitor signal latency alone (s4/s6 at their 2 s bound).
- H5 SQLite latency on node-local disk under 34 busy processes (s2 large; `Retrying held result`,
  `Contended`, `database is locked`).
- H6 returner starved (s1 > 5.1 s; gaps between `Holding N completed tasks` lines).

~60 s: H3 link trouble (`relay.upstream.deadline` 30 s + backoff ≤ 60 s: relay WARNINGs `trying again`,
`lost with the link`, link-state lines); H4 a site client slow to leave or never connected (`Ended N
site clients`, `did not stop within 10 seconds`, a late `Registered client` in the relay-local log);
H1 with backoff at its 30 s cap twice. Not candidates: autoscaler `period` 60 s (its wait sleeps 1 s and
halts at once), `server.evict` 600 s / `evict_hint` (far larger). The driver's own 60 s relay wait comes
after `T_exit`.

## 2. Cluster contract and the R8 recipes

`retire_cluster.sh` / `topology_cluster.sh` contract (`#!/bin/sh`, `set -eu`): environment only, no
`#SBATCH`, no submission, no site names; run inside an allocation the foreman holds and submits with
its own options. Required `HS` (the `hs` under test; `python` and `hyper-shell` beside it; dirname
prepended to `PATH`; `HSD_PY=$(dirname "$HS")/python`). Optional `HSD_WORK` (shared work dir, default
`./<x>-drive`), `HSD_RESULTS` (default `$HSD_WORK/results`, one `<run id>.json` per run),
`HSD_NODE_TMP` (node-local scratch, default `TMPDIR` or `/tmp`), `HSD_SRUN_ARGS`, `HSD_LABEL` and
`HSD_RUN_ID` (`[A-Za-z0-9._-]+`, validated; exit 64 on bad usage). Every caller `HYPERSHELL_*` is
unset, `TZ=UTC` exported; a refused existing run dir; cleanup by recorded PID on EXIT/HUP/INT/TERM;
nothing deleted; collector writes the record (which self-scans for node, host, user, paths, key:
`record.scrubbed` all 0), judge decides the exit status (0 pass, 1 fail/inconclusive). Evidence:
the foreman copies records unchanged into `spec/<slug>/evidence/cluster/` with a `README.md` table
(record, commit, wall, judge); laptop evidence in `evidence/laptop/` (`README.md` + scrubbed `.txt`).

**Recipe** (files under `spec/relay-hardening/drive/`; do not edit `spec/relay/drive/`, which is the
relay cycle's record):

- `tail_run.py` — loads `relay_throughput.py` by path (`importlib`), wraps `prepare` to set
  `HYPERSHELL_LOGGING_LEVEL` (DEBUG/TRACE), `HYPERSHELL_LOGGING_STYLE=system`, `TZ=UTC`, and `watch`
  to record the wall exit time; runs direct + relay at the R8 defaults (no size flags in the budget
  runs); writes one record JSON per run: commit, label, sizes, both walls and ratio, both tails
  (log and poll), the segment stamps above, the last task id, WARNING/ERROR counts, the kept site
  paths. Exit 0 when the run finished (2000/2000, exit 0), whatever the tail.
- `tail_laptop.sh N` (N ≤ 10, refuses more) — N × `tail_run.py`, `TMPDIR` set to a kept dir outside
  the repo, records to `$HSD_RESULTS`; prints one line per run.
- `tail_cluster.sh` — same contract, **one node** (≥ 36 cores: 32 executors + server + relay + driver),
  no `srun` steps needed. `HSD_RUNS` (default 10, max 10). Per run: `TMPDIR=$HSD_NODE_TMP/$run_id/run-NN`
  (node-local, as in the review), then **copy** both site dirs (logs + DBs) to
  `$HSD_WORK/$run_id/run-NN/` before the next run — the review lost its evidence because scratch was
  node-local. Then `tail_judge.py`. ~45 s per run; 10 runs ≈ 8–10 min; ask 30 min.
- `tail_judge.py PATH…` — per run: `run  tail  direct-tail  s1…s8  flags`; flags `TAIL>5` and, per
  segment, `OVER` its design bound (unexplained). Summary `tail_judge: N runs, K over 5 s, U unexplained`.
  `--require-none-over 5` exits 1 if any run is over (the post-fix check for outcome (a)).
- Evidence: `spec/relay-hardening/evidence/{laptop,cluster}/`: records, `README.md` table, and a
  host-scrubbed window of each run's `server.log`/`relay.log` from `T_last − 10 s` to exit (full TRACE
  logs are ~3–4 MB per run at R8 size; keep them in scratch, path in the record).

## 3. Smoke (not a budget run)

`tail_run`-style wrapper in `$TMPDIR/rh-07`, 200 × 0.05 s, 2 clients × 4 threads, TRACE + system + UTC.
Both runs 200/200, exit 0; all processes exited (driver reaps). **Direct tail 2.30 s; relay tail
5.15 s** (poll-based 5.72 s). Relay segments: s1 1.51 s (returner bundlewait), s2 0.01, s3 0.30,
**s4 1.84 s**, s5 0.00, **s6 1.41 s**, s7 0.03, s8 0.005. So H0 is live: two ≤ 2 s heart-monitor waits
and the ≤ 5 s returner wait already make a >5 s tail on a tiny, healthy run.

## 4. Staging for TECH.md

- **Phase: last**, after R1–R7 land (R8 is about the head). Tooling + judge first in that phase
  (optionally a `tests/test_tail_judge.py` over synthetic records), then the runs.
- **Budget split:** ≤ 7 diagnostic laptop runs, ≥ 3 kept for post-fix verification; every started
  run counts. Cluster job (≤ 10 runs, `tail_cluster.sh`, foreman submits) only if **no** laptop run
  shows a tail > 5 s. No more.
- **Verify:** `HS=.venv/bin/hs spec/relay-hardening/drive/tail_laptop.sh N` then
  `uv run python spec/relay-hardening/drive/tail_judge.py spec/relay-hardening/evidence/laptop`
  (outcome (a): add `--require-none-over 5` on the post-fix records).
- **Outcome (a)** if H0 is the cause: two small, order-preserving fixes fit the appetite — the
  returner sends at once when the relay holds no unfinished task (`store.count_backlog == 0`;
  `relay/upstream.py`), and the server calls `dispatcher.finish()` from `wait_heartbeat` (or
  `switch_mode` checks `should_signal` before blocking) so the shutdown reply does not wait out the
  2 s heartbeat get (`server.py`, every server: a latency change only, invariant §6 order kept; R10
  must say so). Otherwise **(b)**: TECH records each run's tail and segments, the logs kept, the
  hypotheses ruled out, and a narrowed seed.
- **Open question for the gate:** H0's envelope (~9 s) exceeds the 5 s threshold, so the literal
  "cluster only if none > 5 s" may never fire even if the Linux anomaly (excess beyond the design
  bounds) is not reproduced. Suggest: count a run as showing the tail only when a segment is `OVER`
  its bound, or fix H0 first and then apply the 5 s rule.
