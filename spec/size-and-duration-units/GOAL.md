# GOAL — Sizes and durations written with common units are read as written

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** size-and-duration-units
- **kind:** fix
- **appetite:** small

## Problem

A user writes `16G` of memory and a timeout of `1h30m`. HyperShell reads the first as 16 bytes
without a word, and refuses the second. **Pre-existing** on `develop` and on the 2.8 line; found
from outside a cycle, while building a separate tool against the library (2026-11-11).

**Memory sizes are read by their leading number.** Every memory size goes through `parse_bytes`
(`src/hypershell/core/types.py:95-100`), and a unit it does not know is dropped while the number is
kept. Observed against 2.9.0a6:

| Given | Read as |
|---|---|
| `16GB` | 17 179 869 184 (correct) |
| `16G`, `16g`, `16GiB` | 16 |
| `512M` | 512 |
| `1.5GiB` | 1 |
| `8,000MB` | 8 |
| `8GB of ram` | 8 589 934 592 (the trailing text is ignored) |

There is no error and no warning, and `16G` is what a Slurm user types by habit. The paths
affected: `hs submit -m/--memory` (`submit.py:1283`), `hs cluster -m/--memory` and
`-M/--client-memory` (`cluster/__init__.py:227`, `232`), `hs server -m/--task-memory`
(`server.py:1599`), `hs client -M/--client-memory` (`client.py:1637`), and the inline `memory:` tag
(`data/model.py:529`). Read from the source, not reproduced on a cluster: a task declared with `16G`
asks for 16 bytes, so declared memory stops limiting how many such tasks a client runs at once; a
client started with `-M 64G` offers 64 bytes, and any task declaring real memory is judged too
large for it.

The same function is the size-or-time test for the log rotation policy (`core/logging.py:1055`,
and `463`), so a cron expression that begins with a digit (`30 2 * * *`) is read as "rotate every
30 bytes". `spec/croniter-missing-guidance/` recorded this as out of scope and never seeded it.

**Durations take only a bare integer of seconds.** The task timeouts (`hs submit -W`, `hs server
-W`, `hs cluster -W`, `hs client -W`) and client timeouts (`hs cluster -T`, `hs client -T`) are
declared `type=int`, so `1h30m` is an argument error. The inline `timeout:` tag is not converted at
all (`data/model.py:530`): `timeout:1h30m` reaches the task as the text `1h30m`. A duration parser,
`parse_time` (`core/types.py:113-118`), exists but nothing in `src/` calls it, and it has the same
leading-match flaw: `1h30m` reads as 3 600, `5MB` as 300, and `1.5h` and `90` are refused.

## Outcome / vision

A size or a duration written the way people write it is read as what it says: `16G` and `16GiB`
are sixteen gibibytes, `1h30m` and `1:30:00` are ninety minutes. A value that is not a size or a
duration in full is refused with a message that says which forms are taken. Nothing is read as a
prefix of itself, and every value accepted today keeps its meaning.

## Acceptance criteria (the contract)

- **R1** — WHEN a memory size is given with the unit `K`, `M`, `G` or `T`, alone or followed by
  `B` or `iB`, in any case (`16G`, `16g`, `16GB`, `16GiB`, `512M`, `1.5GiB`), to `hs submit -m`,
  `hs cluster -m` or `-M`, `hs server -m`, `hs client -M`, or in the inline `memory:` tag, HyperShell
  SHALL read it as the number times 1024, 1024², 1024³ or 1024⁴ bytes respectively, so `16G`, `16GB`
  and `16GiB` are all 17 179 869 184 bytes.
- **R2** — WHEN a duration is given with the units `s`, `m`, `h` or `d`, alone or several in one
  value, with a whole or fractional number (`45s`, `90m`, `1h30m`, `1d12h`, `1.5h`), to a task
  timeout (`hs submit -W`, `hs server -W`, `hs cluster -W`, `hs client -W`) or a client timeout
  (`hs cluster -T`, `hs client -T`), HyperShell SHALL read it as the total in seconds (`1h30m` and
  `1.5h` → 5 400, `1d12h` → 129 600).
- **R3** — WHEN a duration given to any option in R2 is in a Slurm `--time` form with a colon or a
  dash — `MM:SS`, `HH:MM:SS`, `D-HH`, `D-HH:MM` or `D-HH:MM:SS` — HyperShell SHALL read it as Slurm
  does (`90:00` and `1:30:00` → 5 400, `1-12` and `1-12:00:00` → 129 600). A bare integer stays
  seconds (R6), not Slurm's minutes.
- **R4** — WHEN a task's inline `timeout:` tag carries a duration in a form R2 or R3 accepts
  (`timeout:1h30m`, `timeout:1:30:00`), the task SHALL be submitted with that many seconds as its
  timeout, as `hs info` shows it, just as the inline `memory:` tag is already stored as bytes.
- **R5** — IF a value given to any option or tag in R1–R4 is not a size or a duration in full
  (`8,000MB`, `8GB of ram`, `16X`, `16GBB`; as a duration `5MB`, `1hr`, `2hours`, `1w`),
  THEN the command SHALL exit non-zero with an error that names the value and the forms accepted,
  and SHALL NOT read any value by its leading part alone.
- **R6** — Every value accepted today SHALL be read as the same value as before: for memory `16GB`,
  `512MB`, `2KB`, `16 GB`, `1.5GB`, `16gb`, and a bare integer as bytes; for a duration, a bare
  integer as seconds, on every option in R2 and in the inline `timeout:` tag.
- **R7** — WHEN the file log rotation policy is a cron expression that begins with a digit
  (`30 2 * * *`), logging SHALL treat it as a time-based policy, not a size; a size policy (`2GB`,
  and now `2G`) SHALL stay size-based.
- **R8** — The help text and documentation of each memory option and of the inline `memory:` tag
  SHALL state that `K`, `M`, `G` and `T`, with or without `B` or `iB`, are powers of 1024; those of
  each option in R2 and of the inline `timeout:` tag SHALL state which duration forms it takes.
- **R9** — The release notes for this fix SHALL state that a size written with a short or binary
  unit (`16G`, `16GiB`) was read before the fix as that many bytes, and that tasks already stored
  that way are not corrected.
- **R10** — Tests SHALL cover both parsers directly (each accepted form and each refusal above),
  and each option and inline tag named in R1–R4, and the rotation policy in R7.

## Non-goals (no-gos)

- **Units on the other duration options.** `--bundlewait`, `--signalwait`, `--delay-start`, the
  autoscaling `--period`, and the `--interval` on `hs wait` and `hs run` keep taking a bare integer
  in this change. Deferred to a follow-up seed, to be filed as
  `issues/duration-units-remaining-options.md`.
- Long or other unit spellings: `sec`, `min`, `hr`, `hours`, `days`, and weeks (`w`). Refused (R5).
- Reading a bare integer duration as minutes, as Slurm's `--time` does. It stays seconds.
- The decimal reading of `GB` as 10⁹ bytes. Settled: every memory unit is a power of 1024.
- Correcting or migrating tasks already stored with a misread size. A release note only (R9).
- Units on any other numeric option or inline tag (cores, group, and so on);
  `issues/group-tag-integer-validation.md` is a separate seed.
- How sizes and durations are displayed (`hs list`, `hs info`, logs), and how memory and timeouts
  are enforced or scheduled once read.

## Constraints

- **Ships in a patch and a pre-release.** The fix lands on `develop`, ships in 2.9.0a7, and is
  cherry-picked from `develop` onto the 2.8 line for 2.8.7. Keep the change confined to the two
  parsers, their call sites, and their tests, plus the help snippets, docs and release notes R8 and
  R9 require, so the cherry-pick onto 2.8 applies cleanly. No schema, config-model or wider CLI
  refactor rides along.
- Docs and examples name only top-level commands (never `hs task …`), and never pass a key with
  `-k` on a command line.

## Clarifications

- **Q:** Which spellings of a time unit, fractions, and Slurm forms are taken? — **A:** The units
  `s`, `m`, `h` and `d`, combined in one value (`1h30m`, `1d12h`) and fractional (`1.5h`). A bare
  integer is seconds, as today. The Slurm forms `[D-]HH:MM:SS`, `MM:SS`, `D-HH` and `D-HH:MM`, per
  Slurm's `--time` grammar, are also accepted. Long spellings (`hours`, `hr`) are not (resolved
  2026-11-11, maintainer via foreman).
- **Q:** Which duration options take units in this change? — **A:** Only those the seed listed:
  the task timeouts on submit, server, cluster and client, the client timeouts on cluster and
  client, and the inline `timeout:` tag. `--bundlewait`, `--signalwait`, `--delay-start`, the
  autoscaling `--period` and `--interval` on `hs wait`/`hs run` are non-goals, deferred to a
  follow-up seed (resolved 2026-11-11, maintainer via foreman).
- **Q:** What do the memory units mean? — **A:** `K`, `M`, `G` and `T`, each alone or with `B` or
  `iB`, in any case, are all powers of 1024. That is what `GB` means here today and what Slurm does
  with `--mem=16G`; the strict 10⁹ reading would shrink every existing declaration by about 7%
  (settled 2026-11-11 in the seed; reconfirmed 2026-11-11, maintainer via foreman).
- **Q:** What about tasks already stored with a misread size? — **A:** A release note only; no
  migration (resolved 2026-11-11, maintainer via foreman).
- **Q:** Which release carries the fix? — **A:** A patch release, 2.8.7, cherry-picked from
  `develop` onto the 2.8 line, and 2.9.0a7. Prefer a change confined to the parsers, their call
  sites and their tests (resolved 2026-11-11, maintainer via foreman). See Constraints.
- **Left for `/hs-plan` (not clarification markers):**
  - How a size or a timeout written in a configuration file reaches these paths, and whether it is
    read by the same parsers.
  - Rounding of a fractional value that is not a whole number of seconds or bytes (`0.5s`,
    `1.3K`), and whether duration units are taken in upper case too. The contract requires only the
    lower-case forms above.
  - Whether tasks read before a refused inline tag are kept; follow how `hs submit` already
    handles an invalid line.
  - The `versionchanged` marker and release-note placement for a change that ships in both 2.8.7
    and 2.9.0a7 (AGENTS.md § Docs).
  - Root cause: the suspected mechanism above (an unanchored leading match in `parse_bytes` and
    `parse_time`, `type=int` on the duration options) is a lead, not part of the contract.

## Related materials

- Seed: [`issues/size-and-duration-units.md`](../../issues/size-and-duration-units.md)
- Source: `src/hypershell/core/types.py` (`parse_bytes`, `parse_time`, `MEMORY_PATTERN`,
  `TIME_PATTERN`); call sites in `submit.py`, `server.py`, `client.py`, `cluster/__init__.py`,
  `data/model.py` (inline tags) and `core/logging.py` (rotation policy).
- Prior art: `spec/croniter-missing-guidance/` (the rotation-policy discriminator, and the note
  that left digit-leading cron expressions out of scope).
- Related seed: [`issues/group-tag-integer-validation.md`](../../issues/group-tag-integer-validation.md).
- Slurm `sbatch --time` grammar: <https://slurm.schedmd.com/sbatch.html#OPT_time>.
