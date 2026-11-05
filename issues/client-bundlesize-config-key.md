---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `hs client -b` reads `submit.bundlesize`, while the docs describe `[client].bundlesize`

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs client`'s `-b`/`--bundlesize` and `-w`/`--bundlewait` take their defaults from
`config.submit.bundlesize` and `config.submit.bundlewait` (`client.py` `ClientApp`), while the
configuration reference documents `[client].bundlesize` and `[client].bundlewait`
(`docs/_include/config_param_ref.rst`) and `client.DEFAULT_BUNDLESIZE` reads `default.client`.
A user who tunes `[client].bundlesize` changes nothing; one who tunes `[submit].bundlesize` for
`hs submit` buffering also changes every plain `hs client`.

Since `zone-dispatch` (Z2) the value matters more: a client advertises its `-b` as the bundle size
it requests whenever the value is explicit, and "explicit" is read as
`config.which('submit', 'bundlesize') != 'default'` (plan gate ruling N4, which kept the key the
client reads today). So a `submit.bundlesize` in a config file is advertised by every client.

## Intention

Decide which key a client reads (`client.*`, as documented, is the likely answer), migrate with a
deprecation note for anyone relying on `submit.*`, and keep R2's rule that only an explicit value is
advertised.

## Refs

- `spec/zone-dispatch/TECH.md` (Plan gate rulings, N4) and `research/03-client-request.md` §3.
