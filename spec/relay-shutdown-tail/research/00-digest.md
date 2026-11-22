# 00 — Digest

Three briefs, fanned out under the high-blast-radius exception (`server.py` and `client.py`):
[`01-client-bundling.md`](01-client-bundling.md) (R1, R2, R6),
[`02-server-finish-wake.md`](02-server-finish-wake.md) (R3, R4 and the cut),
[`03-relay-tests-and-drive.md`](03-relay-tests-and-drive.md) (R7, R5). One recommendation each:

1. **R1/R2 — an in-flight count in the client.** A lock-guarded `TaskCount` made in `ClientThread`, raised
   by the client scheduler when a reply brings tasks and lowered by the collector as it takes each
   finished task. The collector returns a non-empty partial bundle when the count is 0; otherwise it
   bundles exactly as today. Queue emptiness, executor state and resource accounting were rejected
   (01 § 2). Every client gets it: `hs client`, `hs cluster` local, launched and relay site clients. The
   relay's own returner already returns at once when its site is idle, so no relay change is needed.
2. **R3/R4 — a counted arm, not a per-bundle read.** The scheduler arms when a pass loads nothing with
   the input ended (not `--forever`), recording the results landed so far and the remaining count it
   reads anyway; the receiver checks the counts only once enough results have landed to finish the run,
   and wakes the scheduler (the #110 `wake`) only when nothing is incomplete or retryable. **The GOAL's
   cut does not apply**: no read per landed bundle, no back-off change while work remains (02's table).
3. **R7 — one reader for the scans.** `read_process()` returns a process's command line and environment
   or `None` on `AccessDenied`, `NoSuchProcess`, `ZombieProcess`; `scan_site`, `scan_site_key` and a new
   `client_environments()` (the `:1058` read, now inside `until`) use it. Counts and assertions unchanged.
4. **R5 — `tail_run.py` read-only, a new judge.** Runs on the committed head into `evidence/head/`,
   judged by `drive/tail_bound.py` (tail only, 3 s, at least 3 complete runs at each size, at most 10
   records, one commit). `tail_judge.py` is not reused: it flags every 2004 run and cannot count sizes.
5. **Bounding.** The host has no `timeout(1)`: `drive/bounded.py` runs a command in its own process group
   and stops it (SIGINT, SIGTERM, SIGKILL) past its bound, exit 124. Every verify goes through it or
   through a test that bounds itself.

Contradictions resolved: 03 proposed a perl `bounded()` zsh function; the plan uses `drive/bounded.py`
(same signals, portable to the `sh` that `hs-build` runs verifies in). 01 left the man pages open; the
plan edits their sources and checks the man builder's output, leaving the committed pages to the
release rebuild (PLAN § 5, a sign-off item).
