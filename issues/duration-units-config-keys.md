---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Duration settings read only from configuration still take bare seconds only

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Since 2.9.0a7 (`size-and-duration-units`) a task timeout takes units (`-W 5m`), and
`duration-units-remaining` (#106) extended that to every duration option and the configuration keys
behind them. Several duration settings have no option at all and are read straight from
configuration or the environment, and those still take a bare number of seconds only:
`server.evict`, `client.heartrate`, and the relay's `bundlewait`, `poll`, `retention`, `timeout`,
`upstream.heartrate`, `upstream.evict_hint`, `upstream.deadline` and `upstream.backoff`. A user who
writes `evict = "10m"` in a configuration file, as they now can for `autoscale.period`, gets an
error or a misread value instead.

Found while shaping `duration-units-remaining` (its Non-goals).

## Direction

Read each of these keys, and its `HYPERSHELL_*` environment variable, with the same duration parser
the options use: bare numbers stay seconds, units and Slurm forms are accepted, anything else is an
error naming the key and the accepted forms. Update the configuration reference to state the forms.

## Related

- `spec/size-and-duration-units/`, `spec/duration-units-remaining/` (the parser and its rules).
