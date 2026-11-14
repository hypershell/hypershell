# GOAL — A cluster whose clients all fail says so and exits; one advertised address for every launch mode

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** cluster-launch-failures
- **kind:** fix
- **appetite:** big  ·  *Two `small` seeds promoted together (Clarifications). Together they touch
  every launch mode (`cluster/remote.py`, `cluster/ssh.py`, `cluster/launch.py`, both
  high-blast-radius), the shared `AutoScaler` the relay's fleets are built on, `relay/site.py`, a
  new option and configuration key, and their docs; and the plan has real questions to answer (when
  "tasks remain" is true without racing the last results, how a failed launch is told from one that
  registered). That is a research pass and more than one phase, so `big`.*

## Problem

`hs cluster` starts a server, launches clients, and waits. When every client it launched exits
early, it keeps waiting with work left and nobody to run it: nothing names the failure and the
command never exits. The user sees the server's "listening" line, then silence. Reproduced on
`develop` (`4400e04`) for this shaping: `hs cluster TASKFILE --no-db --no-tls --launcher false` runs
`false hs client …`, which exits 1 at once; the cluster logged `Server listening on 0.0.0.0:…`,
nothing else, and was still running when killed after 20 s. Typical causes are a key that does not
reach the client (a launcher that does not pass on the environment; the client already says so,
`LAUNCH_ENV_HINT`, `src/hypershell/client.py:1147`), a TLS setting the client does not share, a
client that cannot reach the address it was told, or a bad `--remote-exe`.

Where it waits, on `develop`:

- **Launcher and `--mpi` modes.** `RemoteCluster.run_with_exceptions` waits on the one launcher
  process, ignores its exit status, then joins the server (`src/hypershell/cluster/remote.py:311-312`).
- **`--ssh` mode.** `SSHCluster.run_with_exceptions` waits on each `ssh` process in turn, ignores
  every exit status, then joins the server (`src/hypershell/cluster/ssh.py:310-312`).
- **The server does not stop for want of clients.** Dispatch is demand-driven: with no client asking,
  the scheduler waits on that need and re-enters LOAD (`src/hypershell/server.py:243-253`), and it
  finishes only when nothing is left to schedule (`:262`). The heartbeat monitor finishes only once
  scheduling is done (`:905`), and a client that never connected sends no heartbeat to be evicted.
- **Autoscaling** never ends either. Each check drops exited clients and warns for each non-zero exit
  (`LaunchActuator.clean`, `src/hypershell/cluster/launch.py:310-323`), and the next check launches
  another, since the fleet is empty and work remains (`zone_demand`,
  `src/hypershell/cluster/demand.py:157-171`). The server runs forever
  (`src/hypershell/cluster/remote.py:870`), so the result is one failing launch per period, forever.

One common way every client fails is the address it is told. `hs cluster` binds its server to `-H`
and launches clients with a `-H` of their own, the address they dial, but what it tells them depends
on the mode:

- **Launcher and `--mpi`** use the bind address unless it is `0.0.0.0`, and only then the host's own
  name (`src/hypershell/cluster/remote.py:288`).
- **`--ssh`** always passes the host's own name (`src/hypershell/cluster/ssh.py:283`).
- **Autoscaling** always passes the host's own name (`src/hypershell/cluster/remote.py:881`).
- **A relay's site clients** (`hs relay`) always dial the host's own name, whatever the relay's
  `--bind` (`src/hypershell/relay/site.py:172`); the seed did not name this one.

In the remote modes the default bind `localhost` is widened to `0.0.0.0`
(`src/hypershell/cluster/__init__.py:300`, `:305`, `:315`, `:321`), so the common case agrees. It
breaks when a user binds one interface on purpose, such as a cluster's internal network address:
under `--ssh` or autoscaling the server listens only there, but the clients are told a name that may
resolve to another interface or not at all, and every client fails. Nor is there any way to name the
advertised address apart from the bind: a server bound to `0.0.0.0` behind a name its clients must
use, such as the name its TLS certificate is verified against, has no option for it. With the
default CA verification and no `servername`, a client checks the certificate against the host it
dialed (`src/hypershell/core/queue.py:319-321`, `src/hypershell/core/tls.py:186-189`).

Both defects are **pre-existing**: the launcher wait, the server join and the `HOSTNAME` in `--ssh`
and autoscaling are all in 2.8.0, and the `RemoteCluster` rule came with `893cb81`. Both were found
out of cycle on 2026-11-05.

## Outcome / vision

A cluster that cannot run its work says why and exits. When every client it launched is gone and
work remains, `hs cluster` stops its server and exits non-zero with one error naming what it
launched, how each exited, and the address the clients were told. A client that exits non-zero is
logged when it exits, in every mode, so a partial failure is visible too. Autoscaling stops
relaunching into a failure it cannot fix, after a small, configurable number of failed launches
while it has no working client. A run whose clients do the work ends exactly as it does today.

One rule decides the address every launch mode tells its clients: the bind address when the server
binds one address, the host's own name when it binds every interface. `hs cluster --advertise ADDR`
(or a configuration key) overrides it, for a name the clients must use that differs from the bind,
such as a certificate's name.

## Acceptance criteria (the contract)

Failure of every client:

- **R1** — WHEN every client process `hs cluster` launched in launcher, `--mpi` or `--ssh` mode has
  exited, IF the server still holds tasks not completed, THEN the cluster SHALL stop its server and
  exit with a non-zero status within 10 s of the last exit (default settings), after one error
  that says every launched client has exited with tasks remaining and names the processes it
  launched (the launcher command in launcher and `--mpi` modes; each host in `--ssh` mode), each
  one's exit status, and the address the clients were told to dial. This holds however the clients
  ended (non-zero, zero, or by a signal) and whether or not any had connected; it holds in
  `--forever` and `--restart` runs too, whenever the last client is gone and tasks are pending.
- **R2** — WHEN a client process launched by `hs cluster` (any remote mode, autoscaling included)
  exits with a non-zero status, the cluster SHALL log a warning naming that process (its host in
  `--ssh` mode) and its exit status: within a few seconds of the exit in launcher, `--mpi` and
  `--ssh` modes, and no later than the next check under autoscaling (as today). Under `--ssh`, one
  host's exit SHALL be reported while other hosts' clients still run.
- **R3** — WHILE autoscaling (`hs cluster --autoscaling`), IF a configured number of launched clients
  in a row exit non-zero while no client of the cluster is registered with the server, THEN the
  autoscaler SHALL launch no more and the cluster SHALL stop its server and exit non-zero, after one
  error naming the number of failed launches, their exit statuses, and the address the clients were
  told. The number SHALL be the configuration key `autoscale.failed_launches` (environment
  `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES`), default 3; 0 SHALL keep today's behaviour of relaunching
  without end. A launched client that exits 0, or any client registered with the server, SHALL end
  the run of failures.
- **R4** — A cluster whose clients complete the work SHALL end as it does today: the same exit
  status (zero when every task succeeded), every result recorded, and no new error. A client that
  exits non-zero after the work is complete is warned about (R2) but SHALL NOT make such a run fail.
  A `--forever` cluster with no task pending SHALL keep waiting as today when its clients leave.
- **R5** — A relay's site fleets (`hs relay`) and a server autoscaling with relay sites (`hs server
  --autoscaling`) SHALL keep today's failure behaviour: a relay whose site clients exit non-zero
  keeps relaunching under its fleet rules, warns for each such exit as today, and does not exit for
  it; R3's limit does not apply to them.

The advertised address:

- **R6** — WHERE no advertised address is given, every launch that starts clients (`hs cluster` in
  launcher, `--mpi`, `--ssh` and autoscaling modes, and a relay's site clients) SHALL tell its
  clients the bind address when the server binds one specific address, and the host's own name when
  it binds every interface (`0.0.0.0`). The default bind `localhost` of a remote `hs cluster` keeps
  widening to `0.0.0.0`, so a cluster run with no `-H` advertises what it does today.
- **R7** — WHERE the user gives an advertised address, by `hs cluster --advertise ADDR` or the
  configuration key `server.advertise` (environment `HYPERSHELL_SERVER_ADVERTISE`), every remote
  `hs cluster` mode SHALL tell its clients exactly that address, whatever the bind; the option SHALL
  take precedence over the key. `--advertise` on a local cluster (no launched clients) SHALL be
  refused with a usage error naming the option.
- **R8** — `--advertise` SHALL be documented beside `-H` in `hs cluster --help`, its
  `docs/_include/cluster_*.rst` snippets, the man page and the shell completions;
  `server.advertise` and `autoscale.failed_launches` in the configuration reference; each with
  `.. versionadded:: 2.9.0`. The docs SHALL say which address each mode advertises (R6), that under
  TLS the advertised name must be one the client's certificate check accepts, and that a cluster
  whose clients are all gone with work remaining exits non-zero (R1, R3). The project's agent docs
  that state "the advertised `HOSTNAME` must be routable" SHALL state the new rule.

## Non-goals (no-gos)

- **Task failures.** The failure-budget halt policy
  ([`issues/failure-budget-halt-policy.md`](../../issues/failure-budget-halt-policy.md)) is about
  tasks that fail, not clients that do; R3's limit is not it. The cluster's exit status when tasks
  fail is [`issues/run-exit-status-failure-aware.md`](../../issues/run-exit-status-failure-aware.md).
- **Per-zone limits under autoscaling.** A zone whose launches fail while another zone's clients are
  registered keeps relaunching, warned about each time (R2); R3 counts the cluster as a whole.
- **Clients that stay alive but never work**: a half-connected client is
  [`issues/client-partial-connect-hang.md`](../../issues/client-partial-connect-hang.md); eviction and
  `server.evict` are unchanged.
- **Terminating clients.** The autoscaler still never terminates a client; when R1 or R3 fires, the
  launched clients have already exited.
- **The relay beyond R5 and R6.** No `hs relay --advertise` or relay key for the downstream address
  (the relay's CLI, `relay/__init__.py`, is being changed by the relay-hardening cycle, #103); no stop
  or limit for a relay whose site clients all fail; no change to `hs server --autoscaling`. Relay
  changes in this cycle are limited to the site clients' address in `relay/site.py`.
- **`HeartMonitor`** and the rest of `server.py` that #103 touches: this cycle should not need them;
  if the plan finds it does, it says so before building.
- **`LocalCluster`**, whose clients run in-process.
- **IPv6.** A wildcard `::` bind is [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md).
- **Client and submit defaults.** `hs client` takes its default `-H` from `server.bind`
  (`src/hypershell/client.py:1588`) and `hs submit` from `server.host`
  (`src/hypershell/submit.py:1307`); both stay as they are.
- **`TASK_SERVER_HOST`**, which is the server's own host name stamped on each task, not the address
  clients dial; unchanged.
- **No TLS behaviour change**: no certificate generation or `servername` forwarding to clients.
- **No 2.8-line backport.**

## Clarifications

Proposed in shaping on 2026-11-14 by the shaping agent; each is **pending the maintainer's sign-off
via the foreman** before `/hs-plan`.

- **Q:** Two seeds in one cycle, and what appetite? — **A:** One cycle. The advertised-address defect
  is one way every client fails, and R1's and R3's error names the advertised address, so the two
  share their tests and drives. Both seeds say `small`; together they are `big` (header), rounded
  up per the skill's rule rather than split. If the foreman prefers a split, R6–R8 stand alone as
  the pilot's follow-up.
- **Q:** What counts as "every client" under a launcher, where one `mpirun` or `srun` process stands
  for many clients? — **A:** The launcher's exit is the signal: once the launcher process has exited,
  every client it started has. This is the contract `--launcher` already documents ("any program
  that handles process management", as `srun` and `mpirun` do) and the one the autoscaler already
  counts its fleet by (a launched process still running is a client). A launcher that detaches and
  returns while its clients still run (a bare `sbatch`, say) is outside that contract; under R1 such
  a cluster would now exit non-zero once the launcher returns with work left. The error reports the
  launcher's status only; how many clients registered is left to the plan to add if cheap.
- **Q:** How is "every client failed" defined under autoscaling? — **A:** R3: a run of consecutive
  launched clients that exit non-zero while no client of the cluster is registered with the server,
  ended by any client registering or any launched client exiting 0. It is counted across zones and
  whatever the launch was for (work or `--min-size`). Default 3 (with `--init-size` 1 and the
  default 60 s period, about three minutes); `0` turns it off for a service that must survive a
  launcher outage at zero clients. A configuration key only, no new command-line option; the key
  name `autoscale.failed_launches` is the shaper's proposal.
- **Q:** Does it apply to relay site fleets, now that autoscaling also launches through relays? —
  **A:** No (R5). A relay is a long-running delegate: ending it for failing site clients would drop
  its site and its upstream identity, and relay-hardening's fail-fast (R4 there) is about the
  relay's TLS against its upstream, not its site clients, whose key and TLS setting the relay itself
  supplies. The relay already warns at each failed site client. R3's limit lives in code the site
  fleets share (`AutoScaler`, `SiteScaler`), so R5 pins that it stays off for them. A follow-up seed
  can take a relay whose site cannot launch.
- **Q:** Does the advertised-address rule reach the relay? — **A:** The default rule does (R6): a
  relay bound with `--bind` to one address tells its site clients that address, a one-place change
  in `relay/site.py`, which #103 does not touch. The `--advertise`-style override for the relay is
  deferred (Non-goals) to keep clear of #103's `relay/__init__.py`.
- **Q:** Clients that connected and then died with tasks assigned: in scope? — **A:** Yes, by R1's
  wording: once every launched process has exited, the cluster stops without waiting for eviction.
  Their in-flight tasks stay incomplete; with a database, `--restart` resumes them.
- **Q:** `--forever` and `--restart` without autoscaling? — **A:** R1 is a state: whenever every
  launched client has exited and tasks are pending, the cluster exits non-zero, including a task
  submitted after the clients left. With nothing pending it waits as today (R4).
- **Q:** Option name and key for the advertised address? — **A:** `--advertise ADDR` and
  `server.advertise`, unset by default. Not `server.host`: its default `localhost` would be
  advertised, and it is already `hs submit`'s default address.
- **Left for `/hs-plan`** (not clarification markers): how "tasks remain" is read without racing the
  last results of a finishing run (R4), with and without a database; the exit status value (an
  existing runtime-error status is the natural choice); how `--ssh` exits are watched concurrently;
  the exact error wording.

## Evidence plan

What `hs-review` should be able to run against the branch:

- **Unit:** `RemoteCluster` and `SSHCluster` with fake launches (`sh -c 'exit N'`): every one exits →
  stop and error naming each status and the address; a run that completes → unchanged. The R3
  counter: three failed launches stop the autoscaler; a registered client or an exit 0 resets the
  run; `0` never stops; a `SiteScaler` never stops (R5). The `-H` each mode passes `client_launch`
  for a `0.0.0.0` bind, a specific bind, `--advertise`, and `server.advertise`; `zone_launch`'s
  address for a relay bound to one address.
- **`.agents/factory/bin/temp_site.sh` drives:**
  - `hs cluster TASKS --no-db --no-tls --launcher false` exits non-zero within 10 s, with the R1
    error; the same with `--launcher true` (clients that exit 0 with work left).
  - `hs cluster TASKS --no-tls --ssh hostA hostB` with a stub `ssh` first on `PATH` that exits 255:
    non-zero exit, both hosts and statuses named; with one host's stub running the client locally
    and the other failing, the run completes and warns once for the failed host (R2, R4).
  - `hs cluster TASKS --no-tls --autoscaling fixed -P 2s --launcher false` exits non-zero after
    three failed launches; with `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0` it is still relaunching
    after the same time.
  - A healthy run (`--launcher env`, which starts the client on this host) exits 0 with all tasks
    complete, output as on `develop`.
  - A recording launcher (a script that logs its argv, then runs it): `-H 127.0.0.1` in launcher,
    `--ssh` (stub) and autoscaling modes passes clients `-H 127.0.0.1` and completes; `--advertise
    localhost` passes `localhost`; `--advertise` with a local cluster is refused.
- **No scheduler run is required:** every requirement turns on a launcher process's exit and the
  argv it is given, which a fake launcher shows exactly. A confirming run on a real cluster (`srun`
  with a bad `--remote-exe`, expecting a prompt non-zero exit) is the foreman's option, not a gate.
- The test suite under Python 3.11 and 3.14; the Sphinx `-W` build; the launch contract test
  (only options a 2.9.0a4 client accepts are emitted).

## Related materials

- Seeds: [`issues/cluster-waits-when-every-client-fails.md`](../../issues/cluster-waits-when-every-client-fails.md)
  (primary), [`issues/advertised-host-ignores-bind.md`](../../issues/advertised-host-ignores-bind.md).
- Related seeds: [`issues/failure-budget-halt-policy.md`](../../issues/failure-budget-halt-policy.md),
  [`issues/client-partial-connect-hang.md`](../../issues/client-partial-connect-hang.md),
  [`issues/run-exit-status-failure-aware.md`](../../issues/run-exit-status-failure-aware.md),
  [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md).
- Prior cycles: [`spec/zone-autoscale/`](../zone-autoscale/GOAL.md) (#99: `LaunchActuator`, per-zone
  demand), [`spec/relay-topology/`](../relay-topology/GOAL.md) (#100: `SiteScaler`, `hs server
  --autoscaling`), [`spec/relay/`](../relay/GOAL.md) (#82: `--advertise` deferred, its TECH and
  REVIEW), [`spec/key-option-refused/`](../key-option-refused/GOAL.md) (#104: key handling on
  client, server and submit). In flight: relay-hardening (#103: `relay/__init__.py`,
  `relay/upstream.py`, `server.py`'s `HeartMonitor`, `docs/_include/config_param_ref.rst`).
- Invariants: `.agents/factory/invariants.md` §11 (launch hand-off through `client_launch()`, the key
  never on a launched argv, the advertised `HOSTNAME`, the autoscaler never terminates), §9.
- Source: `src/hypershell/cluster/remote.py` (`RemoteCluster`, `AutoScaler`, `AutoScalingCluster`),
  `src/hypershell/cluster/ssh.py` (`SSHCluster`), `src/hypershell/cluster/launch.py`
  (`client_launch`, `LaunchActuator.clean`), `src/hypershell/cluster/demand.py`,
  `src/hypershell/cluster/__init__.py` (`ClusterApp.run_*`), `src/hypershell/relay/site.py`
  (`zone_launch`, `SiteScaler`), `src/hypershell/core/config.py:136-137` (`server.bind`,
  `server.host`), `src/hypershell/core/logging.py:58` (`HOSTNAME`).
