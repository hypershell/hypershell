---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The remaining duration options still take bare seconds only

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`size-and-duration-units` made the task and client timeouts, and the inline `timeout:` tag, accept
durations with units (`1h30m`, `1.5h`, `[D-]HH:MM:SS`) through the strict `parse_time` in
`src/hypershell/core/types.py`. The other duration options were left as they were. They are still
declared as `type=int` or `type=float`, so a unit is an argument error:

- `--bundlewait` on `hs submit`, `hs server`, `hs client` and `hs cluster`
  (`src/hypershell/submit.py:1275`, `server.py:1593`, `client.py:1603`, `cluster/__init__.py:151`)
- `--signalwait` on `hs client` and `hs cluster` (`client.py:1619`, `cluster/__init__.py:246`)
- `--delay-start` on `hs client` and `hs cluster` (`client.py:1606`, `cluster/__init__.py:154`),
  declared `type=float`
- the autoscaling `--period` on `hs cluster` (`cluster/__init__.py:257`)
- `--interval` on `hs wait` and `hs run` (`src/hypershell/task.py:348`, `433`)

A user who has just learned that `-W 1h30m` works will expect `--period 5m` to work too.

## Why it was deferred

Kept out of `size-and-duration-units` to keep that change small enough to cherry-pick onto the 2.8
line for 2.8.7 (maintainer ruling, 2026-11-11). This seed is the follow-up its GOAL names.

## Outcome / vision

Every option that takes a duration takes it the same way, with the same forms and the same refusal
message.

## Sketch of the acceptance criteria

- **R1** — WHEN any option listed above is given a duration with units, it SHALL be read as
  `parse_time` reads it.
- **R2** — Every value accepted today SHALL keep its meaning. That includes the fractional seconds
  `--delay-start` takes now, and whatever meaning a negative value carries.
- **R3** — Help text, docs and completions SHALL state the forms each option takes.

## Notes

- Open question for shaping: `--delay-start` is a float, and a negative value means "wait a random
  time, up to that many seconds" (`src/hypershell/client.py:1335-1342`). `parse_time` rounds up to
  whole seconds and refuses a sign, so this option needs its own rule (for example, a leading `-`
  kept as the random form: `-5m`).
- Open question for shaping: whether the matching configuration keys (for example
  `autoscale.period`) reach these options through the same `type=` path, as the timeouts do.
- Related: `spec/size-and-duration-units/` (the parser and its forms).
