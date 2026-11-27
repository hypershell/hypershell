# REVIEW — The queue's RPC framing carries data, not pickled objects

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** c8f9d96d  ·  **Base:** develop (7c85c83c)  ·  **Date:** 2026-11-23
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Contract drift check:** `GOAL.md` changed after shaping only in 894bccb0, the GOAL gate with the
  maintainer's rulings. The review graded the amended contract.

## Verification run

Fresh reviewer, xhigh effort, with a real 2.9.0a9 install alongside this tree.

- Queue, codec and dispatch suites on 3.14: 430 passed, 3 skipped. The full branch suite also passes.
- CPython-internal pins on 3.11.18, 3.12.13, 3.13.16 and 3.14.6: 13 of 13 each.
- In-process codec probes: the whole vocabulary round-trips with exact types, and arbitrary binary
  `bytes` come back byte for byte. Every out-of-vocabulary form tried is refused: unknown tags,
  malformed tag objects, foreign exceptions, bad Token types, NaN/Infinity, invalid UTF-8, empty
  frames, over-wide and over-deep containers.
- Live, against 2.9.0a9, in both directions:
  - An a9 `hs client` against this server exits 5 with the fixed reply.
  - This tree's client and `hs submit -q` against an a9 server exit 5 with `QueueVersionError`, and
    do not redial.
  - The server logs one WARNING per refused connection and keeps serving.
- A local cluster at `-b 5000` ran all 5000 tasks.
- `SecureManager(serializer=…)` refuses `pickle` and `xmlrpclib`.
- Docs build under `-W` passes. No exploit language in docs, comments, tests or commit messages.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | Pickle frames refused before any parse; live a9 replies refused | ✅ at the codec; see F1 |
| R2 | Out-of-vocabulary probes refused; a refusing connection closes and the server keeps serving | ✅ at the codec; see F1 |
| R3 | Vocabulary round-trip; timed `get`/`put` raise `Empty`/`Full`; decref hand-off; byte-exact payloads | ✅ |
| R4 | The challenge completed in both directions between this tree and a9; framing pins | ✅ |
| R5 | Live in both directions, exit 5, no redial, one WARNING | ✅ (relay inferred from the shared connect path) |
| R6 | Grep of config and schema; `SecureManager` serializer guard | ✅ |
| R7 | `QUEUE_EXPOSED` (9) and `DISPATCH_EXPOSED` (4) unchanged; restated tests match the GOAL's list | ✅ |
| R8 | Refusal past depth 8 and past 64 items; `-b 5000` run admitted | ✅ |
| R9 | 13/13 pins on four Pythons | ✅ |
| R10 | Not re-measured by the reviewer (the build's 5-run bench passed Q4 on all eight rows) | — |
| R11 | Docs updated, markers present, `-W` passes | ❌ one sentence overstates the property (F1) |

Unmapped changes (none are findings):
- Remote exceptions other than `Empty`/`Full` now reach a caller as `RemoteError` text, as the
  GOAL's vocabulary says.
- The `SecureManager` serializer guard.
- The cleartext client building its own socket.

## Findings

### [HIGH/CONFIRMED] F1 — A server still has a way to run code in a client through the transport
- **Where:** `src/hypershell/core/queue.py` (`QueueClient.connect`, how the client's queue proxies
  are built).
- **Failure scenario:** a server that speaks the new codec correctly, with no pickle involved, can
  still cause code to run in a connecting client during `connect()`. The client→server direction is
  not affected. This adds nothing beyond develop and 2.9.0a9, where pickle already allowed it. But it
  contradicts the GOAL's stated outcome, which the 2.10.0 signing and sandbox work build on, and the
  new `docs/security.rst` sentence that neither end can make the other run code through the
  transport.
- **Evidence:** reproduced against a real `QueueClient.connect()` with a hand-built codec-speaking
  server. The details are kept in the private lane.
- **Touches:** the GOAL's stated outcome, R2 in effect, R11.
- **Ruling (maintainer, 2026-11-23):** fix in this cycle. The client builds its proxies from its own
  method lists and refuses anything else its server sends. A regression test with a codec-speaking
  server is required.

## Absurd-input notes (not findings)

- An oversized declared frame length, or a frame split across reads, goes to the unchanged stock
  framing. Frame-size caps are a GOAL non-goal.
- A `#PROXY`-shaped value sent to the server gets a `#TRACEBACK` reply, and the server keeps
  serving.
- Integers past CPython's int→str digit limit fail to encode; no real message carries one.

## Human-gate triggers

- **F1:** CONFIRMED, in `core/queue.py`, and a security property. The maintainer signed off on
  fixing it in this cycle (2026-11-23).

## Review cycle 2 — changes-requested (2026-11-23)

- **Reviewed commit:** 16af7d49 · **Mode:** a fresh blind pass over the full diff, excluding `spec/`,
  not scoped to the remediation · xhigh effort.
- **Verification:**
  - More than 60 hostile server replies and more than 50 hostile client frames, speaking the real
    codec and framing and authenticating with the key, TLS and plain. They were tried against
    `hs client`, `hs submit -q`, `hs relay` upstream and the in-process `QueueServer`, on 3.11, 3.12
    and 3.14. None ran code in any HyperShell role, and pickle frames of protocols 0–5 were refused
    in both directions.
  - Live against 2.9.0a9, both directions, TLS on and off: rc 5, one CRITICAL line, no retries.
  - Full suite serial: 4297 passed.
  - Pins: 19 of 19 on 3.11.18, 3.12.13, 3.13.16 and 3.14.6.
  - `bench_queue.py gate` against 2.9.0a9, bundle size 1: within 2%.
  - A 3000-task non-ASCII bundle round-tripped.
  - Docs build `-W` passes.
- **Cycle-1 F1:** the client builds its proxies from its own method lists and refuses foreign
  method names, and both hold. The third part of the fix, refusing proxy replies the client never
  asked for, is incomplete (F2).

### [CRITICAL/CONFIRMED] F2 — One reply shape gets past the refusal of unrequested proxies
- **Where:** `src/hypershell/core/queue.py:173` (`_EnvelopeCodec.recv`).
- **Failure scenario:** a server can answer a queue or dispatcher call with an unrequested proxy
  reply in a shape the refusal does not check. The client then builds and returns a proxy for a
  server-chosen object, from the client's own method names, so no code runs. It contradicts the
  invariant this branch adds (invariants.md §9, AGENTS.md): "a `#PROXY` reply on a client's
  connection is refused". By impact alone it would be MEDIUM; under the rubric, breaking an
  invariant grades CRITICAL.
- **Evidence:** reproduced against `QueueClient` with a codec-speaking server, TLS and plain.
  The details are kept in the private lane. The existing test covers one shape only.

### [LOW/CONFIRMED] F3 — A test constant is a working payload rather than an inert value
- **Where:** `tests/test_queue_codec.py:491` (`NOT_AN_IDENTIFIER`).
- **Failure scenario:** the public-text rule says tests state outcomes, never how an attack works.
  The refusal test only needs a name that is not one of the client's methods.

### [LOW/CONFIRMED] F4 — The docs don't say that the protection covers HyperShell's own clients, not `SecureManager` used directly
- **Where:** `docs/security.rst` (`versionchanged`), and the `SecureManager` docstring.
- **Failure scenario:** outside code that uses the public `SecureManager` as a generic client,
  with the standard library's default proxy, does not get the client-side refusals that
  `QueueClient` has. The R-IDs concern HyperShell's roles, and those are met. But a Python API
  reader could take the docs as covering that use too.

### Absurd-input notes (not findings)
- These are reachable only by a key-holding hostile peer, and stock multiprocessing behaves the
  same way:
  - an argument-less `RemoteError` whose `str()` raises on the receiver;
  - a non-string `#TRACEBACK` body;
  - a `create` reply that is not a pair raising a generic error rather than `QueueVersionError`.
- Pre-existing and unchanged: a key holder can still send the manager's own requests, such as
  `shutdown` and `debug_info`. The refusal WARNING has no rate limit; transport DoS is a non-goal.

### Ruling (maintainer, 2026-11-23)
Fix F2, F3 and F4 in cycle 3, the last allowed:
- **F2:** refuse an unrequested proxy reply of either shape, with tests for both.
- **F3:** replace the constant with an inert invalid name.
- **F4:** one docs sentence: `SecureManager` used directly should register explicit proxy types.
  Deeper `SecureManager` hardening joins the 2.10.0 `retire-forked-manager` seed.

If cycle 3's review is not clean, stop and escalate.

### Human-gate triggers
- **F2:** CONFIRMED in `core/queue.py`. The maintainer signed off on fixing it in cycle 3
  (2026-11-23).

## Review cycle 3 — changes-requested (2026-11-23)

- **Reviewed commit:** 0d905e1e · **Mode:** a fresh blind pass over the full diff, excluding `spec/`
  · xhigh effort. This is the last cycle the loop bound allows.
- **Verification:**
  - 275 hostile-server scenarios against a real `QueueClient`, TLS and plain: every reply kind, at
    every stage, in tuple, list, dict and string shapes. About 60 odd manager requests per mode were
    sent to the server. Nothing was unpickled.
  - Live against 2.9.0a9, 12 runs, both directions: exit 5, one CRITICAL line, no redial.
  - Pins: 19 of 19 on 3.11.18, 3.12.13, 3.13.16 and 3.14.6.
  - Core suites: 363 passed. Wide suites: 541 passed.
  - Reduced bench, two rows against a9: −0.7% and −2.0%.
  - Docs build `-W` passes.
- **Cycle-2 F2–F4 are fixed:** unrequested proxy replies are refused in both shapes, the test value
  is inert, and the docs scope the client-side protection.

### [CRITICAL/CONFIRMED] F5 — With Python's optimisation flag, a server accepts requests outside its public surface
- **Where:** `src/hypershell/core/queue.py` (`_InProcessServer`'s request path).
- **Failure scenario:** the server relies on the standard library's check of a first request's
  name, and Python's `-O` / `PYTHONOPTIMIZE` removes that check. On a server run that way, a peer
  holding the key can send requests outside the public surface. On a cleartext server, one of these
  makes a later connection's bytes be decoded the old way. That contradicts "never unpickles what its
  peer sends" (R1, R6, invariants.md §9, docs).
- **Scope:** needs the key and `-O` on the server process. Without `-O` the check holds. This is not
  a regression against develop.
- **Evidence:** reproduced with an in-process server and with `hs server --no-tls` under
  `PYTHONOPTIMIZE=1`. The details are kept in the private lane.

### [LOW/CONFIRMED] F6 — A `RemoteError` with no text crashes the receiver's error report
- **Where:** `src/hypershell/core/codec.py:225-234` (decoding), and `core/reconnect.py:473`.
- **Failure scenario:** a hostile server's `('#ERROR', RemoteError())` at connect kills `hs relay`'s
  upstream link thread, and the relay stays up with no upstream. `hs client` and `hs submit -q` exit
  1 with a raw traceback instead of a fatal version error. No HyperShell peer sends this.

### Notes (not findings)
- A dict-shaped proxy reply cannot build a proxy, and ends the client with exit 5 and an
  AttributeError.
- Other in-vocabulary garbage ends the client with TypeError or ValueError, with no hang.
- Pre-existing: a key holder can still send `shutdown`, `debug_info` and `decref`.

### Loop bound
Cycle 3 of 3 did not converge. Per the rubric, this STOPs and escalates to the maintainer.

## Review cycle 4 — changes-requested (2026-11-23)

- **Reviewed commit:** 01badfd2 · **Mode:** scoped, by the maintainer's authorization: verify
  cycle 3's F5 and F6, then a regression sweep. Not an open-ended pass · xhigh effort.
- **F5 is fixed for every HyperShell role.** The roles covered: the in-process `QueueServer`, the
  forked `SecureManager.start()`, `hs server`, `hs cluster` and the `hs relay` site server, each over
  TLS and plain, under both `-O` and `PYTHONOPTIMIZE=1` (20 runs).
  - Every run sent 71 first requests, plus later requests of an established connection. Every
    non-public or odd first request closed unanswered.
  - No server process unpickled anything, every server kept serving, and each CLI server logged one
    WARNING per refused connection.
- **F6 is fixed.** `hs client`, `hs submit -q` and `hs relay`, TLS and plain, across 24 runs: exit 5
  with one CRITICAL `QueueVersionError` line and no traceback. The relay no longer stays up with a
  dead link.
- **Sweep:**
  - Queue, codec, TLS, dispatch and cluster-launch suites: 635 passed. Relay suites: 514 passed.
  - Pins: 20 of 20 on 3.11 to 3.14.
  - `old_peer.sh`: holds in both directions.
  - The cycle-2 hostile probes still hold for every `QueueClient` case.
  - Docs build `-W` passes. Public text states outcomes only.

### [MEDIUM/CONFIRMED] F7 — One public way of building a server doesn't get the F5 check
- **Where:** `src/hypershell/core/queue.py` (`SecureManager`). It sets its server class, but the
  standard library's `get_server()` builds the stock server class regardless.
- **Failure scenario:** Python API code that calls `SecureManager(...).get_server()` and runs it
  under `-O` behaves as cycle 3's F5 described. No `hs` command builds a server this way. It needs
  the key and `-O`, and it is not a regression against develop. It contradicts the new docstrings
  and AGENTS.md ("the server class of every queue manager").
- **Evidence:** reproduced under `-O` and `PYTHONOPTIMIZE=1`, TLS and plain. The details are kept in
  the private lane.

## Review cycle 5 — approved (2026-11-24)

- **Reviewed commit:** 040ccdf0 · **Mode:** a targeted check, by the maintainer's ruling
  (2026-11-23), in place of another open-ended review. Remediation of F7 only.
- **F7 is fixed.** `SecureManager.get_server()` now builds the manager's own server class, keeping
  the stock method's state checks.
  - The reviewer's repro was reproduced on all four runs before the fix: `-O` and
    `PYTHONOPTIMIZE=1`, TLS and plain.
  - After the fix, all four runs: the server is the manager's own class, every non-public first
    request is closed unanswered, a stock client is refused at the named address, and nothing is
    unpickled.
- **New tests:** `test_get_server_builds_the_manager_server` and
  `test_get_server_refuses_non_public_requests_under_optimisation` (TLS and plain). Both were red
  on 968f032c and are green now.
- **New pin:** `TestPrivateInternals::test_get_server_builds_a_stock_server`. It is shown to fail on
  three mutations of the stock method.
- **Other construction sites:** the standard library on 3.11 to 3.14 has no other server
  construction a `SecureManager` can reach.
- **Results:**
  - Queue, codec, TLS and dispatch suites: 427 passed.
  - Pins: 21 of 21 on 3.11 to 3.14.
  - Docs build `-W` passes.
  - Full default suite, serial: 4331 passed.
- **Findings:** none.

## Closing note (2026-11-24)

At the maintainer's ruling, the branch's 20 local commits are squashed into one signed commit
before the push. That commit's tree is identical to 040ccdf0, apart from this record and the TECH
stamp. The SHAs cited above are this cycle's local history.
