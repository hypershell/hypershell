# Research brief 03: the evidence plan (RV5 drive, R7 timing, pairings, founding case)

Paths are relative to the repository root. "Drive" means `spec/relay-topology/drive/`.

## 1. How RV5 was reproduced

- **Not with a committed drive.** RV5 came from the cycle-3 reviewer's own harness: Python in
  scratch, never committed. It ran one `hs server --autoscaling` and the relays as separate
  processes on loopback, each run in a throwaway site under `$TMPDIR`, launching through a shim
  that records each launch. It read liveness through libproc. The relays ran heartrate 1 s and
  autoscale period 1 s (`spec/relay-topology/REVIEW.md:347-353`). Nothing on the cluster
  reproduced RV5; the cluster drive has no duplicated-site scenario.
- **The scenario** (`REVIEW.md:430-445`):
  - The server ran `--autoscaling=dynamic -P 2`, with eviction set to 120 s in the harness.
  - Relay A (`[relay.zone.cpu] max = 3`, site-client `timeout = 3`) ran 3 clients on
    12 × `sleep 12`.
  - Then A2 joined site A with `max = 1`.
- **The result.** 3 runs of 3 on the branch, and 1 of 1 on `7ddd16f`: 150 s after A2 joined,
  2 tasks remained, both assigned upstream to A's `cpu` identity. A made 0 launches and its entry
  stayed `target: 0`. A2 made 13 launches meanwhile, each client idling out after 3 s. After A
  got SIGTERM, the 2 tasks were still not done 90 s later, because eviction had not yet come.
- **The mechanism**, from the `lower` run (`REVIEW.md:438-442`):
  1. A's last client logged `Timeout reached` (DEBUG, `client.py:243`) at 12:02:52 but kept
     running prefetched work until 12:03:14.
  2. A logged `Received 1 tasks` (DEBUG, `relay/upstream.py:393`) at 12:03:02 and at 12:03:14.
  3. A then logged `Reverted 1 staged tasks (zone has no client)` (`server.py:398`) and
     `Task group 0 waiting on zone …: 2 tasks, no client serving it` (`server.py:387`).
- **Required ingredients:** task duration well above the site-client idle timeout, so the client
  times out while it is still connected and the relay's bound (`connected × 1 × 2`,
  `relay/upstream.py:318-327`) keeps pulling. A slow holder (A2 `max = 1`) keeps upstream work
  available. The committed `lower` mode (60 × `sleep 1`, timeout 2 s) does **not** strand and
  passes (`evidence/laptop/lower.txt`).

## 2. The drive harness, and what a retire scenario adds

**Laptop: `topology_drive.sh MODE`**

- Runs under `.agents/factory/bin/temp_site.sh`, which makes a throwaway site, sets
  `UV_PROJECT=<repo>` and changes into the site.
- One `hs server --autoscaling=dynamic` with system-style logs (`:171-173`), plus relays started by
  `start_relay` (`:113-133`). Each relay gets its own config, database and port, heartrate 1, and
  launches through `topology_shim.sh`, which writes `launch t= site= pid= word= env= client=` and
  rewrites `-H` to loopback.
- A 1 s poll loop runs until `hs list --remaining --count` is 0 (`:262-331`), with per-mode
  triggers inside it. `lower` starts A2 once A runs 3 cpu clients (`:314-322`), and `stall*` arm
  `topology_trigger.py`, which follows `server.log` for a regex and SIGKILLs.
- Then come idle-exit and no-relaunch checks, the "every live relay still runs" check
  (`:372-379`), and a `grep -E "Relay site left|Evicting client"` (`:380-383`). The relays are
  drained with SIGUSR1 and must exit 0 (`:385-399`).
- Last come the record checks: tasks complete, none failed, zone naming, no `Terminating site
  client`, and peak ≤ max. `TD_EVIDENCE` writes a scrubbed `MODE.txt`.
- **Gap:** relays are started without `HYPERSHELL_LOGGING_STYLE=system`, so relay log lines carry
  no timestamp (see `evidence/laptop/lower.txt`). A timed R7 check needs that variable set in
  `start_relay`.

**Cluster: `topology_cluster.sh`**

- Runs inside an allocation of at least 4 nodes. Every role is an `srun --overlap` job step:
  - node 0 runs `topology_role.py server`, which also submits the workload on cue, publishes
    `progress.json` from a read-only sqlite read every second, and exports the tasks at the end;
  - nodes 1 and 2 run `topology_role.py relay A|B`.
- Clients launch through `topology_launch.sh`, which round-robins nodes into job steps wrapped by
  `topology_record.sh`. The recorder writes a per-client `.rec` with `t_launch`, `t_start`,
  `t_end`, `status`, `killed`, `signal`, and whether the key leaked.
- The shell drives through flag files in the shared run directory (`submit`, `kill-B`, `drain-*`,
  `stop-server`, `abort`) and appends `events`.
- `topology_collect.py` builds a scrubbed JSON record with times relative to the start. It parses
  the server log with the `REGISTERED`/`EVICTED`/`REVERTED`/`LEFT`/`DROPPED`/`DUPLICATE` regexes
  (`:38-44`) and reads the task export (`schedule_time`, `completion_time`, `attempt`).
- `topology_judge.py` runs `common` checks (`:91-186`) plus per-scenario ones (`spread_kill`,
  `fill`), allowing a 1 s cross-node clock skew (`SKEW`, `:35`).
- **Rehearsal:** `topology_rehearse.sh` runs the same script on one machine. A stand-in `srun`
  ignores the resource flags, sets `SLURMD_NODENAME` from `--nodelist` and rewrites `-H` to
  loopback, over 4 named fake nodes. Settings are scaled down (1 s period and heartbeat, 12 s
  eviction, 1 s tasks), followed by `--require spread-kill,fill`.

**Log lines and data available today**

- **Registration:** `Relay site registered (site: S, zone: Z, min:, init:, max: M, clients: N)
  (host: uuid)` (`server.py:820-822`). The per-identity `max` tells A (3) from A2 (1).
- **Duplicate site:** `Site S advertised by two relays (older: <instance> on host, newer:
  <instance> on host) - the newer holds its entries` (`cluster/topology.py:422-424`). It names
  instance keys, not identity uuids.
- **Eviction:** `Evicting client (host: uuid)` and `Reverting orphaned tasks (host: uuid)`
  (`server.py:909-915`).
- **Departure:** `Relay site left (site, zone) (host: uuid)` (`server.py:830`). It is logged on
  eviction *and* on a final heartbeat, so a retiring relay will produce it legitimately.
- **Site dropped:** `Site S left the topology` (`cluster/topology.py:431`).
- **Relay side:** `Topology V (zone, target, seen, launched) - launching N`, `Autoscale client
  disconnected`, and `Drain requested` (`relay/upstream.py:343`).
- **The upstream `task` table:**
  - It has `schedule_time`, `client_id`, `client_host`, `start_time`, `completion_time`,
    `attempt` and `zone` (`data/model.py:330-376`). `client` has `site` and `relay`
    (`:1286-1302`).
  - A revert nulls `schedule_time`, `client_id`, `client_host` and the server columns
    (`revert_columns`, `:439`) and **records no revert time**. Upstream `start_time` arrives only
    with results.
  - So "available upstream" is the moment a held row's `client_id` stops being one of A's
    identities, or becomes NULL. Measure it by sampling, or by a new server INFO line.

**A new `retire` mode (laptop) and scenario (cluster) needs:**

1. **Roles.**
   - A: `cpu.max = 3`, `timeout = 3`. A2: site A, `max = 1`, launch label `A2`, started on the
     `lower` trigger.
   - Tasks: 12 × `echo "$TASK_ID $(date +%s)" >> <site>/runs.log; sleep 12`. The command runs
     under `shell=True` (`client.py:829`) and `TASK_ID` comes from `task_env`. The `runs.log` line
     proves each task ran once (R5).
   - Eviction 120 s, longer than the run, so a green run cannot be rescued by eviction.
2. **A sampler** (Python, stdlib, read-only sqlite, like `topology_role.read_progress`).
   - Every 0.25 s it logs `t id client_id completion_time` for incomplete rows into
     `held.jsonl`.
   - It maps identity uuids to A or A2 from the registration lines.
3. **Checks.**
   - Every task completes, each line once in `runs.log`.
   - A exits **by itself** with status 0 before the drain, by `wait $relay_A`.
   - No `Evicting client` line names any of A's uuids. `Relay site left` for A's uuids is allowed
     only at or after A's superseded line.
   - A launches nothing after the warning. No `Terminating site client`.
   - A2 stays connected; this is the R9 half.
   - **R7 timed:** take `t_mark` from A's first "superseded" log line, which needs the system log
     style. Take `H`, the rows held by A's identities at `t_mark`, minus those that complete under
     A's identity (started, R5). The check is `max(t_available) − t_mark ≤ 3 × heartrate`, which
     is 3 s at heartrate 1.
   - **Non-vacuity, as `stall3`'s "the race happened":** `H` minus the started rows is non-empty,
     or A logs handing back ≥ 1 task. Otherwise the run is INCONCLUSIVE, not PASS.
4. **Recommendation:** the server should log the requeue at INFO (count plus identity), e.g.
   `Requeued N tasks handed back by relay (host: uuid)`. The judge can then time R7 from two log
   lines and use the sampler as cross-check.
5. **Declared amendment.** With the fix, `lower` fails as written: A is a live superseded relay, so
   it now retires and exits. That trips "every live relay still runs" (`:372-379`) and the
   `Relay site left` grep (`:380-383`). Amend `lower` to the retire expectations, or fold it into
   `retire`. `bounce` is unaffected, since A is SIGKILLed and never reads the mark.
6. **Cluster.** The places that change:
   - **Role:** a third relay role `A2` (site A, label A2) in `topology_role.py`. `ZONES` and the
     relay dispatch are keyed by site today (`:40`, `:369`).
   - **Recorder label:** `HSD_SITE=A2` for the recorder while `relay.toml` keeps `site = "A"`.
   - **Shell:** the scenario must be allowed through the gates in `topology_cluster.sh:84-86`,
     plus a start-A2 trigger in the loop (`:213-230`). A2 can run on A's node.
   - **Collector:** collect `relay-A2` (`topology_collect.py:110,142` hard-code A and B) and copy
     the sampler's file.
   - **Judge:** `retire` in `SCENARIOS`, plus exemptions in `common`:
     - `relay.no_terminate` expects exactly 2 relays (`:156`);
     - `launch.placement` expects highmem at A and gpu at B (`:129`);
     - `relay.stayed`/`relay.drained` would flag A's own exit (`:169-181`).
   - **Timing:** heartrate 2 s gives a 6 s bound, judged with `SKEW`.

## 3. Old/new wire pairings

Precedent: every prior pairing ran a `git archive` export of the base `src/` on `PYTHONPATH` with
the same venv, confirmed by `hypershell.__file__`. Examples: relay-topology `compat` four
pairings (`REVIEW.md:61-63,249,398`), `group-advance-retry-gate/REVIEW.md:31` and
`schema-upgrade-step/REVIEW.md:32`. No committed drive does it; the laptop README's "red on the
reviewed head" runs swapped source in the worktree (`evidence/laptop/README.md:37-48`).

Recommended approach:

1. Export the base once: `git archive fb89670 src | tar -x -C "$TMPDIR/hs-base"`.
2. Give `topology_drive.sh` an optional `TD_BASE_SRC` and `TD_BASE_ROLE` (`server` | `A` | `A2`).
   For that role only, `start_relay` / the server `env` gets `PYTHONPATH=$TD_BASE_SRC`.
3. Site clients inherit it through the launcher, so an old relay's clients are old too, as in a
   real earlier release.
4. Print the `hypershell.__file__` of each role in the checks.
5. Use `git archive`, not `git worktree add`, which the sandbox refuses in `.git/worktrees`.
   `fb89670` has no `src/` drift from the base the seeds name (GOAL Problem).

Expected outcomes under R8:

- **Base relay A vs branch server, `retire`:** behaves as today. A strands until eviction. The
  check is that A logs no topology read error; the old `read_topology` ignores unknown keys
  (`core/topology.py:258-273`) unless the protocol number changes. All tasks complete only after A
  is stopped and evicted (eviction 30 s for this pairing).
- **Branch relays vs base server:** no mark is sent; behaviour is today's, with no error.
- **Plus `idle` and `lower` in both pairings**, as the R9 regression guard.

## 4. A fast, deterministic laptop gate

- **Knobs that shrink the run:**
  - relay heartrate 1 s (sets R7's bound to 3 s);
  - server `-P 1`–`2`, relay period 1 s;
  - client timeout 3 s with `sleep 8`–`12` (keep task ≫ timeout + bundlewait);
  - `bundlewait` 1 s.
- **Green estimate.** About 60–120 s, dominated by A2 at `max = 1` serially running the rest.
  `A2 max = 2` or 8–10 tasks keeps it under 2 min.
- **Red estimate.** Today the red run lasts the whole 150 s window. Bound it instead: FAIL once A
  has 0 clients and a held row has stayed on A's identity for more than 20 s. That is well past
  3 × heartrate, and red lands in about 60–80 s. `TD_DEADLINE` 150 bounds the rest.
- **Flakiness.**
  - REVIEW reports 3/3 and 1/1 and no flakes, but the stranding count depends on prefetch timing,
    and the 1 s-task `lower` never strands.
  - Calibrate on `develop` ×3 with the chosen knobs before fixing the gate.
  - Keep the non-vacuity check.

## 5. The founding case

- **Gate guard:**
  `for mode in founding fixed dynamic restart; do .agents/factory/bin/temp_site.sh sh
  "$PWD/spec/zone-autoscale/drive/zone_drive.sh" $mode || exit 1; done`. This is in relay-topology
  P4 verify (`TECH.md:84-87`). `founding` is the default zone, fixed policy, no `--zone`
  (`zone_drive.sh:12,43`), with evidence in `spec/zone-autoscale/evidence/laptop/founding.txt`.
- **Reviewer's base/branch comparison** (`REVIEW.md:397`): `hs cluster FILE
  --autoscaling=dynamic -P 2 -I 1 -Y 3 -T 3 --launcher=<shim>`, 40 × `sleep 1`, no zone. It
  launched at +0.0, +2.0 and +4.0 s, 40/40 done, 3 idle exits, identical on base and branch.
  Repeat it with `TD_BASE_SRC`-style `PYTHONPATH`.
- **Relay without topology (R9):** `sh spec/relay/drive/relay_smoke.sh` (non-publishing server),
  and the `idle` mode (a lone relay stays connected at 0 clients).
