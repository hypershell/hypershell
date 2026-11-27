# queue-wire-codec — R10 throughput gate against 2.9.0a9 (P5)

- **Command:** `uv run python tests/bench/bench_queue.py gate --base 2.9.0a9 --feature HEAD --repeats 5 --out spec/queue-wire-codec/drive/gate.jsonl` with `--only` for every `b=1` row and every `client-default` row (bundle size 1 is also the configured default).
- **Refs:** base `2.9.0a9` (1acc548632dc), feature `HEAD` = c2a8be87 (P4 committed).
- **Host:** the build host (macOS 27.0.1, Apple silicon, arm64), 2026-11-23, 14:01-14:29 local; both builds ran on Python 3.15.2 (the interpreter `uv sync` chose for each locked build), start method spawn.
- **Load average:** 2.50 / 3.17 / 3.21 at the start, 15.00 / 11.99 / 8.87 at the end (runs interleaved base/feature, so both builds saw the same load).
- **Q4 rule** (`drive/q4.py`): the feature's median is at least the base's median less the larger of 5% of it and the base's own min-max spread.

## Bench table (as printed)

| config | base median [min-max] | feature median [min-max] | delta | tolerance | throughput | tree children b/f | tree FDs b/f | tree threads b/f | own threads b/f | own FDs b/f | tree RSS MB b/f | clean | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4x4 b=1 tls | 312.0 [302.4-317.9] | 309.3 [299.0-312.8] | -2.7 | 15.6 | PASS | 0/0 | 22/22 | 20/20 | 20/20 | 22/22 | 101.5/101.7 | True | PASS |
| 4x4 b=1 no-tls | 320.7 [315.0-336.5] | 311.6 [298.2-312.0] | -9.1 | 21.5 | PASS | 0/0 | 23/22 | 20/20 | 20/20 | 23/22 | 97.6/97.4 | True | PASS |
| 16x1 b=1 tls | 307.5 [289.9-309.6] | 300.2 [294.0-306.5] | -7.3 | 19.7 | PASS | 0/0 | 59/59 | 56/56 | 56/56 | 59/59 | 108.2/104.7 | True | PASS |
| 16x1 b=1 no-tls | 322.0 [312.7-332.7] | 321.8 [297.6-325.0] | -0.2 | 27.4 | PASS | 0/0 | 59/59 | 56/56 | 56/56 | 59/59 | 101.4/97.3 | True | PASS |
| 32x1 b=1 tls | 318.7 [309.2-322.7] | 317.5 [311.1-323.4] | -1.2 | 15.9 | PASS | 0/0 | 107/107 | 104/104 | 104/104 | 107/108 | 108.4/110.8 | True | PASS |
| 4x4 b=1 client-default tls | 318.4 [311.5-331.7] | 307.5 [304.4-310.2] | -10.9 | 20.2 | PASS | 0/0 | 22/23 | 20/20 | 20/20 | 22/23 | 99.5/99.8 | True | FAIL |
| 16x1 b=1 client-default no-tls | 315.9 [313.2-321.7] | 310.8 [297.9-323.6] | -5.1 | 25.7 | PASS | 0/0 | 60/59 | 56/56 | 56/56 | 60/59 | 98.5/98.9 | True | PASS |
| 4x4 b=50 client-default tls | 614.5 [578.2-631.5] | 622.0 [620.3-630.6] | +7.5 | 53.3 | PASS | 0/0 | 23/23 | 20/20 | 20/20 | 23/24 | 101.4/103.6 | True | PASS |
# gate: FAIL (5 repeats per build, interleaved; load average 15.0 at the end)

The bench's own verdict column reads FAIL on `4x4 b=1 client-default tls` because of its footprint
check (the feature's peak tree descriptor count was 23 against the base's 22, one sample); its
throughput column passes there, as on every row. Q4 judges throughput only.

## Q4 (`python3 spec/queue-wire-codec/drive/q4.py spec/queue-wire-codec/drive/gate.jsonl`)

| config | base median [min-max] | feature median [min-max] | delta | allowed drop | runs b/f | Q4 |
|---|---|---|---|---|---|---|
| 4x4 b=1 tls | 312.0 [302.4-317.9] | 309.3 [299.0-312.8] | -2.7 (-0.9%) | 15.6 | 5/5 | PASS |
| 4x4 b=1 no-tls | 320.7 [315.0-336.5] | 311.6 [298.2-312.0] | -9.1 (-2.8%) | 21.5 | 5/5 | PASS |
| 16x1 b=1 tls | 307.5 [289.9-309.6] | 300.2 [294.0-306.5] | -7.3 (-2.4%) | 19.7 | 5/5 | PASS |
| 16x1 b=1 no-tls | 322.0 [312.7-332.7] | 321.8 [297.6-325.0] | -0.2 (-0.1%) | 20.0 | 5/5 | PASS |
| 32x1 b=1 tls | 318.7 [309.2-322.7] | 317.5 [311.1-323.4] | -1.2 (-0.4%) | 15.9 | 5/5 | PASS |
| 4x4 b=1 client-default tls | 318.4 [311.5-331.7] | 307.5 [304.4-310.2] | -10.9 (-3.4%) | 20.2 | 5/5 | PASS |
| 16x1 b=1 client-default no-tls | 315.9 [313.2-321.7] | 310.8 [297.9-323.6] | -5.1 (-1.6%) | 15.8 | 5/5 | PASS |
| 4x4 b=50 client-default tls | 614.5 [578.2-631.5] | 622.0 [620.3-630.6] | +7.5 (+1.2%) | 53.3 | 5/5 | PASS |
# Q4: PASS

The held-back lever (large `str`/`bytes` values outside the JSON part of the frame) was not needed.

## Skew and parity against 2.9.0a9

- skew old client 2.9.0a9 -> new server, tls=on: old client rc 5 in 0.4 s (bound 30), refusal logged 1x, new client rc 0, server rc 0, 200/200 tasks done -> PASS
- skew new client -> old server 2.9.0a9, tls=on: rc 5 in 0.4 s (bound 30), critical lines 1: CRITICAL [hypershell.client] QueueVersionError: Server at localhost:53199 speaks the queue framing of a release before 2.9.0a10; run matching server and client  -> PASS
- skew old client 2.9.0a9 -> new server, tls=off: old client rc 5 in 0.4 s (bound 30), refusal logged 1x, new client rc 0, server rc 0, 200/200 tasks done -> PASS
- skew new client -> old server 2.9.0a9, tls=off: rc 5 in 0.4 s (bound 30), critical lines 1: CRITICAL [hypershell.client] QueueVersionError: Server at localhost:53227 speaks the queue framing of a release before 2.9.0a10; run matching server and client  -> PASS
- parity tls=on: rows 10 vs 10, identical=True; group order base True feature True; rc base 0/[0, 0] feature 0/[0, 0]; wall s base 5.0 feature 5.0 -> PASS
- parity tls=off: rows 10 vs 10, identical=True; group order base True feature True; rc base 0/[0, 0] feature 0/[0, 0]; wall s base 4.9 feature 4.9 -> PASS

## Rerun after the F1 remediation (review cycle 1)

The client's proxies and its `recv` are on the per-call path, so the gate was rerun as above, with
`--out spec/queue-wire-codec/drive/gate-f1.jsonl`.

- **Refs:** base `2.9.0a9` (1acc548632dc), feature `HEAD` = cf7b1330 (the F1 remediation).
- **Host:** the build host, 2026-11-23, 16:19-16:47 local; both builds on Python 3.15.2, start method spawn.
- **Load average:** 2.38 / 2.56 / 2.64 at the start, 15.41 / 12.38 / 8.85 at the end (interleaved runs).

| config | base median [min-max] | feature median [min-max] | delta | allowed drop | runs b/f | Q4 |
|---|---|---|---|---|---|---|
| 4x4 b=1 tls | 319.6 [314.0-327.1] | 316.0 [310.1-319.7] | -3.6 (-1.1%) | 16.0 | 5/5 | PASS |
| 4x4 b=1 no-tls | 325.2 [320.8-326.4] | 322.6 [314.9-327.0] | -2.6 (-0.8%) | 16.3 | 5/5 | PASS |
| 16x1 b=1 tls | 319.6 [314.0-321.8] | 313.6 [309.5-331.6] | -6.0 (-1.9%) | 16.0 | 5/5 | PASS |
| 16x1 b=1 no-tls | 326.7 [317.7-332.9] | 323.3 [316.6-325.4] | -3.4 (-1.0%) | 16.3 | 5/5 | PASS |
| 32x1 b=1 tls | 322.0 [314.6-328.7] | 314.9 [311.3-316.9] | -7.1 (-2.2%) | 16.1 | 5/5 | PASS |
| 4x4 b=1 client-default tls | 313.9 [304.9-318.3] | 318.1 [314.8-325.5] | +4.2 (+1.3%) | 15.7 | 5/5 | PASS |
| 16x1 b=1 client-default no-tls | 325.1 [303.3-330.5] | 319.3 [317.6-323.0] | -5.8 (-1.8%) | 27.2 | 5/5 | PASS |
| 4x4 b=50 client-default tls | 594.4 [585.0-609.3] | 585.7 [569.9-597.6] | -8.7 (-1.5%) | 29.7 | 5/5 | PASS |
# Q4: PASS

The bench's own verdict column reads FAIL on `16x1 b=1 no-tls` and `4x4 b=50 client-default tls`, from
its footprint check (a peak descriptor count one above the base's, one sample each); its throughput
column passes on every row. Q4 judges throughput only.
