# 06 — Evidence harness for zone-dispatch (R1, R4, R5, R14)

Read-only brief. Line refs are to the worktree at `b54d685` (Z1 head `aa55e86` plus the Z2 GOAL).

## 1. R1 throughput gate: reuse `bench_queue.py gate`

- **`--base 2.8.3` works as is.** The tag exists (`git tag -l`: 2.8.0–2.8.3, 2.9.0a1, 2.9.0a2) and
  carries `uv.lock`. `resolve()` takes any ref (`bench_queue.py:65-68`), and `build()` installs it
  with `git archive` + `uv sync --frozen` (`:71-90`). A PyPI version isn't needed and isn't supported.
- **Each side runs its own matched build.** `run_gate_once` submits with the same venv and serves
  with `serve(site, venv, venv, …)` (`:380-381`), so the wire break can't mix sides. `parity` runs
  matched builds too (`:283-285`).
- **The tolerance doesn't meet R1's bar.** The gate passes when `mf >= mb - max(base spread,
  feature spread, 0.05·mb)` (`:416-417`). That was Z1's R9 rule, "within spread counts as equal".
  R1 asks for a fixed **≥ 95% of 2.8.3's median**. Laptop spreads run 30–77 tasks/s at b=1
  (Z1 TECH P5 built note), so the spread rule can accept a 20% regression.
  - **Extension:** add `--min-ratio R` to `gate` (and to `compare.py`). With it, `fast = mf >= R·mb`,
    and the noise tolerance is printed for information only.
  - The footprint check (tree children and tree FDs no higher, `:420`) and the clean check (`:421`)
    already match R1 as read for Z1 (whole server tree).
- **The configs only exercise the advertise path.** Every config passes the client an explicit
  `-b` (`:382`). Under R2 that is the advertise path, not R1's default path.
  - **Extension:** let the client-bundle field be `None`, meaning `-b` is omitted, and add
    `4x4 b=1 tls default` and `4x4 b=50 tls default`: server `-b 50`, client with no `-b`, which
    also exercises the server-side size on 2.8.3.
  - Keep the existing configs. They compare the advertise path with 2.8.3 at equal sizes.

## 2. `parity` and `compat` after Z2

**`parity --base 2.8.3 --feature HEAD`** should still hold. `normalise` picks named columns
(`:275-279`), so the new `task.zone` column is ignored. Two changes are needed:

- **(a) Help diffs.** The byte-identical `--help` check (`:311-315`) will now fail by design
  (`--zone`, maybe a cap flag). Add `--skip-help`, or compare only `hs server --help` when the cap
  isn't user-facing. R13 covers help text instead.
- **(b) Group order.** Add the group-order check from `test_queue_workload.py:124-126` to
  `cmd_parity` (R1: "same group order").
- **Second run.** Also run parity against the branch base (Z1 head or `develop`) as a control.

**`compat` becomes an R5 check.** The current `cmd_compat` (`:322-337`) requires success, so it is
now expected to fail. Replace it with a `skew` subcommand: `--new REF --old REF [--bound S]`, run TLS
on and off. Every process starts with `start_new_session=True` and is killed by group on a bound.
`serve` today kills only the pid (`:199`, `:207`). Two legs:

- **Leg A: old client against the new server.**
  1. Submit 200 tasks of `sleep 0.05` with the new build.
  2. Start the new server, then one new client (`-T 60`).
  3. Once the new client logs its first `Running task`, start an old client **without `-T`**, so
     only the server's disconnect can end it.
  4. Assert:
     - the old client exits within `--bound` (default 30 s) *while the server is still alive*;
     - `server.log` matches `/too old/i`;
     - the old client's log has no `Running task` line;
     - the new client completes every task (rows = M, all exit 0);
     - the server and the new client exit 0.
- **Leg B: new client against an old server**, for `--old 2.8.3` and `--old 2.9.0a2` (both tags
  have locks).
  1. Submit with the old build and start the old server.
  2. Start the new client.
  3. Assert:
     - the client exits **non-zero** within `--bound`;
     - its stderr has exactly one error line matching `/version/i`;
     - the old server is still alive (record it; R5 doesn't require it).
  4. Then kill the old server's group.

## 3. Loopback mixed-zone integration test (R14)

The test is `tests/test_zone_workload.py` (`@mark.integration`). It uses `temp_site`, which sets
DEBUG logging (`conftest.py:48-56`), plus `create_taskfile` (`tests/__init__.py:59`) and
`listening`, imported from `tests.test_queue_workload` (`:35-49`).

**Workload:**
- Groups 0–2, each with 6 tasks in zone `cpu`, 6 in `gpu` and 6 unzoned.
- Each zone is submitted with its own `hs submit --zone … -f …`.
- Every task's command is `echo "$ZONE_MARK"`.
- One `gpu` task in group 0 is `[ "$TASK_ATTEMPT" -ge 2 ] || exit 3; echo "$ZONE_MARK"`, so it
  fails on its first attempt only. `TASK_ATTEMPT` is exported because `task_env` flattens every
  task column (`client.py:448-450`). No marker file is needed.

**Processes:**
- One server: `-r 1`, `-b 2`, cap 3.
- Five clients, each given `ZONE_MARK` in its Popen env (tasks inherit `os.environ`,
  `client.py:448`):
  - `cpu -b 3` and `cpu` with no `-b`;
  - `gpu -b 2`;
  - unzoned `-b 4` and unzoned with no `-b`.
- Every process has `start_new_session=True` and `-T 30`, and the whole run has a 90 s deadline.
  In `finally`, `os.killpg` every process still alive.

**Assertions:**
- Every process exits 0, and rows = 54 + 1.
- **Zone match:** for every row, including the failed first attempt, `lib/task/<id>.out` equals the
  task's zone name, from `task JOIN zone`, with the reserved id mapped to `default`.
- **Retry:** the retry pair links (`previous_id` / `next_id`) and has an equal `zone` (R9).
- **Group order:** global group order holds (R10).
- **Delivery:** each task id appears in exactly one client's `Running task (<id>)` line
  (`client.py:775`, INFO).
- **Bundle sizes:** every client's `Received (\d+) tasks` (`client.py:228`, DEBUG) is ≤ min(its
  advertised size, or the server's `-b` when it advertises none, and the cap). The sum over clients
  equals the row count, so no delivery is duplicated.

Don't assert that a reply is *full* here, because staging can race the first request. Full and
partial sizes belong in deterministic unit tests (see §5).

## 4. Multi-node drive: heterogeneous mode

**`drive.sh` knobs** (the defaults keep Z1's behaviour and evidence comparable):

| Knob | Role | Meaning |
|---|---|---|
| `HSD_ZONES` | server | Submit mix, e.g. `default:2 cpu:1 gpu:1`. Empty = homogeneous. When set, writes one taskfile per zone and runs `hs submit [--zone Z]`. |
| `HSD_GROUPS` (default 1) | server | Tasks are tagged round-robin `group:g`. |
| `HSD_RETRIES` (default 0) | server | Server `-r`. |
| `HSD_CAP` | server | Server cap flag; the name is the plan's choice. |
| `HSD_ZONE` | client | Empty = no `--zone`. Exports `HSD_ZONE_MARK=${HSD_ZONE:-default}`. |
| `HSD_CLIENT_ADVERTISE` (default 1) | client | 0 = omit `-b`. Today `-b` is always passed (`drive.sh:213`). |
| `HSD_CLIENT_LOG` (zoned: DEBUG) | client | Client log level. |

**Zoned task command:** `[ "$HSD_ZONE_MARK" = Z ] || exit 7; [ "$TASK_ATTEMPT" -ge 2 ] || exit 3`
on every `HSD_FAIL_EVERY`-th task, then `$HSD_COMMAND`. A task run in the wrong zone shows up in
`exit_hist` as status `7`, with no per-task data leaving the node.

**`server.json` adds:**
- `zones`, `groups`, `retries`, `cap`;
- `per_zone {name: {rows, retried, exit_hist, distinct_clients}}`;
- `retry_zone_mismatch`: a self-join on `previous_id` where the zones differ;
- `evicted`, from `client.evicted`;
- `unassigned`: rows with a null `client_id`;
- `group_order_ok`.

Zone names are user labels, so they are safe to publish.

**`client-N.json` adds:** `zone`, `advertise` (the size, or null) and `received` (the sum of
`Received N`). It also adds `bundles`, `max_bundle` and `bundle_hist`, parsed from the node-local
log after exit. The parse keeps counts only.

**`compare.py` changes:**
- `config_key` (`:62-64`) gains `zones`, `cap` and advertise, so modes never pool.
- `--min-ratio 0.95` replaces the spread rule (`:154-155`) when given.
- **Zoned checks (`--zoned`)**, run for the candidate only:
  - completion: rc 0 everywhere, rows = M + failures, 0 incomplete, final attempts 0;
  - no exit `7`;
  - `retry_zone_mismatch == 0`;
  - Σ `received` = rows, with `evicted == 0` and `unassigned == 0` (no duplicate delivery);
  - each client's `max_bundle` ≤ min(advertise, or the server's `-b`, and the cap);
  - `group_order_ok`.
- **Homogeneous throughput:** the existing check, run with `HSD_CLIENT_ADVERTISE=0` on both labels
  against `2.8.3`.

**Phases** (Z1's External-wait pattern, Z1 TECH `:131-138`, P6/P7 `:573-652`):
- **Run A:** build the extensions. `smoke.sh` adds one zoned TLS-on run (server plus 5 clients,
  mixed zones and advertise), runs `compare.py --zoned`, and keeps the host/user grep (`:69-73`).
  Then hand back.
- **Run B:** blocked until `spec/zone-dispatch/evidence/cluster/` exists. It is then judged.

## 5. Observing R4 and bundle sizes

**Least invasive:** use the existing client DEBUG line `Received {n} tasks ({host}: {uuid})`
(`client.py:228`) and the INFO `Running task ({id})` (`:775`). Both need no new code. TECH must
**pin both texts** as test contracts, since Z2 rewrites the client scheduler.

**Add one server DEBUG line per reply** in the request callable: `Sent {n} of {asked} tasks (zone
{name}) to client ({uuid})`. It costs the same as today's per-bundle `Scheduled {n} tasks`
(`server.py:255`). Log "client too old" at WARNING, so it appears at the drive's WARNING level.

**No test hooks in production code.** Get determinism from unit tests that call the request
callable directly (in the style of `test_server_fsm.py`'s `served` / `task_db` fixtures):
- K threads request at once from one staged pool. Their replies must be pairwise disjoint and sum
  to what was staged.
- Reply size = min(asked, cap, ready), and an empty pool returns empty within the timeout.

## Recommendation

```
uv run python tests/bench/bench_queue.py gate   --base 2.8.3 --feature HEAD --repeats 5 --min-ratio 0.95
uv run python tests/bench/bench_queue.py parity --base 2.8.3 --feature HEAD --skip-help
uv run python tests/bench/bench_queue.py skew   --new HEAD --old 2.8.3   --bound 30
uv run python tests/bench/bench_queue.py skew   --new HEAD --old 2.9.0a2 --bound 30
uv run pytest -v -m integration tests/test_zone_workload.py   (then the full suite)
sh tests/cluster/smoke.sh                                       (run A; zoned rehearsal included)
uv run python tests/cluster/compare.py spec/zone-dispatch/evidence/cluster \
    --baseline 2.8.3 --candidate feature --control 2.9.0a2 --min-ratio 0.95 --zoned feature   (run B)
```

**Drive:** add the knobs and records in §4. Zoned runs are for correctness; throughput is judged
only on the homogeneous runs with `HSD_CLIENT_ADVERTISE=0`.

**Risks:**
- **(1)** A strict 95% bar on a noisy laptop may flake. Use 7 repeats or `--only` reruns and record
  the spread. Don't loosen R1.
- **(2)** If Z2 renames the pinned log lines, R4 and bundle-size observation break silently, so the
  assertions must fail when they find zero matches.
- **(3)** In leg A, an old client could exit through its own idle path rather than the disconnect.
  Run it without `-T` and assert the server is alive when it exits.
- **(4)** DEBUG client logs slow zoned cluster runs. That is acceptable, because those runs judge no
  throughput.
- **(5)** The bound for the old client depends on the plan's disconnect mechanism (heartrate is
  10 s by default). Set `--bound` from PLAN.
