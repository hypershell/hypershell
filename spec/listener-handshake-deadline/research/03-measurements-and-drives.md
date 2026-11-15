# 03 — Baseline measurements and the CLI drives, dry-run on develop

Host: one macOS arm64 laptop, Python 3.14.6, loopback. Code: `develop` `6da633d`.

## Responsiveness on develop (in-process probe)

A TLS `QueueServer` with no other connections: connect plus first queue call 0.037 s. With 8 open TCP
connections that send nothing: the client did **not** complete within 5 s; one of the 8 was still open
after a further 10 s (no deadline); once the 8 were closed by the peer, the waiting client completed
15.1 s after it started. Red for R1 and R2, as the GOAL says.

## R7 baseline (`research/latency.py`)

Median over 100 sequential connects (after 5 warm-ups), each from before `QueueClient.connect()` to
after `completed.qsize()`; then 64 clients started at once:

| run | median | p10 | p90 | burst |
|-----|--------|-----|-----|-------|
| develop 1 | 34.28 ms | 33.20 | 35.90 | 64/64 in 1.76 s |
| develop 2 | 34.08 ms | 33.44 | 35.21 | 64/64 in 1.72 s |
| develop 3 | 34.24 ms | 33.66 | 35.64 | 64/64 in 1.71 s |

R7's tolerance is the larger of 10 % or 5 ms: 5 ms here, so the branch median must stay at or below
about 39.2 ms on this host. The scratch prototype of the design (research/01), interleaved with
develop: medians 33.89 / 33.94 / 33.86 ms against develop's 33.99 / 34.10 / 34.17 ms, and the burst
completed in 0.53 s instead of 1.72 s (handshakes now run concurrently). `latency.py compare` runs the
same alternation and judges R7; its dry run (develop against develop) passed.

## CLI drives (`research/drive.py`), dry-run on develop

Each runs in `temp_site.sh`, holds one TCP connection that sends nothing to the server's port, and
bounds the work at 25 s (below the 30 s default deadline, so a pass shows R1, not R2).

| drive | develop result |
|-------|----------------|
| `server --no-hold` (2.9 client) | pass, 4 tasks in ~2.5 s |
| `server` | **fail**: client still waiting at 25 s |
| `server --handshake 2` | **fail**: held connection never closed |
| `cluster --no-hold` | pass, 5.7 s |
| `cluster` | **fail**: not finished at 25 s |
| `relay --no-hold` | pass, 7.0 s |
| `relay` | **fail**: upstream not finished at 25 s |
| `refuse` | **fail**: no command names `server.tls.handshake` (develop ignores the key) |
| `server --no-hold --client <2.9.0a8>` | pass, 2.6 s |
| `server --no-hold --client <2.8.7> --expect-too-old` | pass: TLS and authentication complete, then the client is refused as too old (exit 5) |

Drive mechanics settled during the dry runs:
- The relay launches site clients with `-H <this host's name>`, which does not resolve in the build
  sandbox. The drive's launcher (`delay.py`, written into the site) waits 3 s, rewrites the host to
  `127.0.0.1` and execs the client, which also gives the drive time to hold its connection first.
- `hs cluster` takes `--delay-start 3` for the same reason.
- A relay that cannot refuse (develop) never exits; the `refuse` scenario stops each such process
  before the next run and gives each relay its own database file (a second relay refuses to start on
  a locked one).

## R8 finding: a 2.8.x client cannot run tasks against any 2.9 server

Every 2.9 server, `develop` included, refuses a client without the work-request protocol
(`core/queue.py` `_versioned_provider` raises `ClientTooOld` for `_get_scheduled()` with no argument;
the client exits 5 with "client too old"). That is by design since the protocol landed, not part of
this fix. A 2.8.7 client does connect, complete TLS and authenticate (the refusal comes from the
registry callable, which runs only after both), so the testable form of R8 for 2.8.7 is "behaves
exactly as on develop: connects, authenticates, refused as too old". See PLAN §5 — this needs a GOAL
wording decision.

Old clients for the drive: `uv venv --python 3.14 <dir> && VIRTUAL_ENV=<dir> uv pip install
hypershell==2.9.0a8` (and `==2.8.7`), from PyPI.
