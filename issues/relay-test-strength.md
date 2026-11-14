---
status: unshaped
kind: test
appetite: small
lane: public
---

# Relay test strength: a judge that can pass vacuously, two surviving mutants, a port race

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`relay`'s review measured its tests by mutation and by editing the evidence its judge accepts. The
code was right in every case below; these are the places a later change could break it without a
test noticing, and one place the suite fails for a reason that is not the relay's. On
`origin/develop` (`b4b37cd`, 2.9.0a6):

- **The R21 judge can pass without proving what it names (C4-4).** `judge_run`
  (`spec/relay/drive/relay_judge.py:67`) still passes every check when run on edited copies of a
  good run's records in four ways:
  - `env_unreadable` = 50 on both leaves: the `e.*` checks (`:180-192`) never read it, so a site
    client whose environment could not be read is not checked for the key.
  - `key_in_server_log` absent: `e.logs` reads it `or 0` (`:191`).
  - A one-group workload: `a.group_order` (`:104`) is then vacuous.
  - A schema-1 run: `:195` skips the site-key checks, and `--require` (`:235`) does not ask for
    schema 2.
  Run B's records (`spec/relay/evidence/cluster/`) show none of the four.
- **Two reconnect behaviours survive mutation (C4-3, M26 and M28).** In
  `src/hypershell/core/reconnect.py`, removing the epoch guard in `UpstreamLink.mark_down` (`:345`:
  a late failure from a connection that is already over must not take the new one down) and making
  a non-transport error on connect retry instead of failing the link (`:421`) both leave the relay
  tests green. The other five survivors of that review (M41, M23, M19, M18, M36) were killed by
  tests in #82; these two were outside the ruling.
- **The relay workload tests lose a port race to their own concurrent copies.**
  `tests/test_relay_workload.py` draws each relay's site port with `free_port()`
  (`tests/conftest.py:43`), which finds a free number and releases it, then passes it on the command
  line (`'--port', str(free_port())`, `:118`, `:242`, `:655`) to a relay that binds it seconds later.
  In the build's 80 parallel runs on Linux, one lost: another copy took the port first, and the relay
  correctly refused it with "Address already in use". A time-of-check-to-time-of-use race in the
  harness, not a relay defect, but it reads as a relay failure.
- **Two smaller gaps the same reviews noted.**
  - The exhausted-failure filters of the group check (`Task.increment_group`,
    `src/hypershell/data/model.py:684` `exit_status != CANCEL_STATUS` and `:686` `retried IS FALSE`)
    can each be dropped with the whole unit suite green. `test_cancelled_task_does_not_hold_group`
    (`tests/test_retry_gate.py:236`) cancels on the first of three attempts, so `attempt >= attempts`
    excludes the row before the cancel filter matters. **Pre-existing**; the filters predate relay.
  - A relay start-up test passes `site_config=''` (`tests/test_relay_launch.py:649`), which is falsy,
    so `RelayThread` falls back to `<configured database file>.site.toml`
    (`src/hypershell/relay/__init__.py:485`): in the test process that is the developer's default
    database path, and `release()` (`:726`) removes that file if it exists. No real relay writes that
    name, so nothing is lost in practice.

## Why it was deferred

Recorded by `relay`'s review cycles 4, 5 and 8 (2026-11-06) and ruled follow-ups for 2.9.0a6; C4-4
and M26/M28 are on #82's known follow-ups. Test-only: none of them is a behaviour the relay gets
wrong today. The port race was seen once, in the build's 80-run evidence, and named as harness
noise by review cycle 8.

## Outcome / vision

Every behaviour of the relay that a mutation can break fails a test, the R21 judge fails a run
whose records cannot show what each check names, and the workload tests can run in parallel copies
without stealing each other's ports.

## Sketch of the acceptance criteria

- **R1** — Under `--require`, the judge SHALL fail a run with `env_unreadable` above 0, with
  `key_in_server_log` absent, with fewer than two task groups, or with a schema below 2.
- **R2** — A test SHALL fail when `mark_down` takes the link down for a connection that is already
  over (M26), and one SHALL fail when a non-transport connect error is retried (M28).
- **R3** — A relay workload test SHALL NOT fail because another process took its site port between
  the draw and the bind.
- **R4** — A test SHALL fail when either the `CANCEL_STATUS` or the `retried` filter is dropped
  from the exhausted-failure check.
- **R5** — No relay test SHALL resolve, write or remove a path under the developer's configured
  database location.

## Open questions for shaping

- **The judge lives in `spec/relay/drive/`.** `spec/{slug}/` is a dated record, and the drive and
  judge were committed there as R21's evidence tooling. Fixing C4-4 means either editing a spec
  record (with the judge's own tests, which `tests/` already carries) or moving the drive to a
  maintained home first. Decide which before the change.
- **The port race: retry or bind 0.** A relay could accept `--port 0` and log the port it bound,
  which the test reads back; or the helper could retry a start that fails with "Address already in
  use". The first removes the race; the second only narrows how often it shows.

## Notes

- Related: `spec/relay-hardening/` (#103: the relay abort races, C4-2 and C5-1, each landed with
  its own tests),
  [`integration-test-hang-timeout.md`](integration-test-hang-timeout.md) (a relay test that hangs
  costs the CI job), [`server-db-test-suite.md`](server-db-test-suite.md) (the group check reached
  PostgreSQL CI only by a dedicated test in #82, `tests/test_zone_postgres.py`),
  `spec/relay/REVIEW.md` (cycle 4: C4-3 and C4-4; cycle 5: notes; cycle 8: notes).
- Found by: `relay` review cycles 4, 5 and 8, 2026-11-06.
