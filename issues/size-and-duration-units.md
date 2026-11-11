---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Sizes and durations written with common units are misread or refused

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A user writes `16G` of memory and a timeout of `1h30m`. HyperShell reads the first as 16 bytes
without a word, and refuses the second. Both come from the two unit parsers in
`src/hypershell/core/types.py`.

### Memory sizes

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
2.9.0a6 (the code is the same on `develop` and on the 2.8 line):

| Given | Read as |
|---|---|
| `16GB` | 17 179 869 184 (correct) |
| `16G`, `16g` | 16 |
| `512M` | 512 |
| `16GiB` | 16 |
| `1.5GiB` | 1 |
| `8,000MB` | 8 |
| `8GB of ram` | 8 589 934 592 (the trailing text is ignored) |

There is no error and no warning. `16G` and `512M` are what a user of Slurm types by habit.

Every place a memory size is read goes through this function:

- `hs submit -m/--memory` (`src/hypershell/submit.py:1283`)
- `hs cluster -m/--memory` and `-M/--client-memory` (`src/hypershell/cluster/__init__.py:227`, `232`)
- `hs server -m/--task-memory` (`src/hypershell/server.py:1599`)
- `hs client -M/--client-memory` (`src/hypershell/client.py:1637`)
- the inline `memory:` tag (`src/hypershell/data/model.py:529`)

What follows, read from the source and not reproduced on a cluster:

- **A task declared with `16G` asks for 16 bytes.** It fits beside anything, so declared memory no
  longer limits how many such tasks a client runs at once, and the client then warns that the task
  exceeded its allocation.
- **A client started with `-M 64G` offers 64 bytes.** A task that declares a real amount of memory
  is judged too large for it.

The same function is the size-or-time test for the log rotation policy
(`src/hypershell/core/logging.py:1055`, and `463`). A cron expression that begins with a digit is
therefore read as a size: `30 2 * * *` becomes "rotate every 30 bytes". That was recorded as out of
scope in `spec/croniter-missing-guidance/research/00-digest.md` and never seeded. It has the same
cause.

### Durations

Every duration is a whole number of seconds. The options are declared `type=int`, so a unit is an
argument error:

- `hs submit -W/--timeout` (`src/hypershell/submit.py:1286`)
- `hs server -W/--task-timeout` (`src/hypershell/server.py:1600`)
- `hs cluster -W/--task-timeout` and `-T/--timeout` (`src/hypershell/cluster/__init__.py:242`, `243`)
- `hs client -W/--task-timeout` and `-T/--timeout` (`src/hypershell/client.py:1615`, `1614`)

The inline `timeout:` tag is not converted at all (`src/hypershell/data/model.py:530`), unlike
`memory:` one line above it: `timeout:1h30m` reaches the task as the text `1h30m`.

A parser for durations exists, `parse_time` (`src/hypershell/core/types.py:113-118`), and nothing
in `src/` calls it. It has the same flaw as `parse_bytes`, and reads one unit only:

| Given | Read as |
|---|---|
| `1h`, `90m` | 3 600, 5 400 (correct) |
| `1h30m` | 3 600 (the `30m` is dropped) |
| `1d12h` | 86 400 (the `12h` is dropped) |
| `5MB` | 300 |
| `1.5h` | refused |
| `90` | refused |

## Why it was deferred

Found from outside a cycle, while building a separate tool against the library, so there was no
change set to fix it in. **Pre-existing** in `develop` and in the 2.8 line.

## Outcome / vision

A size or a duration written the way people write them is read as what it says: `16G` and `16GiB`
are sixteen gigabytes, `1h30m` is ninety minutes. A value that is not a size or a duration is
refused with a message that says which forms are taken. Nothing is ever read as a prefix of
itself.

## Sketch of the acceptance criteria

- **R1** — WHEN a memory size is written with a short unit (`16G`, `512M`) or a binary unit
  (`16GiB`, `512MiB`), in any case, it SHALL be read as the same quantity as the long form (`16GB`,
  `512MB`). The units `K`, `M`, `G` and `T`, each with an optional `B` or `iB`, SHALL all be powers
  of 1024.
- **R2** — WHEN a duration is written with units, including several in one value (`1h30m`,
  `1d12h`, `90m`, `45s`), it SHALL be read as their sum in seconds, wherever a task timeout or a
  client timeout is given.
- **R3** — WHEN a size or a duration is not recognised in full (`8,000MB`, `8GB of ram`, `5MB` as a
  duration), the command SHALL refuse it with a message that names the value and the forms
  accepted. No value SHALL be read by its leading part alone.
- **R4** — Every form accepted today SHALL be read as the same value as before: `16GB`, `512MB`,
  `16 GB`, `1.5GB`, lower case, a bare integer as bytes; a bare integer of seconds for a duration.
- **R5** — WHEN the inline `timeout:` tag carries a duration with units, the task SHALL get that
  many seconds, as the inline `memory:` tag already gets bytes.
- **R6** — WHEN the log rotation policy is a cron expression that begins with a digit, logging
  SHALL treat it as a time policy.
- **R7** — The documentation of each memory option SHALL state that its units are powers of 1024,
  and of each duration option which forms it takes.
- **R8** — Tests SHALL cover both parsers directly and each option and tag listed above.

## Notes

- **The maintainer's direction** (2026-11-11): a user who writes `1h30m` should have it handled
  correctly, not refused, and `16G` and `16GiB` are valid memory quantities and should be handled
  as such. The seed was widened from memory sizes alone to units on that word.
- Open question for shaping: which other duration options take units in the same change. The
  candidates are `--bundlewait`, `--signalwait`, `--delay-start`, the autoscaling `--period`, and
  the `--interval` on `hs wait` and `hs run`.
- Open question for shaping: which spellings of a time unit are taken (`h`, `hr`, `hours`), whether
  a fraction is (`1.5h`), and whether the Slurm forms are (`1:30:00`, `1-12:00:00`).
- **Settled by the maintainer** (2026-11-11): `G`, `GB` and `GiB` are the same quantity, 1024³
  bytes, and likewise for `K`, `M` and `T`. That is what `GB` means here today, what Slurm does
  with `--mem=16G`, and what is meant by the memory of a node. The strict reading (`GB` as 10⁹)
  was considered and not taken: it would shrink every existing declaration by about 7%. R7 has
  the documentation say so.
- Open question for `/hs-plan`: tasks already stored with a mis-read size cannot be told from tasks
  that meant it. Whether a release note is enough.
- Open question for `/hs-plan`: how a size or a timeout written in a configuration file reaches
  these paths, and whether it is read by the same parsers.
- Related: `spec/croniter-missing-guidance/` (the rotation-policy discriminator, and the note that
  left digit-leading cron expressions out of scope).
- Related: [`group-tag-integer-validation.md`](group-tag-integer-validation.md), another inline
  tag whose value is taken as given.
- Found by: an out-of-cycle build against the library (2026-11-11).
