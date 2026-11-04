# 03 — Deterministic tests, hang-safe verification, and the 2.8.4 port trial

> Read-only research brief for `hs-plan`, written by the planner. Questions: how do R8's tests
> replay the interleavings with no timing, how do verify commands stay safe against hangs, and
> how is R9's port proved before release?

## Unit replays (R8 a, b)

`tests/test_retry_gate.py` (#68) already holds the pattern, and it exists on both `develop` and
2.8.4 after #68.

- Bind `Session` to a per-test SQLite file and rebind it on teardown.
- Drive `Scheduler(queue=None, poll=0)` through `start()` and `load_bundle()`. With `poll=0` the
  back-off is `sleep(0)`.
- Commit "other process" writes through `Session.session_factory()`.

The new file copies that fixture and its two write helpers rather than importing them from
another test module, so it stays self-contained when ported.

- **(a) Race.** Injection must land between `increment_group`'s incomplete count and its
  next-group choice. `count_retryable` runs in that gap (#68 order). It also runs in the stop
  branch, so the replay arms the injection only while `increment_group` runs: a wrapper on
  `increment_group` sets a flag, and a one-shot wrapper on `count_retryable` fires only under it.
  To isolate the race fix from the provisional empty-database start (01), the replay starts from a
  started group. A pending row in a higher group keeps `count_remaining() > 0`, so the stop branch
  does not end the run first.
- **(b) Late lower group.** Group 1 starts, a group-0 row is committed, and group 1 completes.
  The test is parametrized over `forever_mode`. In non-`forever` mode it asserts group 0 runs, a
  WARNING names 0 and 1, and the run reaches FINAL. In `forever` mode it asserts group 0 runs.
- **Further replays.** Both are red on `b31c66e` in the scratch prototype.
  - Empty-database start with `-1, -1, 0` committed before the first LOAD: order `-1, -1, 0` and
    no warning (R4, R5).
  - `--restart` on a database the defect hung: L runs, FINAL, no warning (R6).
- **Guard, green before and after.** `--restart` on a halted database (group 0 exhausted, group 1
  unscheduled) still stops with `Failed task group 0`, and group 1 is never scheduled (R7).
- **Bounded loops.** Every drive loop has a fixed pass limit. A replay that never reaches FINAL
  fails by assertion rather than hanging, so the red runs on `b31c66e` are safe and strict
  `xfail(raises=AssertionError)` proves red by assertion, as #68's P1 did.

## Integration test (R8 c)

`hs cluster FILE -N2`, with a file that lists a higher group before a lower one (layouts
`1, 0` and `0, -1`). After the run, `hs list --json` (on both branches) gives `group`,
`schedule_time` and `completion_time`. The test asserts:

- every lower group's last `completion_time` is no later than the next group's first
  `schedule_time`;
- the `Completed task group … - starting task group …` lines are exactly the ascending
  transitions;
- no late-lower-group warning appears.

With ingest first, the database holds the whole file before the first choice, so the test does
not depend on timing to pass. On `b31c66e` it hangs about half the time, which is why it is never
run red, and why every run carries a kill timer.

## Hang safety and the known flake

- The integration suite has no per-test timeout (seeded separately), and `timeout(1)` is not
  installed. Every `verify:` that runs integration tests or `hs`/`hsx` uses a shell function
  that runs the command in its own session and kills that process group after N seconds (exit
  124). It is a one-line Python `subprocess.Popen(start_new_session=True)` plus `threading.Timer`
  around `os.killpg`, and it was checked under both `sh` and `zsh`.
- **TLS localhost flake** (own fix cycle): a client dialing `localhost` can reach a foreign IPv6
  listener and exit rc 5 with `ConnectionResetError`. Suite verifies log to a file and, on
  failure, print the failed node ids, then rerun **only** those once (`pytest --lf`). The build
  records both runs in the commit body. A second failure is red, and a first failure that is not
  this signature is a finding even if the rerun passes. `-l` puts the captured `stderr` in the
  traceback so the signature can be read.
- **Pre-existing early-stop with streams** (01): CLI drives use files or `hs submit` +
  `--restart`, never a pipe, so they cannot trip it.

## The 2.8.4 port trial (R9)

- **#68 does not cherry-pick cleanly onto `origin/master`.** `git merge-tree` shows one
  `src/` conflict, in `Scheduler.load_bundle`'s stop branch. 2.8.3 has
  `... and not self.startup_phase`, and #68 adds `Task.count_retryable(...) == 0` to `develop`'s
  form. The resolution keeps 2.8.3's `not self.startup_phase` and adds #68's conjunct and its
  two-line comment. `model.py` and `tests/test_retry_gate.py` merge cleanly.
  `AGENTS.md`/`ROADMAP.md` conflict too, but the port is path-limited to `src/` and `tests/`.
- **Mechanics.**
  1. Create a detached worktree of `origin/master` under the main checkout's `.worktrees/`. The
     main checkout is resolved from `git rev-parse --git-common-dir`, so no host path is
     written into the repo.
  2. Apply #68 with `git diff b819de6^ b819de6 -- src tests | git apply -3`, write the scripted
     resolution of that one hunk, check that no conflict markers remain, and `git add` the result.
  3. Apply this cycle with `git diff b31c66e HEAD -- src tests | git apply -3`, which must be
     conflict-free.
  4. Run `uv sync --frozen` in the trial worktree, against 2.8.3's lock.
  5. Run the new tests, `tests/test_retry_gate.py` and `tests/test_groups.py` under the kill
     timer.
  6. `git worktree remove --force` and `git worktree prune`.

  `master` must be unchanged: compare `git rev-parse master` before and after.
- **Keeping step 3 conflict-free** is a build constraint. `develop`'s stop branch differs from
  2.8.3's, so edits in `load_bundle` must not touch the stop-branch lines or the lines next to
  them. Git treats an adjacent change as a conflict. The R5 bookkeeping goes in a helper called
  at the top of `if self.tasks:`. The provisional start changes only the `Task.next` call above
  that block.
