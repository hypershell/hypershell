# GOAL — Launch-path follow-ups: accurate no-key hints, template docs, an export-name test

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** launch-hint-followups
- **kind:** fix
- **appetite:** small · three LOW follow-ups from the final review of `authkey-handling-hardening`
  (merged as #80), plus one maintainer-approved docs sentence; a few hours of build.

## Problem

The final review of `authkey-handling-hardening` approved the cycle and recorded LOW follow-ups
(`spec/authkey-handling-hardening/REVIEW.md`, *Review cycle 3*: R3-2, R3-3, R3-4). Each is small, and
each leaves a user or a maintainer with something slightly wrong:

- **Misleading error hint (R3-2).** When an `hs client` fails to authenticate, or fails to verify
  the server's TLS certificate, and it was given no `-k` and no `HYPERSHELL_SERVER_AUTH`, its error
  adds a hint: "No key reached this client … the launcher most likely did not pass the environment".
  The hint also appears when the client *did* get a key, from a configuration file
  (`server.auth`, a documented way to give one). That user had a wrong key or a TLS problem, and the
  hint points them at a cause that is not theirs.
- **Inaccurate template docs (R3-3).** `docs/_include/templates.rst` says a `{% %}` command runs with
  "the same environment the client's tasks get". It does not get what the client adds for a task
  (the `[export]` values and the `TASK_*` variables). The same page says that under a client,
  `os.environ` in a `{= =}` lambda includes `HYPERSHELL_SERVER_AUTH`. That holds only for some
  clients, not for every client.
- **Untested check (R3-4).** `ssh_env_exports()` (`src/hypershell/cluster/launch.py`) leaves out an
  exported name that a shell cannot assign. Without that check, the remote shell reads such a word as
  a command and that host's client never starts. No test fails when the check is removed.
- **Unclear security sentence.** `docs/security.rst` has a sentence on the limits of keeping the key
  out of task environments. It is accurate but hard to read. The maintainer approved replacement
  wording.

## Outcome / vision

A client's no-key hints appear only when no key reached it at all, so the hint is right whenever it
appears. The template docs say exactly what environment `{% %}` commands and `{= =}` lambdas see, and
when. Removing the export-name check fails a test. `docs/security.rst` carries the approved wording.

## Acceptance criteria (the contract)

- **R1** — WHEN an `hs client` that got its key only from a configuration file fails to
  authenticate (a wrong key), its error SHALL NOT include the no-key hint.
- **R2** — WHEN an `hs client` that got its key only from a configuration file fails to verify the
  server's TLS certificate, its error SHALL NOT include the no-key TLS hint.
- **R3** — WHEN a client got no key from any source (no `-k`, no `HYPERSHELL_SERVER_AUTH`, no
  configuration file) and fails to authenticate or to verify the server's certificate, its error
  SHALL still include the matching hint. A key from `-k` or from the environment SHALL still
  suppress it (today's behaviour, unchanged).
- **R4** — `docs/_include/templates.rst` SHALL describe accurately the environment a `{% %}` command
  runs with (in particular, not claim it is the task's environment), and SHALL state accurately for
  which clients `os.environ` in a `{= =}` lambda includes `HYPERSHELL_SERVER_AUTH`. Each statement
  SHALL have been checked against the running code, not only by reading.
- **R5** — The test suite SHALL fail when `ssh_env_exports()` stops leaving out a name a shell
  cannot assign. The test SHALL show that such a name is on no ssh command line and that the launch
  still runs.
- **R6** — In `docs/security.rst`, the sentence that begins "Keeping the key out of what tasks
  inherit keeps it out of an environment a task dumps or logs by accident; …" SHALL be replaced by
  exactly the approved text below. The surrounding text SHALL change only as much as grammar needs.

  > This keeps the key out of an environment that a task dumps or logs by accident. It does not hide
  > the key from task code that looks for it: a task runs as the same user as its client, and that
  > user can generally read the client's environment.

## Non-goals (no-gos)

- Anything else in the launch path: key handoff, launcher modes, the SSH step, the Open MPI handling.
- Changing what a task or a `{% %}` command actually receives. R4 is documentation only.
- New hints or a change to the wording of the existing hint messages.
- Any other change to `docs/security.rst` beyond R6.

## Clarifications

- **Q:** Where does the contract come from? — **A:** the foreman's brief for this cycle, taken from
  REVIEW.md cycle 3 (R3-2, R3-3, R3-4) and the maintainer's approval of the security sentence
  (resolved 2026-11-05).
- **Q:** Must the R1/R2 fix use the reviewer's scratch condition (`not auth_given and self.auth ==
  DEFAULT_AUTH`)? — **A:** no; it is a checked candidate, and `/hs-plan` may pick a cleaner signal for
  where the key came from (resolved 2026-11-05).
- **Q:** Must the R4 wording be the reviewer's suggestion? — **A:** no; it is a starting point. The
  claims the docs make are verified by execution, and the wording follows what the code does
  (resolved 2026-11-05).

## Related materials

- Review: [`spec/authkey-handling-hardening/REVIEW.md`](../authkey-handling-hardening/REVIEW.md),
  *Review cycle 3* — R3-2, R3-3, R3-4, and *The unapproved `docs/security.rst` sentence*.
- Merged cycle: #80 (`d2a460f`).
- Source: `src/hypershell/client.py` (`ClientApp.run`, `LAUNCH_ENV_HINT`, `LAUNCH_ENV_TLS_HINT`),
  `src/hypershell/cluster/launch.py` (`ssh_env_exports`), `docs/_include/templates.rst`,
  `docs/security.rst`, `tests/test_cluster_launch.py`.
