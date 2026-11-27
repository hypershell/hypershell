# Research 04 — R4: TLS on/off mismatch fails fast in both directions

Probed on the laptop (macOS, Python 3.15.2, OpenSSL 3.5.9) against `develop` 2ffb5cc code, real
`hs server --forever` in throwaway sites, keys by environment, loopback ports. Scratch scripts were
kept outside the repo; nothing in `src/` was edited (the candidate ran as a monkeypatch).

## Verdict

Both directions can meet R4 with **no wire change, no change to `core/queue.py` or `core/tls.py`,
and no change to a plain client's or server's TLS behaviour**. Each direction has a positive,
deterministic proof that arrives in milliseconds, so neither has to be told apart from a stall by
waiting. **No return to the gate is needed for R4 itself.** One fact does go back to the gate
(see the last section).

## How it works today (line refs at 2ffb5cc)

- The link counts every `ssl.SSLError` as transport (`core/reconnect.py:89` FATAL, `:92` TRANSPORT,
  `attempt` `:416-448`, fatal branch `:421-425`). `dial` (`:450-477`) bounds each attempt by the
  deadline and abandons a connect thread that overruns. `self.connected` (`:243`, set at `:439`) is
  false until the first success: it is the "first connection" state R4 needs.
- TLS client: `secure_client` (`core/queue.py:296-343`) does the TLS handshake, then
  `answer_challenge`. Plain client: `_plain_client` (`:357-368`) waits for the server's challenge.
- A TLS server waits for a ClientHello before it sends anything. A plain peer and a TLS server
  therefore each wait for the other: the client for `#CHALLENGE#`, the server for a ClientHello,
  and nothing is ever sent.
- Relay order: `link.start()` `relay/__init__.py:583`, `wait_link` `:584`/`:647` (raises the link's
  failure), and only then `heartbeat.machine.fetch()` `:606`, `cluster.start()` `:607`, fleets
  `:611`. No site client can launch before the first connection succeeds (confirmed: 0 launches in
  every mismatch probe).
- Exit: `AuthenticationError` maps to `handle_exception`, `exit_status.runtime_error` (5)
  (`relay/__init__.py:1099`). A `TLSError` is not mapped, so it falls to the shared `Exception`
  handler, `write_traceback`: still 5, plus a "traceback written" line.

## Probes (time to failure)

| Probe | TLS client → plain server | Plain client → TLS server |
|---|---|---|
| `QueueClient.connect` (in process) | `ssl.SSLError` WRONG_VERSION_NUMBER, **0.002 s** | **no reply** (8 s cap); each side waits for the other |
| `hs client` | exit 5, 0.47 s, via `write_traceback` | **hangs**: no output, killed at the 25 s cap |
| `hs relay` (deadline 5) | "Cannot reach … SSLError … trying again" for ever, 0 launches | `TimeoutError` every 5 s for ever, 0 launches |

Detectors (raw sockets, 3 s timeout, against in-process `QueueServer`s):

| Target | `speaks_tls` (TLS handshake, CERT_NONE, sends nothing after) | `speaks_plain` (read 4-byte frame length + `#CHALLENGE#`) |
|---|---|---|
| TLS server | **True, 0.003–0.016 s** | False after the full timeout (a TLS server never sends first) |
| plain server | False, 0.001 s | **True, 0.001 s** |
| refused | `ConnectionRefusedError`, 0.0004 s | same |
| stalled listener (never accepts) | False after timeout | False after timeout |

Probe connections leave no WARNING or ERROR in the upstream server's log. A TLS probe closed early
logs one DEBUG "Queue server dropped a connection: SSLEOFError". A probe on a plain server fails
inside the stock `_handle_request` (bad length or EOF), which is logged only through `util.info`,
so it is silent.

**End to end with the candidate monkeypatched into `UpstreamLink.dial`** (deadline 5):
`--no-tls` relay → TLS server: exit 5 in 0.41 s wall (mostly process start), 0 launches. TLS relay → plain server:
exit 5 in 0.45 s, 0 launches. Matching TLS/TLS and plain/plain: connected, relay-local server
ready, 1 site client launched. Refused at start (both modes): "Cannot reach … ConnectionRefusedError
– trying again", still retrying at the 8 s cap.

## Distinguishability

- **TLS relay → plain upstream.** A plain upstream sends its challenge frame as soon as it accepts. Whatever
  OpenSSL calls the resulting `SSLError` (WRONG_VERSION_NUMBER here; Linux and OpenSSL 3.x may say
  "record layer failure" or "packet length too long"), a confirming `speaks_plain` that reads
  `#CHALLENGE#` is proof that does not depend on the message text. Refused, no route or stalled
  peers never yield an `SSLError` with a challenge behind it. A wrong key is an
  `AuthenticationError` after a good transport, so it is already distinct. `SSLEOFError` or a reset
  (an upstream TLS server that cannot build its context, or one that is restarting) fails the
  confirmation and stays transport.
- **`--no-tls` relay → TLS upstream.** The plain dial alone *cannot* tell this apart from a stall,
  because neither side sends anything. A `speaks_tls` probe made **before** the plain dial can:
  only a TLS server completes a handshake. Plain, refused and stalled peers all give False or raise,
  and the dial then goes on as today. The one blind spot is a TLS upstream that does not complete
  the probe's handshake within the deadline. It cannot be told from a stalled peer, so retrying it
  is correct.

## Recommendation (one per direction)

Both run only while `not self.connected`, inside the existing `dial` connect thread, so the whole
attempt, probe included, stays within one deadline (each probe's socket timeout is the remainder).
After a first success nothing changes: a later `SSLError` or timeout retries as today (R4's last
sentence).

- **`--no-tls` relay:** before `client.connect()`, run `speaks_tls(address, remaining)`. If it
  returns True, raise a fatal mismatch error. Otherwise continue unchanged. This costs one extra TCP
  connection of about 1 ms per first-connection attempt against a plain upstream. The mismatched
  plain connection is never opened, and the probe closes its own socket.
- **TLS relay:** on any `ssl.SSLError` from `client.connect()` other than
  `SSLCertVerificationError` (already fatal), run `speaks_plain(address, remaining)`. If it returns
  True, raise the fatal mismatch error from the original. Otherwise re-raise the original
  (transport). This costs nothing on the happy path.
- **Placement:** the probes go in a relay-side helper (`relay/upstream.py`, next to `upstream_config`,
  which knows host, port and TLS), handed to `UpstreamLink` as an optional hook (for example
  `UpstreamLink(..., first_check=…)`), so the link stays generic and `core/queue.py`/`core/tls.py`
  are untouched. The error class should subclass `TLSError` (in `reconnect.py` or `relay/`), which
  makes it fatal through the existing `FATAL_ERRORS` without editing them. Map it in
  `RelayApp.exceptions` (`relay/__init__.py:1094-1100`) to `handle_exception`,
  `exit_status.runtime_error` (5, as for a wrong key), so it prints one CRITICAL line.
- **Probe details:** dial IPv4 (`socket.AF_INET`) exactly as both queue clients do (`queue.py:311`);
  `create_connection` could follow `localhost` to `::1`. The TLS probe uses a throwaway
  CERT_NONE context, sends no data and no key, and trusts nothing; it is detection only, and the
  real dial verifies as before. Match the challenge as a 4-byte big-endian length followed by
  `#CHALLENGE#`, the same prefix from Python 3.11 through 3.15 (3.12+ follows it with
  `{sha256}`). Optionally cap `speaks_plain` (say at 5 s) so a non-HyperShell service on the port
  does not use the whole deadline.
- **Wording** (as probed): "Upstream server H:P uses TLS, and this relay has TLS off (`--no-tls` or
  `[relay.upstream.tls] enabled = false`): the relay and its upstream server must agree on TLS".
  The other direction: "… does not use TLS, and this relay has TLS on (`[relay.upstream.tls]
  enabled`, the default) … must agree on TLS (`--no-tls` on both, or on neither)".

## R9 test shape

1. **Link level** (`tests/test_relay_link.py`, real in-process `QueueServer`, TLS on or off, free
   port, `deadline=5`). Both directions: `link.wait_up` raises the mismatch error, elapsed < 1 s, and
   the text names the setting and "must agree". On 2ffb5cc the TLS direction never fails, and the
   plain direction only times out.
   - *Connected once, then mismatched:* serve matching, connect, stop, then serve mismatched on the
     same port. The link goes DOWN and keeps retrying, and is not FAILED.
   - *Refused at start:* free port, not FAILED, attempts > 0. Then a matching server comes up on
     the port and the link connects.
   - On 2ffb5cc the plain → TLS test never returns on its own. Bound every wait, or serve the TLS
     upstream from a subprocess `hs server`.
2. **CLI** (`tests/test_relay_workload.py` pattern, cf. the wrong-key test at `:746` and
   `wrapped_launcher` `:611`). Run `hs server --forever` with and without `--no-tls`, key in
   `HYPERSHELL_SERVER_AUTH`, and the relay opposite, with `HYPERSHELL_RELAY_UPSTREAM_DEADLINE=5` and
   a recording launcher in a shared site, so `<auto>` cafile mirrors the server cert. Assert:
   - exit status 5, within 5 s of start;
   - the log has the setting name and "must agree";
   - no launch recorded, and no "Relay-local server ready".

   A refused-at-start relay is still running at the bound.

## Back to the gate / risks

1. **The GOAL's premise is half wrong.** "A plain `hs client` fails at once in both directions" is
   not true: `hs client --no-tls` against a TLS server **hangs indefinitely** (this is not
   platform-specific; each side waits for the other, as above). R4 is unaffected and the relay
   will do better than a client. The Problem text should be corrected and `hs-review` told.
2. Linux OpenSSL messages were not probed. The design does not depend on them, and CI will run the
   tests there.
3. The `--no-tls` relay's TLS probe adds one short-lived connection per first-connection attempt.
   That is not a wire or protocol change, and a 2.9.0a8 server stays silent about it (R10).
