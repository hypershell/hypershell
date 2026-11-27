# 00 — Digest

The research was done inline and in sequence (the harness ran without subagents). It was scoped
to the high-blast-radius launchers and to the cross-version question the GOAL's Clarifications
hand to planning. Briefs: [`01-client-key-intake.md`](01-client-key-intake.md),
[`02-launch-paths.md`](02-launch-paths.md).

Decisions (one each; the briefs do not contradict each other):

1. **Handoff variable:** `HYPERSHELL_SERVER_AUTH`. Every client from 2.8.0 to 2.9.0a4 already
   reads it as its `-k` default, so no client, server or queue change is needed (R4, R5).
2. **Launcher modes and autoscaling:** the key goes in the `Popen` environment those paths already
   build. Open MPI is asked to forward it via `OMPI_MCA_mca_base_env_list`.
3. **SSH:** the key goes on the ssh channel's standard input, read by a POSIX `sh -c` preamble on
   the remote side. `-T` comes after the user's arguments.
4. **One builder:** `client_launch()` in a new `cluster/launch.py`, used by every subprocess
   launch mode (planning requirement 3; it also ports cleanly to 2.8.4).
5. **Backport:** copy the module verbatim and hand-port three call sites (no `zone`/`from_json`
   at 2.8.4).
6. **Appetite:** every mode fits `small`. No mode needs a separate, larger mechanism.
