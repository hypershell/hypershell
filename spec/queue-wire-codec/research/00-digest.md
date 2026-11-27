# 00 — Digest: decisions for `queue-wire-codec`

Four briefs (2026-11-23). Two were fanned out (01, 02); 03 and 04 were written by the planner. One
recommendation per question; where briefs disagree, the decision here wins.

| # | Brief | Question |
|---|---|---|
| 01 | [`01-envelope-inventory.md`](01-envelope-inventory.md) | The manager's envelope on 3.11–3.14, HyperShell's traffic, R8 sizing, R9 pins |
| 02 | [`02-old-peer.md`](02-old-peer.md) | What a 2.9.0a9-or-earlier peer does with the fixed reply (live probe) |
| 03 | [`03-codec-design.md`](03-codec-design.md) | Encoding, `bytes`, hook points, cost |
| 04 | [`04-tests-docs-bench.md`](04-tests-docs-bench.md) | Tests to restate, docs to rewrite, the benchmark and skew check |

## Decisions

1. **Where.** A pure `core/codec.py`; HyperShell's two connection classes override only `send` and
   `recv` with it (`SecureConnection`; a new `_PlainConnection` subclass of the stock `Connection`,
   never a patch of the stock class, which the forked manager's pipe shares). Framing and the
   challenge are untouched. The manager's envelope is identical on 3.11–3.14 and reaches the
   connection only through `send`/`recv` (01 §1).
2. **Encoding.** JSON; a tuple is a JSON array; `list`, `dict`, `bytes`, `Token` and the three
   exceptions are one-key tag objects; strict types (`bool` apart from `int`); `NaN`/`Infinity`
   refused; any other exception in `#ERROR` crosses as `RemoteError("<Type>: <text>")` (03; 01 §2).
3. **`bytes`.** A tagged string with one code point per byte (Latin-1): total, as cheap as UTF-8 for
   HyperShell's all-ASCII payloads. Switching producers to `str` and base64 rejected (03).
4. **R8 limits.** Depth 8, 64 elements per container, checked by the codec's own walk. HyperShell's
   largest real message is depth 3 with 9 elements; the stock paths HyperShell never takes reach 4
   and 11. Bundles cross as one leaf, so bundle size (unbounded in configuration) never touches the
   limits (01 §4). 01 recommended these values; 03's prototype used 32/4096 only as a placeholder.
5. **Refusal.** The connection closes itself before raising (the manager neither closes on a
   non-`EOFError` from `recv` in `serve_client`, nor drops a client's cached connection). Server side:
   an `EOFError` subclass, a WARNING naming the peer, other connections served. Client side:
   `QueueVersionError` (fatal; a plan-gate question for the maintainer: the GOAL does not fix the
   client's next step for R2) (01 §1).
6. **Fixed reply to an older peer.** `pickle.dumps(('#ERROR', RuntimeError(TEXT)), protocol=4)`,
   built once at import, bytes pinned by a test. This supersedes 03's `('#TRACEBACK', …)` at protocol
   2: on the probe every 2.9.0a9 role exits 5 within a second either way, but `RuntimeError` gives one
   clean CRITICAL line and no traceback file, and unpickles on every release from 2.8.x (a
   `QueueVersionError` would not before 2.9.0a4) (02). **Q2's option (b) holds; (c) is not needed.**
7. **GOAL premise correction (informational).** In 2.9.0a9 only the relay's upstream link redials on
   `EOFError`; `hs client` and `hs submit` have no reconnect layer and exit on a bare close too. The
   relay is the role that needed (b) (02).
8. **New client, old server.** The codec's first byte `[` is not a pickle opcode; an old server
   answers with a pickled `#TRACEBACK`, whose `0x80` the new client refuses as `QueueVersionError`,
   already fatal and mapped to `runtime_error` (Q3) (03).
9. **Pins (R9).** 01 §5's `co_names`/`co_consts` checks, verified on 3.11.18, 3.12.13, 3.13.16 and
   3.14.6, join `_INTERNALS`; string constants must be read through nested tuples (01 §5).
10. **Tests to restate (R7).** The three the GOAL names plus two `TCP_NODELAY` tests in
    `tests/test_queue.py` whose stock end would unpickle the codec's message; the 2.9.0a4 launch test
    becomes a refusal check (04).
11. **R10.** `bench_queue.py gate --base 2.9.0a9`, read against Q4 by hand (the tool's default rule
    also admits the feature's own spread). The codec adds ≈1–4 µs per small message and ≈2 µs per task
    in a 1,000-task reply (01, 03); the lever if the gate fails is carrying large leaves out of band in
    the same frame (03).
12. **Docs (R11).** `docs/alternatives.rst:826-828` also describes the pickle framing and changes with
    the named pages; the change log is the PR's squash body (04).
