# 00 — Digest

The GOAL hands one question to `hs-plan` (Q2: the mechanism that carries the relay's program across
its re-execution), and the change touches `cluster/remote.py` and `cluster/ssh.py` (high blast
radius, one constant each), so the plan ran a scoped research pass: three briefs, done in sequence
by the planner with a scratch prototype, no contradictions between them.

| Question | Decision | Brief |
| --- | --- | --- |
| Where the relay's program comes from | `relay_program(argv0)`: `argv0` absolute when it is an executable file, else the scripts directory's `hs`, else `hs` | [01](01-reexec-carry.md) |
| Which `argv0` | `sys.argv[0]` only when `sys.argv[1:] == ['relay', *RelayApp.argv]` (not under an embedding `main(['relay', …])`) | [01](01-reexec-carry.md) |
| Across `--database`'s `execve` | private `_HYPERSHELL_RELAY_PROGRAM`, set beside `REEXEC_MARKER`, trusted only with it, popped on read | [01](01-reexec-carry.md) |
| Reading `HYPERSHELL_RELAY_REMOTE_EXE` before the fence | import-time `config`; resolved in `RelayApp.run()` before `RelayThread` exists | [02](02-launch-paths.md) |
| Every launch path | one `remote_exe` through `site_cluster` (and its `AutoScalingCluster`) and every `zone_launch`; `SiteScaler` launches what it is given | [02](02-launch-paths.md) |
| The R5 test | symlinked `inst/bin/{hs,hsx}`, `PATH=inst/bin:/usr/bin:/bin`, real `hs server`, no launcher, DEBUG launch line + `hs list --remaining --count` | [03](03-symlinked-hs-test.md) |

Found on the way, out of scope: with no launcher, a site client program that does not exist kills
the `SiteScaler` (CRITICAL `FileNotFoundError`) and the relay exits, rather than warning and relaunching — on `develop`
that is exactly what the `hyper-shell` default does on an `hs`-only `PATH` (02, PLAN §5).
