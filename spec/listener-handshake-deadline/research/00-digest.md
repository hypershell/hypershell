# 00 — Digest: decisions for listener-handshake-deadline

Fan-out note: the change touches `core/queue.py` and `core/tls.py` (high blast radius), so `hs-plan`
Step 3 calls for research despite `appetite: small`. It was done sequentially by the planner, with
probes, in three briefs: [`01-accept-path.md`](01-accept-path.md),
[`02-deadline-and-config.md`](02-deadline-and-config.md),
[`03-measurements-and-drives.md`](03-measurements-and-drives.md). Harness scripts:
[`drive.py`](drive.py) (CLI drives) and [`latency.py`](latency.py) (R7 measurement).

1. **Handshake on the handler thread, accept loop does only the TCP accept.** New
   `SecureListener.accept_pending()` / `wrap_pending()`; `_InProcessServer.serve` uses them for TLS and
   hands the raw socket to `handle_pending` on the per-connection `_ServerThread`. `accept()` is kept
   as is for the forked path and its direct tests. (01)
2. **Deadline = socket timeout set to what is left of `accepted + handshake`.** One `do_handshake()`
   under that timeout is an absolute deadline on 3.11–3.14; `TimeoutError` marks a deadline close. (02)
3. **`_live` holds the pending socket** (raw, then `SSLSocket`, then `SecureConnection`), swapped under
   `_lock` with a `_halted` check each time; `_cut` learns plain `socket.socket`. Stop still cuts
   everything, including handshakes in progress. (01)
4. **Admission before the thread**: `_admits(raw)` right after the TCP accept, refused at once. (01)
5. **Key carried raw on `TLSConfig.handshake`, parsed only by servers** through one helper,
   `core/tls.py` `handshake_seconds()`; refusal in `_InProcessServer.__init__` (`QueueServerError`,
   before binding) and early in `hs server`, `hs cluster`, `hs relay` (`ArgumentError`). Clients never
   parse it. Default `30` in `core/config.py`. (02)
6. **Logging** on the handler thread: one DEBUG per deadline close (peer, deadline); one WARNING naming
   the deadline and `server.tls.handshake` for the first close and at most one per
   `REFUSAL_WARNING_INTERVAL` after it, from its own timestamp under a lock. Other failures keep
   `develop`'s DEBUG text `Queue server dropped a connection: {error!r}`. Nothing the peer sent is
   formatted into a deadline line. (01, 02)
7. **The client-side handshake mark stays** (a forked manager's accept loop still waits on it); only
   the wording that says an in-process server runs the handshake inline changes. (01)
8. **R8 for 2.8.7 is "as on develop"**: connects, authenticates, refused as too old. Needs the
   foreman's GOAL wording call (PLAN §5). (03)
9. **R7 is judged on the same host, interleaved** (`latency.py compare`), against a `src/` exported
   from the base commit with `git archive`, put first on `PYTHONPATH` (it shadows the editable
   install; checked). (03)
