---
slug: zone-autoscale
title: Zone-aware autoscaling (zone, cycle 3)
kind: feature
appetite: big
status: in_review
branch: feature/zone-autoscale
base: develop
current_phase: done
last_updated: '2026-11-12'
phases:
- id: P1
  name: Per-zone signal and the demand seam; the autoscaler decides through it (own
    zone only)
  status: done
  satisfies:
  - R1
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_zone_autoscale.py tests/test_zone_model.py tests/test_zone_postgres.py
    tests/test_autoscale.py tests/test_relay_launch.py tests/test_relay_fsm.py &&
    uv run pytest -v tests/test_cluster_launch.py -k autoscaling && .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" founding
- id: P2
  name: 'Per-zone launch path: {zone}, HYPERSHELL_ZONE and --zone through client_launch
    and LaunchActuator'
  status: done
  satisfies:
  - R3
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_cluster_launch.py tests/test_relay_launch.py
    tests/test_relay_fsm.py tests/test_autoscale.py tests/test_zone_autoscale.py tests/test_completions.py
    && .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    founding
- id: P3
  name: Zone discovery and per-zone fleets under the fixed policy, end to end
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R5
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_zone_autoscale.py tests/test_autoscale.py tests/test_zone_model.py
    tests/test_relay_launch.py tests/test_relay_fsm.py tests/test_completions.py tests/test_cluster.py
    && uv run pytest -v tests/test_cluster_launch.py -k autoscaling && .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" fixed && .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" founding
- id: P4
  name: Per-zone pressure under the dynamic policy, laptop evidence, full regression
    and docs gate
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    dynamic && .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    fixed && .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
    founding && uv run pytest && uv run --python 3.12 --group docs sphinx-build -W
    --keep-going -q -E -b html docs docs/_build/html
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Zone-aware autoscaling (zone, cycle 3)

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/zone-autoscale/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (leaf `task.py` apps, docs,
  tests). The coupled core (`data/model.py`, `server.py`, `client.py`, `core/*`) is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in `.agents/factory/bin/temp_site.sh sh -c "…"` so they hit a throwaway site,
  never the developer's real database).
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md) — it is the constitution. Consult
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) for the curated footgun
  checklist relevant to this change (§1, §2, §3, §5, §9, §10, §11, §12 here).
- One phase per `hs-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. Branch commit subjects follow the house style `[feature] Build zone-autoscale
  P<n>: …` (no `WIP:` prefix) — squashed into the single PR-title commit at `hs-publish`.
- **No `Co-Authored-By` trailer** (repo convention; overrides the AGENTS.md default).
- A CLI/feature change updates `docs/_include/*.rst` help snippets and `share/` completions **in the
  same commit**. A user-visible change also updates its prose page, marked `.. versionadded::` /
  `.. versionchanged::` with the release line; its user-facing note goes in the PR's
  squash commit body, which `hs-publish` drafts (`AGENTS.md` § Docs).
- **Founding-case rule (every phase).** The tests named in PLAN §6 "Founding case" stay green with
  their assertions unchanged. The only edit allowed to them is the declared `patch_queries` widening
  in `tests/test_autoscale.py` (P1). Nothing in `src/hypershell/relay/` changes except the
  `discover_zones=False` keyword in `site_cluster` (P2).
- **Generic names.** Docs, tests, shim and drive name no real cluster, account, partition or host.
  Example partitions are `cpu`, `gpu` and `himem`.
- **The drive is laptop-scale.** No cluster, no scheduler: `spec/zone-autoscale/drive/zone_drive.sh`
  in a `temp_site`, bounded by its own poll deadline (macOS has no `timeout`).
- **Q1, resolved 2026-11-12** (GOAL Clarifications; PLAN §2.5). The own default zone keeps today's
  argv, with no `--zone`. Every named zone, the own named `--zone` included, gets `--zone <name>`.
  `{zone}` and `HYPERSHELL_ZONE` are on every launch, `default` included.
- **Plan gate:** approved by the foreman on 2026-11-12, deviation rows included. `hs-build` may start
  P1.

## The demand / actuation seam (built in P1–P3)

| Side | Name | Inputs → outputs |
|------|------|------------------|
| reading | `demand.read_zone_loads(group, knobs, *, own, discover, attempts, eager)` | current group, knobs, own `ZoneRef` or None, discovery switch, the server's retry rule → `Dict[zone_id, ZoneLoad]`. This is the only DB access. The own zone is read in `group`; with `discover`, other zones are found in the group the scheduler runs next (`Task.increment_group(group, attempts, eager)`, P3 amendment). |
| demand | `demand.zone_demand(load, launched, knobs, *, own, initial)` | one zone's `ZoneLoad`, its fleet size, knobs → desired count (≥ `launched`, ≤ `launched + 1` once steady). Pure. |
| demand | `demand.fleet_demand(loads, launched, knobs, *, own, initial)` | all loads, `Mapping[zone_id, int]` fleet sizes, knobs, own `ZoneRef` or None → `Dict[ZoneRef, int]` desired (the own zone always included). Pure. |
| actuation | `launch.LaunchActuator(launch, own, launch_for)` | `.launched() -> Dict[zone_id, int]`; `.apply(desired: Mapping[ZoneRef, int]) -> int` launches the shortfall per zone and never terminates; `.launch_one(zone=None) -> Popen`; `.clean() -> List[Popen]`; `.clients: List[Popen]` (flat); `.describe(zone=None)` logs a zone's launch once; `.launch_of(zone) -> ClientLaunch` (cached). |
| actuation | `launch.client_launch(..., zone=name, expose_zone=True)` | one zone's `ClientLaunch` with `{zone}` in the launcher words, `HYPERSHELL_ZONE` in env, and `--zone` per PLAN §2.5. |
| glue | `remote.AutoScaler` | FSM: `check_*` = `read_zone_loads` → `fleet_demand(launched=actuator.launched())` → `self.pending`; `scale` = `actuator.apply(pending)`. |

Types: `ZoneRef(id, name)` (`ZoneRef.from_name(name)`: None, '' and `default` give the default zone), `ScalingKnobs(policy, factor, init_size, min_size, max_size)`,
`ZoneLoad(zone, remaining, retryable, pressure, group)` with `.work` (`group` is the group read, for logging,
and is left out of equality). `AutoScalerPolicy` moves to
`cluster/demand.py` and is re-exported by `cluster/remote.py`.

## Forward compatibility (`issues/relay-topology.md`)

`relay-topology` plugs into the seam above. It changes none of it.

- **Server side (publish, no launch).** Its autoscaler runs `read_zone_loads(group, knobs, own=None,
  discover=True, …)`, which gives every zone with current-group work and no own-zone init/min. It then
  runs `fleet_demand(loads, launched=<per-zone live fleet, from relay-reported counts>, knobs,
  own=None, initial=False)`, which gives the **per-zone desired totals**. It splits each total across
  live sites by `fill`/`spread` under per-site `min`/`max`/`init`, which is its R1, and hands the
  result to a new `PublishActuator`. That actuator has the same `launched()`/`apply()` surface as
  `LaunchActuator`; its `apply` bumps and stores the versioned topology instead of starting processes.
  Z3's demand takes the fleet size as an injected mapping, so a publishing server, which holds no
  `Popen`s, can supply its own count.
- **Relay side (reconcile).** Each relay builds a `LaunchActuator(site_launch(cluster), own=<site zone>,
  launch_for=lambda ref: zone_launch(cluster, ref.name, …))` over its zones. For each fetched entry it
  calls `apply({ref: desired})`. That launches the shortfall through the same per-zone path and never
  terminates, which is its R5 (scale-down stays idle exit). With no topology (its R6), the relay keeps
  today's fixed fleets: the site cluster with `discover_zones=False`, and `zone_fleet`. Whether relay
  launches set `expose_zone` is that cycle's call. It is one keyword on `zone_launch`.
- What Z3 deliberately leaves out: site identity, per-site knobs, versioning, relay-reported fleet
  counts, and any change under `relay/` beyond the one keyword.

---

## Phase P1 — Per-zone signal and the demand seam; the autoscaler decides through it (own zone only)
**Satisfies:** R1, R4 · **Depends on:** —
**Goal:** the zone-scoped reading and the pure demand exist and are tested. `AutoScaler`'s checks run
through them for its own zone only, with discovery not yet on, and every founding-case test stays
green. Nothing user-visible changes.

- [x] `data/model.py`: add `zone: str = None` (through `_filter_zone`) to `count_remaining`,
  `effective_rate_by_client`, `effective_rate`, `avg_duration`, `time_to_completion` and
  `task_pressure`. Add `count_remaining_by_zone(group)` and `count_retryable_by_zone(attempts, group,
  eager=False)` (PLAN §2.1). NULL maps to `DEFAULT_ZONE_ID`; no `min`/`max` over the uuid column;
  `CANCEL_STATUS` comes from `query_retryable`.
- [x] New `cluster/demand.py`: `AutoScalerPolicy` (moved; re-export from `cluster/remote.py` and keep
  it in `__all__`), `ZoneRef`, `ScalingKnobs`, `ZoneLoad`, `read_zone_loads`, `zone_demand` and
  `fleet_demand`, exactly as in PLAN §2.2. Discovery is implemented and unit-tested here but not yet
  switched on by any caller.
- [x] `cluster/remote.py`: `check_fixed()`/`check_dynamic()` read `read_zone_loads(…, own=<own zone>,
  discover=False, …)` and decide with `fleet_demand`. `init()` uses `fleet_demand(initial=True)`. Both
  stay zero-argument and return `AutoScalerState`. The own zone comes from a new `own_zone` keyword
  (None means default); `AutoScalingCluster` passes its `zone`. The per-zone log line replaces the old
  one. Keep the DEBUG "Autoscale launcher" record (`test_autoscaling_mode_logs_never_hold_key`).
- [x] `tests/test_autoscale.py`: the declared `patch_queries` widening only, as PLAN §4 describes.
  The 11 decision assertions are unchanged.
- [x] New `tests/test_zone_autoscale.py`:
  - unit: the `zone_demand` table;
  - unit: a parametrized equivalence of `zone_demand(own=True)` to today's `check_fixed`/`check_dynamic`
    table over (policy, launched, work, pressure, min, max);
  - unit: other zones get no init/min;
  - integration: `read_zone_loads` on a real DB, with discovery, the retry edge, a gated zone absent,
    an unnamed zone id skipped with a warning, and the default zone keyed `DEFAULT_ZONE_ID`.
- [x] `tests/test_zone_model.py` (+ `test_zone_postgres.py`): the zone-filtered helpers, including
  filtered == unfiltered in a zone-less DB, `count_remaining_by_zone` and `count_retryable_by_zone`
  (cancelled excluded; the `attempts <= 1 and not eager` rule).
- [x] New `spec/zone-autoscale/drive/zone_shim.sh` and `zone_drive.sh` (from
  [03](research/03-launcher-docs-verify.md), modes `founding|fixed|dynamic`). Founding mode asserts:
  - every launch is in the default zone with no `--zone`;
  - all tasks complete and none failed;
  - every client exits by itself.

  The `{zone}`/env checks are switched on in P2.
- **Verify:** see frontmatter (model, demand, autoscaler, relay and autoscaling-launch tests, plus the
  founding drive).
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/cluster/demand.py`,
  `src/hypershell/cluster/remote.py`, `tests/test_zone_model.py`, `tests/test_zone_postgres.py`,
  `tests/test_autoscale.py`, `tests/test_zone_autoscale.py`, `spec/zone-autoscale/drive/`.

## Phase P2 — Per-zone launch path: `{zone}`, `HYPERSHELL_ZONE` and `--zone`
**Satisfies:** R3 · **Depends on:** P1
**Goal:** every launch the autoscaler makes for a zone exposes that zone to the launcher. The relay's
launches are byte-identical to today. A founding-case run with the shim records `default` in both
`{zone}` and `HYPERSHELL_ZONE`, with the argv unchanged.

- [x] `cluster/launch.py`: `client_launch(..., expose_zone=False)` per PLAN §2.3. It replaces `{zone}`
  per launcher word and launcher argument after `shlex.split`, adds `HYPERSHELL_ZONE` to `env`, does
  no Open MPI forwarding of it, never touches `-t`, and refuses with SSH.
- [x] `cluster/launch.py`: `LaunchActuator` per PLAN §2.3: a cached launch per zone, `describe()`
  logged once per zone, a flat assignable `clients`, untracked entries counted as own, and `clean()`
  moved here from `AutoScaler`.
- [x] `cluster/remote.py`:
  - `AutoScaler` holds a `LaunchActuator`; `clients`/`launch` become properties onto it, with a
    `clients` setter;
  - `scale()` = `apply(pending)`, or one own-zone launch when nothing is pending;
  - new keywords `launch_for`, `attempts`, `eager` and `registered`, passed through by
    `AutoScalerThread`;
  - `AutoScalingCluster(discover_zones=True)` builds `launch_for` with `expose_zone=True` and sets
    `self.launch = launch_for(own)`. Off, it builds today's launch verbatim.
- [x] `relay/site.py::site_cluster`: pass `discover_zones=False`. That is the only `relay/` edit.
- [x] Apply PLAN §2.5 for `--zone` (Q1, as resolved 2026-11-12).
- [x] Tests in `tests/test_cluster_launch.py`:
  - the `expose_zone` cases from PLAN §6;
  - the 2.9.0a4 contract test parametrized with `expose_zone`;
  - `test_autoscaling_mode_keeps_every_client_option` unchanged, with a sibling asserting
    `HYPERSHELL_ZONE` in `cluster.launch.env`;
  - a test that the client's zone never comes from `config.zone` (`HYPERSHELL_ZONE` set, no
    `--zone` → default zone).
- [x] Tests in `tests/test_zone_autoscale.py`: `LaunchActuator` accounting, caching and `apply`
  shortfall, using a fake `launch_for` that records launches.
- [x] Docs, same commit:
  - `docs/_include/cluster_help.rst` and `APP_HELP`: `--launcher` gets `{zone}`/`HYPERSHELL_ZONE`,
    and `--autoscaling` gets the generic launcher-shim example;
  - `docs/_include/config_param_ref.rst` (`[autoscale] .launcher`);
  - the `--launcher` description in `share/zsh/site-functions/_hs`;
  - `docs/cli/cluster.rst`: `.. versionchanged:: 2.9.0`;
  - `AutoScalingCluster` docstring;
  - one line in AGENTS.md (cluster section) and invariants.md §11, in lockstep.
- [x] Drive: founding mode now also asserts `word == env == default` on every launch.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/cluster/launch.py`, `src/hypershell/cluster/remote.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/relay/site.py`, `tests/test_cluster_launch.py`,
  `tests/test_zone_autoscale.py`, `docs/_include/cluster_help.rst`, `docs/_include/config_param_ref.rst`,
  `docs/cli/cluster.rst`, `share/zsh/site-functions/_hs`, `AGENTS.md`, `.agents/factory/invariants.md`,
  `spec/zone-autoscale/drive/`.

## Phase P3 — Zone discovery and per-zone fleets under the fixed policy, end to end
**Satisfies:** R1, R2, R4, R5 · **Depends on:** P2
**Goal:** with discovery on, one `hs cluster --autoscaling=fixed` serves default, `cpu` (group 0) and
`gpu` (group 1). Each zone gets its own clients through the shim with the right zone, `gpu` is never
launched while group 0 has work, and every client retires by idle exit.

- [x] `cluster/remote.py`:
  - `check_fixed()`/`check_dynamic()` call `read_zone_loads(discover=<launch_for given>, attempts,
    eager)`, decide with `fleet_demand(launched=actuator.launched())`, set `self.pending` (one launch
    per zone per check at most), and log per zone with the registered count from `registered` (falling
    back to `Client.count_connected()` when absent);
  - names come from `ZoneRef`;
  - `AutoScalingCluster` passes `attempts`/`eager` (the server's), `registered` (the dispatcher's
    `clients`, lazily) and `own_zone`.
- [x] Tests in `tests/test_zone_autoscale.py` (unit, fake actuator):
  - three zones, with the own zone's init/min only;
  - a gated zone never launched;
  - the retry edge launches a named zone;
  - max-size per zone under dynamic;
  - `pending` capped at one per zone per check;
  - the autoscaler never ends by itself, and no termination.
  The relay tests are re-run unchanged.
- [x] Docs, same commit:
  - `docs/_include/cluster_help.rst` and `APP_HELP`: `--zone` under autoscaling is the own zone, and
    `--autoscaling` covers per-zone scaling, uniform knobs and pacing per zone;
  - `docs/_include/cluster_desc.rst`: one sentence;
  - the `--zone` description in `share/zsh/site-functions/_hs`;
  - `docs/cli/cluster.rst`: extend the `versionchanged`.
- [x] Drive `fixed` mode passes all of PLAN §6's three-zone assertions.
- **Amendment (P3 build): discovery reads the group the scheduler runs next.** The current group
  is read as `Task.current_group()`, the group of the most recently scheduled task, and the
  scheduler moves on only when a client asks for work. A zone of the next group has no client, so
  its tasks were never scheduled and the drive's `gpu` zone was never discovered (first `fixed`
  run: tasks remained after the deadline). So, with discovery on, `read_zone_loads` finds other
  zones in `Task.increment_group(group, attempts, eager)`: `group` while it holds incomplete tasks
  or failures awaiting a retry, else the lowest group with incomplete tasks. That is the
  scheduler's own rule, read only, so R2 still holds (no zone of a later group is read while the
  current one has work). The own zone is still read in `group`, exactly as before, so the
  zone-less case is unchanged. `ZoneLoad` gains `group` (logging only, out of equality). Known
  edge, left as it is today: an own zone whose only work is in the next group, with no client of
  any zone left to ask, waits as the founding case does. Tests: two unit tests and one
  integration test in `tests/test_zone_autoscale.py`.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/cluster/remote.py`, `src/hypershell/cluster/__init__.py`,
  `tests/test_zone_autoscale.py`, `docs/_include/cluster_help.rst`, `docs/_include/cluster_desc.rst`,
  `docs/cli/cluster.rst`, `share/zsh/site-functions/_hs`, `spec/zone-autoscale/drive/`.

## Phase P4 — Per-zone pressure under the dynamic policy, laptop evidence, full regression
**Satisfies:** R1, R2, R3, R4, R5 · **Depends on:** P3
**Goal:** the dynamic policy scales each zone on its own pressure in a live run. The three drive
modes' logs are kept as evidence, the full suite is green, and the docs build passes with `-W`.

- [x] Drive `dynamic` mode, with a task duration long enough (a `sleep`) for pressure to be known.
  It asserts every PLAN §6 three-zone check, plus that at least one zone grew past one client on its
  own pressure while another stayed at one.
- [x] When `ZD_EVIDENCE=<dir>` is set, `zone_drive.sh` copies each mode's launch log, task log and
  per-zone cluster log lines to `<dir>/{founding,fixed,dynamic}.log`; unset (every `verify:`), it
  writes nothing in the repo. Run the three modes once with
  `ZD_EVIDENCE="$PWD/spec/zone-autoscale/evidence/laptop"` and commit the logs. Write a short
  `spec/zone-autoscale/evidence/laptop/README.md`: what was run, on what kind of host, the commit,
  and the pass lines.
- [x] Fix anything the full suite or the `-W` docs build finds. Any change to source re-runs P1–P3's
  verifies before this phase is marked done.
- **Verify:** see frontmatter (three drive modes, full `uv run pytest`, the docs build); the evidence
  logs are part of the phase's commit.
- **Build notes (P4).**
  - Drive: zones are now `NAME:GROUP:TASKS:SECONDS`, and the cluster gets `-Y MAX` (1 for
    founding and fixed, the default; 3 for dynamic). Dynamic gives default and `gpu` twelve
    one-second tasks and `cpu` three quick ones, so two zones grow on their own pressure while
    one stays at one; it also checks that no zone passes the max-size. With the default max-size
    of 1 no zone could grow, which the first dynamic run showed. `ZD_EVIDENCE` writes `MODE.txt`
    with the host name, repository path and site path replaced (`.txt`, since `*.log` is
    ignored repo-wide; the checklist's `{founding,fixed,dynamic}.log` names became `.txt`).
  - No source change in this phase, so P1–P3's verifies were not re-run beyond the three drive
    modes and the full suite.
  - Full suite: 1718 passed, 88 skipped (PostgreSQL-only without `HS_TEST_POSTGRES`:
    `test_contended_write` 8, `test_timescale` 4, `test_upgrade_postgres` 29,
    `test_writeback_postgres` 24, `test_zone_postgres` 5; `test_timescale` 16 without
    `HS_TEST_TIMESCALE`; one fish-completion case without fish; one fixture regeneration).
  - Docs gate, sandbox form: the `sphinx_sitemap` extension cannot bind a local socket in the
    build sandbox, so the gate ran as `uv run --python 3.12 --group docs sphinx-build -W
    --keep-going -q -E -b html -D extensions=sphinx.ext.autodoc,sphinx.ext.napoleon,sphinx.ext.intersphinx,sphinx.ext.extlinks,sphinx.ext.viewcode,sphinxext.opengraph,sphinx_inline_tabs,sphinx_copybutton,sphinxcontrib.details.directive,sphinx_reredirects
    docs docs/_build/html` (every extension but `sphinx_sitemap`): exit 0, no warnings. The
    unmodified frontmatter command is to be run outside the sandbox before review.
- **Touches:** `spec/zone-autoscale/drive/`, `spec/zone-autoscale/evidence/laptop/`, and, only for
  fixes, any file P1–P3 touched.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
