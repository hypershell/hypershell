---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A relay run can still end up to about 5 s late: the last result waits in a site client

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

After `relay-hardening` (R8, outcome (b)), the end-of-run tail of a relay run, from the last task's
`completion_time` to the upstream server's exit, fell from 6.6 to 13.4 s to 1.7 s in the best case
but still reaches about 7 s in three of five runs on the fixed head. Two causes remain, both named
from logs (`spec/relay-hardening/evidence/laptop/`, runs `laptop-01` to `laptop-10`; table in
`spec/relay-hardening/evidence/laptop/README.md`, decision in `spec/relay-hardening/TECH.md` P7):

- **(a) The site client's own result bundle wait.** The relay launches its site clients with
  `relay.bundlewait` (5 s) as their result bundle wait, and a bundle of
  `max(relay.bundlesize, relay.num_threads)` results (8 in the runs). The last results of a run
  can sit in a site client until that wait expires before the relay-local server has them: segment
  s1 is 5.0 s in `laptop-02` (diagnostic) and in `laptop-08`, `laptop-09` and `laptop-10` (post-fix),
  and nothing else is over 5 s in those runs. Options: a site client flushes its bundle when it
  holds nothing left to run; or the relay gives its site clients a shorter bundle wait than the one
  it uses for its own return upstream.
- **(b) A landed result does not wake the upstream scheduler.** The scheduler looks again after a
  back-off that doubles toward `server.poll` (30 s) and is woken only by a cut or new demand, never
  by a result landing. In `laptop-04` it looked 4 ms before the last 8 results landed, saw them
  incomplete, and backed off 4 s twice: s4 was 6.03 s against a 2 s bound (tail 12.5 s). The five
  post-fix runs did not hit it (s4 0.61 to 0.84 s), so it is rare, but the back-off can grow far
  longer, as one 60 s run seen in review may have.

## Why it was deferred

`relay-hardening` R8 bounded the investigation (at most 10 laptop runs; one cluster job only if no
run exceeded 5 s) and allowed a fix only where it fit the appetite. It landed the two fixes that
were within it (the relay's returner sends at once when the site holds no unfinished task; the
upstream heart monitor's shutdown signal no longer waits out its 2 s read) and recorded outcome (b)
for these two. No cluster job ran: plan-gate ruling 3 rules it out when a segment exceeds its design
bound or post-fix runs exceed 5 s, and both held. (a) touches how site clients are configured and
launched; (b) touches the scheduler's wake-up for every server.

## Outcome / vision

A relay run ends within about 2 s of its last result, and a plain server's scheduler notices a
completion that makes the run finishable without waiting out a back-off.

## Sketch of the acceptance criteria

- **R1** — WHEN a site client holds no task to run and has results not yet returned, it SHALL return
  them without waiting out its result bundle wait (or the relay SHALL start site clients with a wait
  no longer than a small fixed bound), with a test that fails before.
- **R2** — WHEN a result lands on a server whose scheduler is backing off with every remaining task
  now complete, the scheduler SHALL look again promptly, not after the back-off.
- **R3** — Post-fix runs of `spec/relay-hardening/drive/tail_run.py` (the R8 size) SHALL show no
  tail over 5 s, on the laptop and, if run, once on Linux.
- **R4** — A server's dispatch, retry and eviction otherwise behave as today.

## Notes

- Evidence: `spec/relay-hardening/evidence/laptop/README.md` (the nine segments s1 to s9 and their
  bounds in `spec/relay-hardening/drive/tail_judge.py`), `laptop-02.json`, `laptop-04.json`,
  `laptop-08.json` to `laptop-10.json` and their log windows. `GOAL.md` R8 of `relay-hardening`
  defines the tail.
- `tail_run.py` and `tail_judge.py` are the measuring tools; the cluster script `tail_cluster.sh`
  was rehearsed on the laptop only and has never run on a cluster.
- Related: [`relay-hardening.md`](relay-hardening.md) (the slow finish this narrows),
  [`hypertable-contended-scheduler-and-cli-writes.md`](hypertable-contended-scheduler-and-cli-writes.md)
  (also changes the scheduler).
- Found by: `relay-hardening` R8, 2026-11-14.
