# Evidence — relay-shutdown-tail

All runs are laptop runs (macOS, loopback) of `spec/relay-hardening/drive/tail_run.py`, used as it is:
4 site clients of 8 threads, tasks of 0.2 s, at 2000 tasks (the R8 size) and 2004 tasks (a partial last
site bundle). Each run writes `RUN_ID.json` and log windows `RUN_ID-server.txt`, `RUN_ID-relay.txt`
(TRACE, from 10 s before the last task; host, user, paths and keys scrubbed by the tool). The *tail* is
the time from the last task's `completion_time` upstream to the upstream server's exit.

## `baseline/` — `develop` `f3fb4dd2`, recorded during shaping (2026-11-21)

Copied verbatim from the shaping scratch. `develop-01` to `-06` are 2000-task runs, `develop-odd-01`
and `-02` 2004-task runs.

| Size | Runs | Tail (s) | What waited |
|------|------|----------|-------------|
| 2000 | 6 | 1.58–1.78 | nothing; direct `hs cluster` tail 1.65–1.76 s |
| 2004 | 2 | 6.75, 7.45 | one site client's last bundle of 4 held 5 s (`Bundle wait exceeded`, 4 lines per relay window); the upstream scheduler then saw the last 4 land 0.65 s into a back-off |

## `plan-probe/` — a plan-time prototype, not the head

`proto-2004-01` ran a scratch copy of the tree with both fixes prototyped (PLAN § 2), put first on
`PYTHONPATH` for every process. Its `commit` (`6f4e809f`) and `src_changed: false` describe the
worktree it was launched from, **not** the code it ran. Tail 1.54 s, direct tail 0.68 s; its relay
window has no `Bundle wait exceeded` line and one idle return. It de-risks R5's 3 s bound and does not
count toward R5's budget or its judge.

## `head/` — the R5 runs (TECH P6)

Written by the R5 phase on the committed head: `head-2004-NN` and `head-2000-NN`, judged by
`../drive/tail_bound.py`, with the table beside the baseline in [`tails.md`](tails.md).

Laptop (macOS 27, loopback), 2026-11-21, commit `dd0b227d` (the P5 commit; `src_changed: false` in
every record), the venv's `hs` on Python 3.14. Six runs in the planned order (2004, 2000, 2004, 2000,
2004, 2000), every one complete; no spare was spent. `tail_bound.py`: PASS.

| Size | Runs | Tail (s) | Baseline tail (s) |
|------|------|----------|-------------------|
| 2000 | 3 | 0.79, 0.72, 0.75 | 1.58–1.78 (6 runs) |
| 2004 | 3 | 1.65, 0.81, 0.69 | 6.75, 7.45 (2 runs) |

What the windows show:

- No `Bundle wait exceeded` line in any relay window (the baseline's 2004-task windows had 4 each).
- Each 2004-task relay window has one `Client idle - returning N tasks` line: the site client whose
  last bundle was partial returned it at once. The 2000-task windows have none (every last site
  bundle was full).
- Each upstream server window has one `Result finished the run - waking scheduler` line: the last
  result ended the scheduler's back-off. Segment s4 (the back-off after the last result) is 0.00 s
  in every head run, against 0.60–2.71 s in the baseline.
- What is left is s7 and s8 (the relay-local server's shutdown signal and the relay's final
  heartbeat, out of scope), 0.61–1.45 s together. The slowest run, `head-2004-01` (1.65 s), is the
  first of the drive and spent 0.95 s in s7.
