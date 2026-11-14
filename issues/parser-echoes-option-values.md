---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A mistyped option can print its value, a key included, to stderr and the log

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

When the command-line parser rejects an option, its message repeats the arguments it could not
place: `unrecognized arguments: -x VALUE …` or `ambiguous option: --au=VALUE could match …`. Every
command sets up logging before it parses its arguments, and cmdkit logs each argument error at
CRITICAL, so the message reaches stderr and any configured log file. When the value is a secret, the
key above all, it is written out.

`key-option-refused` closes the common case: `-k`/`--auth` on `hs client`, `hs server`,
`hs submit`, `hs cluster` and `hs relay` is recognised and refused without echoing the value. What
is left is every other mistyped or ambiguous option, for example `hs server --au=KEY` (ambiguous
between `--auth` and `--autoscaling`), which still echoes its value.

Found by `key-option-refused`'s plan probes (`spec/key-option-refused/research/01-refusal-mechanism.md`).

## Direction

Have the parser report which option it could not place without repeating its value, or redact a
value in the message. The general fix belongs in cmdkit's `Interface` error path; HyperShell would
then depend on that cmdkit release. A HyperShell-only fallback is to catch the parser's error before
it is logged and rewrite the message.

## Related

- `spec/key-option-refused/` (the refusing action, and the probes listing every form that echoes).
- `issues/env-config-key-mapping.md` (other ways a key reaches a command).
