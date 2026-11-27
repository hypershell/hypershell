# 01 — How `--delay-start` is parsed, and what it emits to a launched client

GOAL-deferred question (Clarifications → *Left for `/hs-plan`*): "How `--delay-start` is parsed (a
signed, unrounded variant of the duration parser, or a wrapper) and how its error names the random
form." Also R6's launched command line. Researched sequentially by the planner (no subagent): the
answers rest on probes of `develop` (`6c46cbd` + spec commits, HEAD `e4bad90`), run 2026-11-14 with
the package installed under Python 3.14.6 and 3.11.18.

## The parser today

`core/types.py:123-147` (2.9.0a7): `parse_time` strips, tries four full-match patterns in turn
(`TIME_SECONDS_PATTERN`, `TIME_UNITS_PATTERN`, `TIME_CLOCK_PATTERN`, `TIME_DAYS_PATTERN`), sums the
groups as `Fraction(num) * TIME_SCALES[unit]`, and returns `math.ceil` of the sum; anything else raises
`UnitError(f'Duration {value!r} is not valid: expected {TIME_FORMS}')`. `UnitError` is both a
`ValueError` and an `argparse.ArgumentTypeError`, so argparse prints its message verbatim (exit 2).
No pattern admits a sign; the bare-number form is `\d+(?:\.\d+)?` (no leading dot, no exponent).

## What `-d` accepts today (`type=float`), by probe

`ClientApp` and `ClusterApp` `interface.parse_args`, identical on 3.14 and 3.11 except where noted:

| argv | develop | note |
|---|---|---|
| `-d -600` | -600.0 | two words: argparse's negative-number rule passes it on every version |
| `-d -0.5` | -0.5 | same |
| `-d 0.5` | 0.5 | |
| `--delay-start=-600`, `-d-600` | -600.0 | |
| `-d -10m` | 3.14: `invalid float value: '-10m'`; **3.11: `expected one argument`** | the two-word unit form never reaches `type=` before 3.14 (GOAL non-goal) |
| `--delay-start=-10m`, `-d-10m` | `invalid float value: '-10m'` on both | reaches `type=` on every version |
| `-d +5` | 5.0 | `float()` accepts |
| `-d 1e3` | 1000.0 | `float()` accepts |
| `-d .5` | 0.5 | `float()` accepts |

`float()` also accepts `5.`, `inf`, `nan`, `1_000`, and surrounding spaces. `inf`/`nan` are not usable
(`ClientThread.wait_start`, `client.py:1332-1342`: `nan` falls to `random.uniform(0, nan)` and
`time.sleep(nan)` raises; `inf` sleeps forever).

## Recommendation

A new **`parse_delay(value) -> float`** in `core/types.py`, beside `parse_time`, sharing its matcher:

- Factor the pattern loop of `parse_time` into a private helper that returns the exact `Fraction` of
  seconds or `None`. `parse_time` becomes `ceil` of it (behaviour byte-for-byte unchanged, same
  message). `parse_delay` strips, takes **one optional leading `-`**, matches the remainder with the
  same helper, and returns `float(±fraction)` — **no rounding**.
- `float(Fraction(s)) == float(s)` for every decimal string probed (`0.1`, `0.5`, `-600`, `600.25`,
  `123456789.123456789`, …), so every bare number in R5's list reads exactly as `float()` read it.
- Refusal: the message begins with exactly the message `-W` gives for the same text (`Duration '1hr'
  is not valid: expected …`) and adds one clause for the random form, e.g. `; a leading - waits a
  random time up to that duration (e.g., --delay-start=-10m)`. R4 ("the error `-W` gives") is held by
  the prefix; the clause answers the GOAL's "how its error names the random form".
- A value too large for a float (`Fraction` → `OverflowError`) is refused with the same message.

A wrapper over `parse_time` was rejected: it would have to undo the ceiling (lossy) to keep fractions.

## R5 vs R4 for spellings only `float()` took

`+5`, `.5`, `5.`, `1e3`, `inf`, `nan`, `1_000` were accepted by `type=float`. R5 keeps "every value
accepted today"; R4 refuses anything "not a duration in full". Neither the a7 grammar nor the GOAL's
named R5 values (`30`, `0.5`, `-600`, `--delay-start=-600`) include them. The precedent is a7's own
review, which dropped `-W +60` and `-W 1_000` as observations under the same tension. **Recommended:
refuse them (one grammar), state it in the user-facing note** — a foreman decision (PLAN §2, D1).

## R6 — the launched command line

`cluster/launch.py::client_options` (`:432-467`) emits `'-d', str(delay_start)` as two words, and
`'-w', str(bundlewait)`, `'-S', str(task_signalwait)`. With `parse_time` the latter two are `int`, so
`str` gives bare whole seconds that `type=int` (every client back to 2.9.0a4; 2.9.0a8's
`client.py:1603/1606/1619` is still `int`/`float`/`int`) reads.

The delay is a `float`. `str(float)` switches to exponent notation for small and large magnitudes:
`str(1e-05) == '1e-05'`, `str(1e16) == '1e+16'`. An older client's `float()` reads that, but the new
client's `parse_delay` would refuse it, and a negative exponent form (`-1e-05`) as a separate word
is not matched by argparse's negative-number rule on 3.11 (`^-\d+$|^-\d*\.\d+$`). Probe:
`format(Decimal(repr(v)), 'f')` gives `0.00001`, `-0.00001`, `10000000000000000`, `-600.0`, `0.5`,
`30.0`, `0` — a plain decimal both readers take, exact (it formats `repr`, the shortest round-trip
string). **Recommended:** `client_options` writes `-d` through such a formatter; every value that
prints without an exponent today prints identically. Keep the two-word shape: `-600.0` matches the
negative-number rule on every supported Python.

## High-blast-radius contact

`client.py` is on the list. The change there is the `-d` `add_argument` (`type=parse_delay`), the
`core.types` import line and the `APP_HELP` line. `ClientThread.wait_start` is untouched: it already
treats `== 0`, `> 0` and `< 0` as today.
