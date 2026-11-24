# REVIEW — A relay starts its clients as the legacy `hyper-shell`

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only**. Every finding cites
> an **executed** command.

- **Reviewed commit:** 35d500f9  ·  **Base:** develop (5897e5ab)  ·  **Date:** 2026-11-23
- **Verdict:** approved
- **Cycle:** 1 of ≤3
- **Contract drift check:** `GOAL.md` changed after shaping only in 22a65191, the GOAL gate with the
  maintainer's and the foreman's rulings.

## Verification run

Fresh reviewer, xhigh effort, on Python 3.14 and 3.11.
- **End to end, 20 scenarios on 3.14 and 10 on 3.11.** Each runs an upstream `hs server` and an
  `hs relay` started from a `bin/` that holds only symlinked `hs` and `hsx`, with no `hyper-shell` on
  PATH. In every one, all tasks completed and the relay drained on SIGUSR1 with exit 0.
- **Targeted test files:**
  - On 3.14: `test_relay_remote_exe`, `test_config_schema`, `test_env_config` and `test_cluster_exit`
    → 328 passed.
  - On 3.14: the relay launch, db, advertise and topology files, cluster launch, completions and
    cluster → 409 passed.
  - On 3.11, with `test_relay_launch` added → 399 passed.
- **Builder's gate:** the whole suite gave 4168 passed on 3.14 and 4165 passed on 3.11.
- **Docs and completions:** the docs build under `-W` passes, and `bash -n` / `zsh -n` pass for the
  completions.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | Site clients launched as the absolute `…/bin/hs` symlink, never `hyper-shell` (3.14, 3.11) | ✅ |
| R2 | `--database` re-exec; relative `./bin/hs`; renamed link; a wrapper; embedded `main(['relay', …])` (the interpreter's `hs`); two zones; topology-driven launches | ✅ |
| R3 | Option, environment variable and file key each taken. Precedence is option > env > file. An empty env value or empty file value means the default. Applies to every zone. `hs config get/which` | ✅ |
| R4 | `DEFAULT_REMOTE_EXE` is `'hs'` in both modules; `SSHCluster`, `RemoteCluster` and `AutoScalingCluster` launch `hs client`; `hs cluster` is unchanged | ✅ |
| R5 | `tests/test_relay_remote_exe.py`: symlinked `bin/hs`, plain and `--database`, plus the precedence cases | ✅ |
| R6 | Help, `.rst`, config reference, completions, `versionadded`/`versionchanged` markers, the `core/config.py` comment | ✅ |

**No leaks:** across every scenario, no carried internal variable, no `HYPERSHELL_RELAY_*` setting
and no upstream key reached a site client's or a task's environment.

Unmapped changes: none. AGENTS.md and invariants.md describe the mechanism accurately. The seed and
ROADMAP changes are the status flip only.

## Findings

None.

## Absurd-input notes (not findings)

- `--remote-exe ''` falls to a bare `hs` from PATH; an empty value is out of scope (Q6).
- A command line or a `~` path in `--remote-exe` is passed as one word, as documented. A command
  line is a non-goal.

## Human-gate triggers

- None.
