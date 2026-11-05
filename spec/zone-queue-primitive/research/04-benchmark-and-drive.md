# Research 04 — R9 benchmark recipe and the multi-node drive

Base: `b819de6`; comparison release `2.8.3`. Prototypes ran on a 10-core arm64 macOS laptop, CPython
3.14 (default start method `spawn`), against the base and 2.8.3 only (the feature does not exist yet).
Prototype files, ready for the build to adapt (not committed):
`$TMPDIR/zqp-bench/bench.py` (A), `$TMPDIR/zqp-bench/drive.sh` + `$TMPDIR/zqp-bench/compare.py` (B).

## A. Local R9 benchmark

**Builds.** One locked, non-editable venv per build, no worktree metadata touched:
`git archive <ref> | tar -x -C $TMPDIR/…/src-<label>` then, in that dir,
`UV_PROJECT_ENVIRONMENT=$TMPDIR/…/venv-<label> uv sync --frozen --no-dev --no-group docs --no-editable`.
The feature build uses the feature worktree's `HEAD` the same way (or `uv pip install <worktree>` into
a fresh venv if it must measure uncommitted code). Non-editable freezes the code under test; `--frozen`
gives both builds the same dependency set when `uv.lock` is unchanged. Preferred over
`git worktree add`: nothing to clean up in the shared `.git`.

**One run** (`one_run` in `bench.py`): fresh temp site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`
in it, `HYPERSHELL_CONFIG_FILE=''`, every other `HYPERSHELL_*` scrubbed, logging `WARNING`), free
loopback port, random 32-hex auth key.
1. Pre-submit M tasks (`true`): `hs submit -f tasks.in -b 5000 -w 1` (not timed).
2. `hs server --restart -H localhost -p PORT -k KEY -b B [--no-tls]`. With no FILE the server would
   read stdin; `--restart` schedules from the DB only and halts at `count_remaining() == 0`, so the
   measured window is pure schedule→run→return. Same behaviour in 2.8.3.
3. Readiness without a probe connection: poll `psutil` `net_connections` of the server's process tree
   for LISTEN on PORT (at base the listener lives in the manager child).
4. N × `hs client -H localhost -p PORT -k KEY -N T -b CB -w 1 -T 60 [--no-tls]`; they exit on the
   server's disconnect sentinel (`-T` is only a backstop). Same-site clients share the `<auto>` cert.
5. A sampler thread (0.2 s) records peaks for the server process **and its whole tree**: RSS, threads,
   `num_fds`, recursive child count, child cmdlines.
6. From SQLite: `MIN(schedule_time)`, sorted `completion_time`s, exit-status histogram, `retried`,
   incomplete count.

**Metric.** Primary: steady-state `p10_p90_tasks_per_s` = 0.8·M / (t₉₀ − t₁₀) over completion times.
The full span (first schedule → last completion) also counts client start-up, because the scheduler
stamps and posts the first bundle (queue size 1) before any client connects. Both are reported.

**Making the queue the bottleneck.** `-b 1` on server and client with the default `queuesize = 1`:
every task is one bundle and one round trip through `scheduled`, `confirmed` and `completed`.
Evidence: 16 slots of `true` reach only ~230 tasks/s at ~2 cores busy, so it is latency-bound, not
spawn-bound. A `-b 50` profile (client `-b 50 -w 1`) shows the bundled regime.

**Baseline at `b819de6`** (M=5000, 3 repeats, TLS default; tasks/s):

| config (N clients × T threads, bundle) | TLS | p10–p90 median [min–max] | spread | full-span median |
|---|---|---|---|---|
| 4×4, b=1 | on | 240.8 [231.2–242.6] | 11.4 (4.7%) | 233.9 |
| 4×4, b=1 | off | 172.1 [170.4–201.5] | 31.1 (18%) | 172.4 |
| 16×1, b=1 | on | 224.3 [199.7–232.6] | 33.0 (15%) | 207.4 |
| 16×1, b=1 | off | 227.2 [214.6–252.4] | 37.8 (17%) | 216.9 |
| 4×4, b=50 (M=20000) | on | 836.5 [820.0–839.8] | 19.8 (2.4%) | 821.2 |

All 15 runs: server rc 0, every client rc 0, 0 incomplete, histogram all `0`. Server wall time is
span + ~0.6–0.8 s start-up + ~3–4 s shutdown drain after the last completion.
`--no-tls` is *not* faster at 4×4 (repeatable across 3 runs); compare only like with like.

**Footprint at `b819de6`** (peaks; identical across repeats ±1):

| | own RSS | own threads | own FDs | children | tree RSS | tree threads | tree FDs |
|---|---|---|---|---|---|---|---|
| N=4 | 95 MB | 7 | 51–53 | 2 | 204 MB | 25 | 116–117 |
| N=16 | 96 MB | 7 | 52–53 | 2 | 208 MB | 61 | 153 |

The two children under `spawn` are the manager process and `multiprocessing.resource_tracker`
(started for the `JoinableQueue` semaphores). Under `fork` expect only the manager child; Linux
3.14 defaults to `forkserver`, so record the start method with every result. Tree threads grow
~3 per client: the manager's per-connection handler threads. In the feature those threads move into
the server process, so **its own thread count will rise by ~3 per client by design**; gate on
**tree** totals, never on own counts. Expected feature footprint: 0 children, tree FDs below base
(no pipes, no second interpreter), tree RSS roughly halved.

**Gate (proposed for PLAN).** Interleave builds A B A B … (`bench.py --build base=VENV --build
feature=VENV`; the first is the baseline) to cancel thermal and background drift. Per config:
PASS if `median_feature ≥ median_base − max(spread_base, spread_feature, 5% · median_base)`, and
peak tree `children` and tree FDs ≤ base. `bench.py` prints this verdict and exits non-zero on
failure. Spreads of 5–18% at 3 repeats mean **5 repeats** for the gating run. Profiles:
- quick (~6 min): 4×4 b=1, TLS on and off, M=5000, 3 repeats.
- gate (~25 min): add 16×1 b=1 on/off and 4×4 b=50 on, 5 repeats.

**A/B check of the gate (2.8.3 vs `b819de6`, interleaved, 4×4 b=1 TLS on, 3 repeats each).** At
M=2000: 2.8.3 228.2 [225.6–242.8], b819de6 200.8 [198.7–216.4], an apparent −27. At M=5000:
2.8.3 213.6 [192.0–225.0], b819de6 222.9 [204.8–240.6], so +9.3 against a tolerance of 35.8: PASS,
with footprints identical (2 children, 117 tree FDs, 25 tree threads). The M=2000 gap was noise and
reversed at M=5000. So use M ≥ 5000, interleave the builds, and expect ~15% spread even at 4×4.
A delta of about ±10% at 3 repeats means nothing.

**Where it lives.** `tests/bench/bench_queue.py`. `pyproject.toml` sets no `testpaths` or
`python_files`, so pytest collects only `test_*.py` / `*_test.py`. Any name outside those patterns is
never collected (no `__init__.py` needed; never name it `*_test.py`). `tests/` already ships in the
sdist. Run it with the baseline venv's python, which has `psutil`.

## B. Multi-node drive (`tests/cluster/drive.sh`, POSIX sh)

Prototype: `$TMPDIR/zqp-bench/drive.sh`. It ran unchanged against both 2.8.3 and `b819de6` archives
locally (one server "node" and two client "nodes" as separate `HSD_LOCAL` dirs, sharing one
`HSD_RESULTS`), TLS on and off. Both gave identical results: 2000 rows, `{0: 1980, 3: 20}`, every rc
0, 2 clients seen.

**Interface: environment only.** `HSD_ROLE` (`server` | `client`), `HSD_ARCHIVE`, `HSD_RESULTS`
(shared), `HSD_RUN_ID`, `HSD_AUTH`, `HSD_HOST`, `HSD_BIND`, `HSD_PORT`, `HSD_TLS`, `HSD_LOCAL`
(node-local scratch, default `$TMPDIR`), `HSD_TASKS`, `HSD_FAIL_EVERY`, `HSD_COMMAND`,
`HSD_CLIENTS` (recorded), `HSD_THREADS`, `HSD_BUNDLESIZE`, `HSD_CLIENT_BUNDLESIZE`,
`HSD_CLIENT_BUNDLEWAIT`, `HSD_IDLE`, `HSD_TIMEOUT`, `HSD_WAIT`, `HSD_CLIENT_INDEX`. Any launcher
(job script, mpirun, ssh) starts one `server` and N `client`s with the same `HSD_RUN_ID`. Nothing is
scheduler-specific. The auth key travels as `HYPERSHELL_SERVER_AUTH`, which server and client both
read (`config.server.auth`) in both versions. The CLI flags used are identical between 2.8.3 and
`b819de6`: `hs server --help` and `hs client --help` diff clean, and `hs submit` only gained
`--from-json`, `--repeat` and `--update`.

**Archive (one per version, one unpack per node).** Layout: `python/` (a python-build-standalone
interpreter from `uv python install X.Y --install-dir …`, relocatable by design) + `site/` +
`LABEL`. `site/` comes from `uv export --frozen --no-dev --no-group docs --no-emit-project
--no-hashes` at the ref, then `uv pip install --python <that interpreter> --target site -r req.txt
<src>`, then `compileall`. About 45 MB gzipped. It runs as `python/bin/python3 -c 'from hypershell
import main; …' <args>` with `PYTHONPATH=site`, `PYTHONNOUSERSITE=1` and
`PYTHONDONTWRITEBYTECODE=1`: no absolute paths, no console-script shebangs, no per-node install
step. It is preferred over (a) `uv venv --relocatable`, whose `pyvenv.cfg` still points at a base
interpreter by absolute path that must exist on every node, and (b) a per-node venv plus a wheelhouse
install, which adds resolver and pip I/O per node and needs a matching Python already on the node
(the system python may be too old for ≥3.11). Build it on a host with the nodes' OS and arch, or
cross-build with `uv pip install --python-platform … --python-version …` and the matching
`uv python install` target. Unpacking: the first process on a node wins `mkdir $node/unpack.lock` and
extracts; the others wait for `$node/unpacked`. The venv, sites, task DB and logs stay node-local.
Only small results go to `HSD_RESULTS`.

**Server role.** Generate M tasks (every `HSD_FAIL_EVERY`-th is `exit 3`, the rest `HSD_COMMAND`) →
`hs submit -f` into node-local SQLite (timed separately) → `hs server --restart -H $HSD_BIND …` in the
background, launched directly so `$!` is the server pid. A watchdog loop sends TERM, then KILL, after
`HSD_TIMEOUT`. The Python sampler waits for LISTEN on the port via psutil. When it sees it, it
atomically publishes `server.crt` (TLS: the server's `<auto>` certificate, which clients pass as `--tls-ca`; works unchanged in both
versions) and then `server.ready` to the shared run dir, then
samples tree peaks until the server exits. **Done** = the scheduler's own stop rule
(`count_remaining() == 0`). The server then sends one disconnect sentinel per heartbeat-registered
client, drains the receiver and confirm threads, and exits. The server writes `server.json` and
`server.log.gz`.

**Client role.** Wait for `server.ready` on shared storage (poll 1 s, up to `HSD_WAIT`, else exit 71).
This replaces a TCP probe: `hs client` does not retry a refused connection, and a raw probe would
exercise the accept path differently in the two builds. Then `hs client -H … -N T -b … -w … -T
$HSD_IDLE (--tls-ca server.crt | --no-tls)`. It exits on the sentinel; `-T` idle is the backstop for
a client that never receives one. It writes `client-<name>.json` (rc, start, end) and keeps its log
only on failure.

**Recorded per run** (`server.json`): label, run id, TLS, M, N expected/seen
(`COUNT(DISTINCT client_id)`), T, bundle size, server rc, submit s, server wall s, DB span, full and
p10–p90 tasks/s, rows, retried, incomplete, exit-status histogram, and tree peaks (children,
threads, FDs, RSS; own values too). Add `multiprocessing` start method and Python version when
productionising.

**Comparison** (`compare.py RESULTS 2.8.3 <new>`). Group by (TLS, N, T, M, bundle). Results must be
identical: histogram equal across all runs and labels, rows = M, 0 incomplete, all rc 0. Throughput
uses the same rule as A on p10–p90 medians (≥3 runs per label, interleaved). With 1 run per label
the spread is 0 and the verdict is meaningless; the prototype showed exactly that false
"REGRESSED" on a single no-TLS pair.

**Task mix.** Plain `true` plus 1% `exit 3` with no retries (`-r 0`, the default). **Groups and
retries are excluded** from the 2.8.3 comparison. `b819de6` itself (#68, group-advance-retry-gate)
changed when the scheduler advances a group or stops with a retry pending, and 2.8.3 can legitimately
finish differently, so "unchanged results" would not hold for reasons outside this cycle. R8's
grouped retry workload belongs to the local base-vs-feature check only.

**Recommendation:** run three labels on the cluster (2.8.3, `b819de6`, feature), interleaved and with
≥3 (better 5) runs each, so any gap against 2.8.3 can be attributed to this cycle or to the 79 commits
since 2.8.3. Locally, 2.8.3 and `b819de6` are equal within spread (§A).
