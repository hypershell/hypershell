---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A memory size with a short unit is silently read as bytes

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`parse_bytes` (`src/hypershell/core/types.py:95-100`) matches its pattern against the **start** of
the value and ignores whatever follows:

```python
MEMORY_PATTERN: re.Pattern = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]B)?')

def parse_bytes(value: str) -> int:
    if match := MEMORY_PATTERN.match(value.upper()):
        return int(float(match.group('num')) * MEMORY_SCALES[match.group('unit') or 'B'])
```

The unit group is optional and the pattern is not anchored at the end. So a unit the pattern does
not know is dropped, the number is kept, and the value is read as that many bytes. Run against
2.9.0a6 (the code is the same on `develop`):

| Given | Read as |
|---|---|
| `8GB` | 8 589 934 592 (correct) |
| `8G`, `8g` | 8 |
| `512M` | 512 |
| `8GiB` | 8 |
| `1.5GiB` | 1 |
| `8,000MB` | 8 |
| `8GB of ram` | 8 589 934 592 (the trailing text is ignored) |

There is no error and no warning. `8G` and `512M` are what a user of Slurm types by habit.

Every place a memory size is read goes through this function:

- `hs submit -m/--memory` (`src/hypershell/submit.py:1283`)
- `hs cluster -m/--memory` and `-M/--client-memory` (`src/hypershell/cluster/__init__.py:227`, `232`)
- `hs server -m/--task-memory` (`src/hypershell/server.py:1599`)
- `hs client -M/--client-memory` (`src/hypershell/client.py:1637`)
- the inline `memory:` tag (`src/hypershell/data/model.py:529`)

What follows, read from the source and not reproduced on a cluster:

- **A task declared with `8G` asks for 8 bytes.** It fits beside anything, so declared memory no
  longer limits how many such tasks a client runs at once, and the client then warns that the task
  exceeded its allocation.
- **A client started with `-M 64G` offers 64 bytes.** A task that declares a real amount of memory
  is judged too large for it.

The same function is the size-or-time test for the log rotation policy
(`src/hypershell/core/logging.py:1055`, and `463`). A cron expression that begins with a digit is
therefore read as a size: `30 2 * * *` becomes "rotate every 30 bytes". That was recorded as out of
scope in `spec/croniter-missing-guidance/research/00-digest.md` and never seeded. It has the same
cause and the same fix.

`parse_time`, next to it in the same file, anchors the same way: `1h30m` is read as one hour and
`5MB` as five minutes. Nothing in `src/` calls it today.

## Why it was deferred

Found from outside a cycle, while building a separate tool against the library, so there was no
change set to fix it in. **Pre-existing** in `develop` and in the 2.8 line.

## Outcome / vision

A memory size means what it appears to mean, or the command refuses it and says which forms it
takes. Nobody's `8G` is quietly 8 bytes. A cron expression is never mistaken for a size.

## Sketch of the acceptance criteria

- **R1** — WHEN a memory size is given in a form `parse_bytes` does not recognise in full, the
  command SHALL refuse it with a message that names the value and the forms accepted.
- **R2** — WHEN a memory size is written with a short unit (`8G`, `512M`) or a binary one (`8GiB`),
  it SHALL either be read with that unit or be refused; it SHALL never be read as a bare count of
  bytes. Which of the two is decided at shaping.
- **R3** — Every form documented and accepted today (`8GB`, `512MB`, `8 GB`, `1.5GB`, lower case,
  a bare integer as bytes) SHALL be read as the same value as before.
- **R4** — WHEN the log rotation policy is a cron expression that begins with a digit, logging
  SHALL treat it as a time policy.
- **R5** — Tests SHALL cover the parser directly and each caller listed above, including the inline
  `memory:` tag.

## Notes

- Open question for shaping: accept `K`/`M`/`G`/`T` with an optional `B` or `iB`, all as powers of
  1024 as `GB` is today, or refuse everything but the documented forms. Accepting them is the
  kinder reading; either way the silent case goes.
- Open question for shaping: a bare number is bytes here and megabytes in Slurm. Keep, or require a
  unit.
- Open question for `/hs-plan`: tasks already stored with a mis-read size cannot be told from tasks
  that meant it. Whether a release note is enough.
- Open question for `/hs-plan`: whether `parse_time` is fixed in the same change or removed, since
  it has no caller.
- Open question for `/hs-plan`: how a size written in a configuration file reaches these paths, and
  whether it is checked there too.
- Related: `spec/croniter-missing-guidance/` (the rotation-policy discriminator, and the note that
  left digit-leading cron expressions out of scope).
- Related: [`group-tag-integer-validation.md`](group-tag-integer-validation.md), another value
  accepted at submit time that should have been refused.
- Found by: an out-of-cycle build against the library (2026-11-11).
