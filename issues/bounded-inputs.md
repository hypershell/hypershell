---
status: adopted:bounded-inputs
kind: fix
appetite: big
target: 2.9.0
lane: public
---

# Every numeric setting has one declared range, one parser and one error

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Numeric input is validated piecemeal. `env-config-keys` (#113) gave every configuration key a
declared kind (`core/config_schema.py`), but bounds were then added one cycle at a time, each as its
own parser or check:

- `cluster-nodb-and-zero-period` (#112): a period greater than zero.
- `config-set-and-unread` (#117): an evict floor, an infinity check and a cap, and a rate floor.
- `submit-input-checks` (#118): 32-bit task groups.

Each review then finds the next value that slips through: a 4,300-digit integer whose error message
cannot be printed, an integer wider than a float, `1e-300`. Every such case so far is absurd input
that no real run uses. The maintainer wants this handled once, centrally, with the program bailing
on bad input, rather than a handler per case (2026-11-22).

## Outcome

- **One range per setting.** Every numeric option and configuration key declares a sane range next
  to its kind in one table, for example:
  - counts (`-N`, `-b`, `-r`, `server.attempts`): 1 (or 0 where 0 means "none") to 2³¹−1;
  - ports: 1 to 65535;
  - durations: 0 or 1 second up to a practical ceiling;
  - rates: 0, or 10⁻⁶ to 10⁹;
  - sizes: up to 2⁶³;
  - groups: 32-bit.
- **One parser per kind.** The parser serves argparse's `type=`, the configuration file and the
  `HYPERSHELL_*` layer alike. Out-of-range, non-numeric or non-finite input is refused with one
  message: `<name>: expected <form> from A to B, given V`. It exits 2 for an option and 3 for
  configuration. V is cut to a printable length.
- **A backstop.** Any `ValueError` or `OverflowError` raised while start-up reads arguments and
  configuration becomes a clean error, never a traceback.
- **No bypass.** The ad hoc floors and caps from #112, #117 and #118 become table entries.
  `hs client`, `hs relay` and the Python API read through the same parsers.
- **Reviews.** The review rubric's "Absurd input is not a finding" rule (2026-11-22) points here.

## Absorbs

These seeds are superseded by this one and retired with it:

- `config-set-wide-integers`;
- `config-text-attempts-crashes`, insofar as it concerns a numeric key;
- the waived #117 N2 (an evict of over 4,300 digits) and `config_seconds`' matching case;
- `hs client`'s own read of a tiny or non-finite `client.ratelimit` (#117 N1);
- an integer rate wider than a float.

## Related

- `core/config_schema.py` (`KEYS`, kinds), `core/types.py` (`parse_time`, `parse_rate`,
  `parse_period`, the group parser), `server.py` (`check_evict`).
- `.agents/factory/review-rubric.md` (Scope).
