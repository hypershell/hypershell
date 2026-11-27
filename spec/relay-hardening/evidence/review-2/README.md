# Laptop drive evidence — relay-hardening, review cycle 2

The hardening drives re-run on the review-cycle-2 remediation build (on top of `8ba6d96`),
2026-11-14, on a laptop (macOS, Apple silicon), loopback only, each drive in its own throwaway site
(`.agents/factory/bin/temp_site.sh`), with `DRIVE_EVIDENCE` pointing here. Host, user, paths and the
run key are replaced in every file. The format of each file is that of `../laptop/README.md`.

| Drive | Mode | Result | Note |
|-------|------|--------|------|
| `hardening_drive.sh` | `tls-on` | PASS | exit 5 about 1 s after start, one CRITICAL line naming `[relay.upstream.tls] enabled`; no launch; upstream still running. The mismatch is now taken from the failed handshake itself |
| `hardening_drive.sh` | `tls-off` | PASS | exit 5 about 1 s after start, one CRITICAL line naming `--no-tls`; no launch; upstream still running |
| `hardening_drive.sh` | `zones` | PASS | the gpu zone (no site client, no topology) asked once at the shutdown and held a staged task until its client came back; 21 of 21 ran once; both exit 0 |
| `hardening_drive.sh` | `zones-nodb` | PASS | 8 of 8 ran in the default zone; gpu ended with the default zone's shutdown; both exit 0 |
| `hardening_drive.sh` | `restart` | PASS | relay never exited 5; every task completed; exit 0 |
| `hardening_drive.sh` | `pair-relay` | PASS | `2ffb5cc` relay, this server: 40 of 40 once; both exit 0 |
| `hardening_drive.sh` | `pair-server` | PASS | `2ffb5cc` server, this relay: 40 of 40 once; both exit 0 |

The idle-zone hand-back (RV5) needs a topology change landing between a zone's ask and its first
site client, which no laptop drive mode stages; it is covered at the puller with the real finishing
`Dispatcher` (`tests/test_relay_connector.py`) and on a real queue server for the stop-time requeue
(`tests/test_relay_retire.py`).
