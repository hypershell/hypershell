# GOAL — `hs update --delete` refuses changes it would ignore, `hs client` reads its own bundle keys, and a task that never ran is not retried

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** update-client-retry
- **kind:** fix
- **appetite:** big  ·  *Three `small` seeds promoted together on the foreman's brief, plus two
  defects found while verifying them (Clarifications). The `hs update` refusal and the client keys are
  small and local. The retry half is not: making two exit statuses terminal changes the retry
  selection, the group gate and the autoscaler's demand count together (`AGENTS.md` § Retry model:
  "change them together"), on SQLite and PostgreSQL, and the gate's halt message. With the found
  defect (retries drop a task's resource and walltime declarations) and the Python API retry default,
  that is nine criteria and three deliverables, which `small` does not cover. A split is offered in
  Clarifications.*
- **target:** 2.9.0  ·  *all three seeds; each changes behaviour users see.*

## Problem

Three defects, each **pre-existing** on `develop`, each reproduced on `develop` `6a0c28ea` in a
throwaway site (`.agents/factory/bin/temp_site.sh`, SQLite, Python 3.14, local `hs cluster` with
`--no-tls` on a free port). Line numbers are that commit's.

**`hs update --delete` ignores the changes given with it.** `--cancel`, `--revert` and `--delete` are
mutually exclusive in the parser (`src/hypershell/task.py:1046-1049`), but field assignments, tag
changes and `--remove-tag` are not excluded from `--delete`. `TaskUpdateApp.update_tasks` parses them
(`:1092`), counts, prompts, and then deletes (`:1118-1120` without `--limit`, `:1147-1148` with it),
never reading them. Reproduced, each with `-f` and exit 0:

- `hs update group=9 --delete --limit 1` → "Deleted 1 tasks"; no task has group 9.
- `hs update foo:bar --delete --limit 1` and `hs update --delete --limit 1 --remove-tag foo` → one
  task deleted each, the tag change ignored.
- `hs update group=9 --delete -w 'args == 4'` (no `--limit`) → "Deleted 1 tasks".

The prompt counts the rows; it says nothing about the changes that will be dropped. *Found while
verifying, not in the seed:* the legacy single-task form ignores the action options the other way
round. `hs update ID group 7 --delete -f` writes `group = 7` and deletes nothing, exit 0, and
`hs update ID group 8 --cancel -f` writes `group = 8` and cancels nothing: `run` routes three
positional arguments led by an ID to `update_legacy` (`:1065-1068`), which never reads `--cancel`,
`--revert`, `--delete` or `--remove-tag`.

**`hs client` reads the `[submit]` section's bundle keys.** `hs client -b` has no default of its own
(`client.py:1644-1645`); `advertised_size` (`:349-361`) advertises `submit.bundlesize` whenever a
layer above the built-in default sets it, and `-w` defaults to `config.submit.bundlewait`
(`:1647-1648`). Probed: with `HYPERSHELL_CLIENT_BUNDLESIZE=7` and `HYPERSHELL_CLIENT_BUNDLEWAIT=11`,
or `client.bundlesize = 7` in a file, a client advertises nothing and waits 5 seconds; with
`HYPERSHELL_SUBMIT_BUNDLESIZE=9` and `HYPERSHELL_SUBMIT_BUNDLEWAIT=13`, it advertises 9 and waits 13.
The parameter reference contradicts itself: `[client].bundlesize` and `[client].bundlewait` each say
"See also `-b`/`-w`" (`docs/_include/config_param_ref.rst`, `[client]`), while `[submit].bundlesize`
(added by #78, not yet released) and `client_help.rst` say the client reads `submit.bundlesize`. The
2.8 reference documented only `[client]`. Since #78 (zone-dispatch, plan gate ruling N4) the key
matters more: an explicit value is advertised as the bundle size the client requests, so a
`submit.bundlesize` set to tune `hs submit` changes the bundles every plain `hs client` asks for.

**A task that can never run is retried to exhaustion.** A client records two statuses for a task it
did not start (`client.py:717-718`): `-1001` when the command template does not expand, and `-1002`
when the task declares more cores or memory than the client has in total (`insufficient_resources`,
`:684-691`). Both pass the retry filters, which exclude only success and the cancel status
(`data/model.py:776-788`, `__awaiting_retry`, used by `query_retryable` and `select_failed`). With
`hs cluster t.in -r 2 -t 'echo {nope}'`, each task ran three times, three rows each, all `-1001`;
the retry budget went on a failure that could not change. The group gate then holds the group
(`Task.increment_group`, `model.py:684`): without `--forever` a run that has tasks waiting in another
group stops there, and with `--forever` the server waits for the failures to be cleared
(`server.py:290-296`). That gate is by design (`AGENTS.md`); the wasted attempts are not. The client
logs each as "(cancelled)", which it is not: a cancelled task has status `-1`.

*Found while verifying, not in the seed:* **a retry drops the task's declared cores, memory and
walltime.** The retry row copies `args`, `tag`, `group`, `fingerprint`, `source` and `zone`
(`model.py:889-896`), not `cores`, `memory` or `timeout`. Probed: a task submitted with
`-c 100000 -m 1T` failed `-1002` on its first attempt and ran to exit 0 on its retry with no
declaration; a `sleep 6` submitted with `-W 2` was killed at 2 seconds and its retry ran the full 6.
So today a retry is not the same task, and the `-1002` retry that "succeeds" does so by running
without the resources the user asked for.

The seed's third claim, that retry defaults diverge "between standalone and cluster launch paths",
holds for the Python API only. `hs server` and `hs cluster` both retry no task by default
(`server.attempts` is 1, `core/config.py:150`; `server.py:1813`, `cluster/__init__.py:163`), and
`hs cluster` passes its value to every class it builds. But `LocalCluster`, `RemoteCluster`,
`SSHCluster` and `AutoScalingCluster` default `max_retries` to `DEFAULT_ATTEMPTS` (1, so one retry:
`cluster/local.py:200`, `remote.py:261`, `remote.py:963`, `ssh.py:250`), while `ServerThread` and the
`serve_*` functions default to `DEFAULT_ATTEMPTS - 1` (0). A Python caller of `run_local` gets a
retry that `hs cluster` with the same configuration does not; with `in_memory=True` it also logs
"Retries disabled when database disabled" for a retry it never asked for. This is the defect of
`issues/cluster-max-retries-defaults.md` (target 2.9.x).

## Outcome / vision

`hs update` either does every change it is given or refuses before it counts or asks. `hs client`
takes its bundle size and wait from the `[client]` section the reference documents, and `hs submit`'s
keys no longer reach it. A task that never ran because its template does not expand or it needs more
than the client has fails once, is never retried, and still holds its group as any failed task does;
a task that is retried keeps everything it declared. Every Python API entry point retries by the same
default as the commands. The release note names each change and who it affects.

## Acceptance criteria (the contract)

In these criteria a **change** to `hs update` is a field assignment (`FIELD=VALUE`), a tag change
(`KEY:VALUE`) or `--remove-tag`; an **action option** is `--cancel`, `--revert` or `--delete`. A
**terminal status** is `-1001` (the template did not expand) or `-1002` (the task declares more
cores or memory than the client has in total). "Retries" means the server's `-r`/`--max-retries`,
`server.attempts`, and `--eager`/`server.eager` as they combine today.

- **R1 — `--delete` with a change is refused.** WHEN `hs update` is given `--delete` and any change,
  in the filtered form, with or without `--limit`, it SHALL exit 2 with a message naming `--delete`
  and the conflicting arguments, before it opens the database count, prints the count or prompts, and
  SHALL delete and write nothing. `--no-confirm` SHALL NOT change this. `--delete` with filters only
  SHALL behave as today.
- **R2 — The legacy form with an action option is refused.** WHEN `hs update ID FIELD VALUE` is given
  an action option or `--remove-tag`, it SHALL exit 2 with a message naming the option, and SHALL
  write nothing. The legacy form without them SHALL behave as today.
- **R3 — `hs client -b` reads `client.bundlesize`.** WHEN `-b` is not given and `client.bundlesize`
  is set by any layer above the built-in default (a file, `HYPERSHELL_CLIENT_BUNDLESIZE`, or its
  `_env`/`_eval` forms), `hs client` SHALL advertise that value as the bundle size it requests and
  return results in bundles of that size, as an explicit `-b` does. WHEN it is not so set, the client
  SHALL advertise no size, as today with nothing set. `-b` SHALL still override it.
  `submit.bundlesize` SHALL have no effect on `hs client`.
- **R4 — `hs client -w` reads `client.bundlewait`.** WHEN `-w` is not given, `hs client` SHALL take
  its bundle wait from `client.bundlewait`, in the same duration forms. `submit.bundlewait` SHALL have
  no effect on `hs client`. `-w` SHALL still override it.
- **R5 — A terminal status is never retried.** WHEN a task's attempt completes with a terminal status,
  the server SHALL NOT retry it, whatever the retries: no retry row is created, the task is not
  counted as awaiting retry (by the scheduler, a `--restart`, or the autoscaler's demand), and its
  row stays the task's last attempt. Every other non-zero status, signal deaths included, SHALL be
  retried as today. The client's error line for a terminal status SHALL say the task did not run, not
  that it was cancelled.
- **R6 — The group gate holds a terminal failure.** WHILE a group holds a task whose last attempt has
  a terminal status, the server SHALL treat the group as failed exactly as it treats a failure with
  no retries left, on its first attempt and whatever the retries: without `--forever` the run SHALL
  stop at that group when tasks wait in another group, and otherwise finish as today; with
  `--forever` it SHALL wait for the task to be cleared (by `hs update --revert`, `--cancel` or
  `--delete`). The message that reports the failed group SHALL name such a task as not retried
  because it did not run, not as "exceeding allowed retries".
- **R7 — A retry keeps the task's declarations.** WHEN the server creates a retry, the retry SHALL
  carry the failed task's declared cores, memory and walltime (`hs submit -c`, `-m`, `-W`, and their
  `hs cluster` counterparts), as it carries its arguments, tags, group and zone today.
- **R8 — One retry default.** Every Python API entry point that takes `max_retries` (`ServerThread`,
  `serve_from`, `serve_file`, `serve_forever`, `LocalCluster`, `RemoteCluster`, `SSHCluster`,
  `AutoScalingCluster`, and the `run_local`, `run_cluster`, `run_ssh` helpers) SHALL, when not given
  it, allow as many retries as `hs server` and `hs cluster` take from the same configuration
  (`server.attempts - 1`; none with the built-in default). Each SHALL be pinned by a test.
- **R9 — Documentation and release note.** The `hs update` page SHALL state that `--delete` takes no
  change and that the legacy form takes no action option. The parameter reference SHALL say
  `[client].bundlesize`/`.bundlewait` are read by `hs client` (with R3's advertise rule, moved from
  `[submit].bundlesize`), and `[submit]`'s keys by `hs submit` and `hs server`; `client_help.rst`
  SHALL name `client.bundlesize` and `HYPERSHELL_CLIENT_BUNDLESIZE`. The retry documentation
  (`[server].attempts`, `server_desc.rst`, `-r` help) SHALL name the terminal statuses as never
  retried and say a retry keeps the task's declarations. Changed entries carry
  `.. versionchanged:: 2.9.0`. The release note lists every change below and who is affected. Docs,
  help and notes use top-level commands only.

### User-visible changes (the release note)

Each is a change from `develop` `6a0c28ea` and from 2.8.x unless it says otherwise.

1. **`hs update --delete` refuses field and tag changes.** `--delete` given with `FIELD=VALUE`,
   `KEY:VALUE` or `--remove-tag` now exits 2, naming the conflict, before it counts or asks, and
   deletes nothing. Previously the tasks were deleted and the changes silently ignored.
   *Affects:* scripts that pass both; they now fail instead of deleting.
2. **The legacy `hs update ID FIELD VALUE` refuses `--cancel`, `--revert`, `--delete` and
   `--remove-tag`.** It now exits 2. Previously it wrote the field and ignored the option.
   *Affects:* anyone who combined them and believed the task cancelled, reverted or deleted.
3. **`hs client` reads `client.bundlesize` and `client.bundlewait`.** A client without `-b` now
   advertises `client.bundlesize` when it is set (file or `HYPERSHELL_CLIENT_BUNDLESIZE`), and
   without `-w` waits `client.bundlewait`. Previously both keys were ignored and the client read
   `submit.bundlesize` and `submit.bundlewait`. *Affects:* anyone with `client.bundlesize` or
   `client.bundlewait` set (it now takes effect: a client with `client.bundlesize` set requests
   bundles of that size); anyone who set `submit.bundlesize`/`submit.bundlewait` for `hs client`
   (move them to `[client]`); anyone who set them for `hs submit` only (plain clients stop
   advertising that size and stop waiting that long). `hs submit`, `hs server` and `hs cluster` are
   unchanged.
4. **A task whose template does not expand (`-1001`) or that needs more cores or memory than its
   client has (`-1002`) is never retried.** It fails once, and its group is failed at once, as a
   task with no retries left: a run stops there when tasks wait in another group, and
   `--forever` waits for it to be cleared with `hs update`. Previously, with retries, it was retried
   until the attempts ran out and then failed the group; a `-1002` task's retry ran without the
   cores and memory it declared (change 5), so it could "succeed" by oversubscribing its node.
   *Affects:* runs with retries (`-r` 1 or more, or `server.attempts` above 1) that hit either
   status; without retries nothing changes. The autoscaler no longer counts such a task as work
   awaiting retry. A task too large for some nodes belongs in a zone (`hs submit --zone`,
   `hs client --zone`). The client's log line says the task did not run, not "(cancelled)".
5. **A retry keeps its task's cores, memory and walltime.** Previously a retry ran with none of
   them: unconstrained, and with no walltime. *Affects:* runs with retries whose tasks declare
   `-c`, `-m` or `-W`: a retry now waits for its resources and is killed at
   its walltime, as the first attempt was.
6. **The Python API retries by the commands' default.** `LocalCluster`, `RemoteCluster`,
   `SSHCluster`, `AutoScalingCluster` and `run_local`/`run_cluster`/`run_ssh`, when not given
   `max_retries`, now retry `server.attempts - 1` times (none by default); `ServerThread` and the
   `serve_*` functions read the same key. Previously the cluster classes retried each failed task
   once and the server functions never, whatever the configuration. *Affects:* Python callers who
   relied on the cluster classes' one retry (pass `max_retries=1`), and callers with
   `server.attempts` set. `hs server` and `hs cluster` are unchanged.

## Non-goals (no-gos)

- **Routing an over-large task to a fitter node.** A `-1002` task is not requeued to another client,
  and no client reports its capacity to the server. Zones are the routing mechanism (`--zone` on
  `hs submit` and `hs client`, shipped by #78); the seed's "requeue to a fitter node" reading is not
  accepted scope.
- **No other status becomes terminal.** Signal deaths (a walltime kill included) and every positive
  status are retried as today. No new status is added, and the range below `-1000` is not made terminal as a
  rule: `issues/task-start-spawn-guard.md` decides its own spawn-error status, against R5's test
  (the task did not run and would fail the same way on that client).
- **The group gate is unchanged** for every other failure, and so is `--forever`'s wait.
- **The run's exit status.** `hs cluster` and `hs server` still exit 0 when a group fails
  (`issues/run-exit-status-failure-aware.md`).
- **Other `hs update` combinations.** `--cancel` or `--revert` with changes, and the legacy form with
  filters (`-w`, `-t`, `-g`, `--limit` and the aliases, which it also ignores), are unchanged
  (the second is a candidate seed). The prompt's wording is unchanged.
- **`hs cluster`'s `-b` and `-w`** keep their keys (`server.bundlesize`; `submit.bundlewait`, which
  it hands to its clients as `-w`), and `hs relay` still gives its clients `-b`/`-w` explicitly.
- **No fallback** from `client.*` to `submit.*` for `hs client`, and no warning when only `submit.*`
  is set (Clarifications).
- **`hs server`'s and `hs cluster`'s retry default** (none) is unchanged; `server.attempts` under
  `hs cluster` is #117's.
- **The 2.8 line.** 2.9 only.

## Clarifications

- **Q:** One GOAL for the three seeds, under a new slug? — **A:** Yes, `update-client-retry`, each seed
  marked `adopted:update-client-retry` (foreman's brief, 2026-11-22: the last cycle before 2.9.0,
  each seed changes behaviour users see).
- **Q:** Is seed 3 in scope for 2.9.0? — **A:** Yes, as a change to retry defaults and terminal
  statuses (maintainer's ruling, relayed by the foreman's brief, 2026-11-22).
- **Q:** Appetite? — **Proposed:** `big` (header). Each seed says `small`; the retry half changes the
  invariant-governed retry and gate queries together, and the found defects add two criteria.
  **Needs sign-off.**
- **Q:** Split? — **Proposed:** one cycle, phased so the halves land in order: R1–R2, R3–R4, then
  R5–R8, with R9 alongside each. If the gate wants a smaller cycle: (a) R1–R4 here (`small`) and
  R5–R8 as a second 2.9.0 cycle; or (b) keep R1–R6 and R9 here and file R7 and R8 as seeds. Either
  split leaves the other criteria as written. **Needs sign-off.**
- **Q:** Is `-1002` terminal, or only `-1001`? — **Proposed:** both. Both mean the task did not run;
  `-1002` is decided by the dequeuing client's totals (`-C`/`-M` or the node), which `hs cluster`
  gives every client alike. On a mixed fleet a retry could land on a larger node, but that rescue is
  luck, and today it "works" only because the retry drops the declaration (R7). Zones are the
  sanctioned route. The alternative keeps `-1002` retryable, so with R7 it retries to exhaustion on
  same-size nodes. **Needs sign-off.**
- **Q:** Is every `-1001` deterministic? — **Proposed:** treat it so. A template's `{% … %}` shell
  expansion runs a subprocess and can fail for a reason that passes (a file not yet on a node), and
  clients started by hand may run different templates. Both are rare; a template error is a
  configuration error, and the user clears it with `hs update --revert` once fixed. **Needs
  sign-off.**
- **Q:** Take R2 (legacy form with an action option), found while verifying? — **Proposed:** yes. It
  is the seed's defect from the other side (an action silently dropped), in the same command, with
  the same check and exit status. **Needs sign-off.**
- **Q:** Take R7 (a retry drops cores, memory and walltime), found while verifying? — **Proposed:**
  yes. It changes what R5 means for `-1002` and what a retry is under every retry setting; the
  fix is in the retry row's construction R5 also touches. It splits cleanly into a seed if the gate
  prefers. **Needs sign-off.**
- **Q:** R8 is `issues/cluster-max-retries-defaults.md` (target 2.9.x), which the seed's third claim
  restates. Take it here? — **Proposed:** yes, as the "retry defaults" half of the maintainer's
  ruling, with that seed marked `adopted:update-client-retry` by the foreman at the gate (this commit
  leaves it as it is: the brief named three seeds). **Needs sign-off.**
- **Q:** Should R8's server functions (`ServerThread`, `serve_*`) read `server.attempts` too, or keep
  the built-in default? — **Proposed:** read it, so "the same configuration" holds for every entry
  point. Their default with the built-in configuration (no retry) is unchanged. **Needs sign-off.**
- **Q:** A fallback for `submit.bundlesize`/`bundlewait` under `hs client`? — **Proposed:** no; the
  release note (change 3) is the deprecation note. The 2.8 reference documented only `[client]`, the
  `[submit]` sentence is unreleased, and #117 ruled the same way for `task.ratelimit`. The
  alternative is one release of fallback with a warning. **Needs sign-off.**
- **Q:** Exit status for R1 and R2? — **Proposed:** 2, a usage error, as the parser's own
  `--cancel`/`--revert` conflict and `hs update`'s other argument refusals exit today (reproduced).
  **Needs sign-off.**
- **Left for `/hs-plan` (not clarification markers):** where the R1 check sits (the parser cannot
  express "an action option excludes positional assignments"; `process_arguments` runs before the
  count); the refusal and halt messages' wording; how R6's gate sees a terminal status on attempt 1
  (`increment_group` counts only `attempt >= attempts`), on SQLite and PostgreSQL, and in the
  autoscaler's demand (`cluster/demand.py`, `Task.count_retryable_by_zone`); the `AGENTS.md` and
  `invariants.md` retry-model prose to keep in lockstep; how R8's defaults read the configuration.

**Overlap with #117 (`config-set-and-unread`, near merge).** No requirement here changes what #117
changes; its Non-goals defer `client.bundlesize`/`bundlewait` to seed 2. The overlap is textual and
one dependency: #117 edits the `ClientApp` option block (`-R`, a few lines below `-b`/`-w`),
`client_help.rst`, `docs/cli/client.rst`, the `[client]` section of the reference (`.ratelimit`),
`[server].attempts`'s entry, `ClusterApp`'s `-r`/`--eager`/`-R` lines, and the cluster classes'
constructors (`evict_after`); R8 builds on #117's R6 (`hs cluster` reads `server.attempts`).
`/hs-plan` should plan against `develop` with #117 merged and the build rebase onto it.

## Related materials

- Seeds: [`issues/update-delete-ignores-assignments.md`](../../issues/update-delete-ignores-assignments.md),
  [`issues/client-bundlesize-config-key.md`](../../issues/client-bundlesize-config-key.md),
  [`issues/resource-error-terminal-and-retry-defaults.md`](../../issues/resource-error-terminal-and-retry-defaults.md).
- Overlapping seed: [`issues/cluster-max-retries-defaults.md`](../../issues/cluster-max-retries-defaults.md) (R8).
  Consistency: [`issues/task-start-spawn-guard.md`](../../issues/task-start-spawn-guard.md),
  [`issues/run-exit-status-failure-aware.md`](../../issues/run-exit-status-failure-aware.md).
- Prior art: `spec/hs-update-contended-writes/` (#111: `hs update` write paths; its Non-goals record
  the `--delete` observation); `spec/zone-dispatch/TECH.md` (plan gate ruling N4, the advertise rule);
  `spec/config-set-and-unread/` on `fix/config-set-and-unread` (#117).
- Source: `src/hypershell/task.py` (`TaskUpdateApp`), `src/hypershell/client.py` (`advertised_size`,
  `ClientApp`, `TaskExecutor.create_task`, `insufficient_resources`, the status constants),
  `src/hypershell/data/model.py` (`__awaiting_retry`, `query_retryable`, `select_failed`,
  `count_retryable`, `count_retryable_by_zone`, `increment_group`, `starting_group`,
  `__schedule_next_failed_tasks`), `src/hypershell/server.py` (`Scheduler`, `ServerThread`,
  `serve_*`), `src/hypershell/cluster/` (`local.py`, `remote.py`, `ssh.py`, `demand.py`).
- Contract prose: `AGENTS.md` § Retry model and the status table; `.agents/factory/invariants.md`.
- Docs: `docs/_include/task_update_desc.rst`, `task_update_help.rst`, `config_param_ref.rst`,
  `client_help.rst`, `server_desc.rst`, `server_help.rst`, `cluster_help.rst`.
