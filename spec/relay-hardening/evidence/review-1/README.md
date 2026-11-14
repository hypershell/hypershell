# Laptop drive evidence — relay-hardening, review cycle 1

The P6 verify re-run on the remediation build (on top of `fcea305`), 2026-11-14, on a laptop (macOS,
Apple silicon), loopback only, each drive in its own throwaway site
(`.agents/factory/bin/temp_site.sh`), with `DRIVE_EVIDENCE` and `TD_EVIDENCE` pointing here. Host,
user, paths and the run key are replaced in every file. The format of each file is that of
`../laptop/README.md` (hardening modes) and of the earlier cycles' drives (the others).

| Drive | Mode | Result | Note |
|-------|------|--------|------|
| `hardening_drive.sh` | `restart` | PASS | relay never exited 5; every task completed; exit 0 |
| `hardening_drive.sh` | `tls-on` | PASS | exit 5 about 1 s after start (0.08 s first to last relay line), one CRITICAL line naming `[relay.upstream.tls] enabled`; no launch; upstream still running |
| `hardening_drive.sh` | `tls-off` | PASS | exit 5 about 1 s after start (0.01 s first to last relay line), one CRITICAL line naming `--no-tls`; no launch; upstream still running |
| `hardening_drive.sh` | `zones` | PASS | the gpu zone (no site client, no topology) asked once at the shutdown and held a staged task; 21 of 21 ran once; both exit 0 |
| `hardening_drive.sh` | `zones-nodb` | PASS | 8 of 8 ran in the default zone; gpu ended with the default zone's shutdown; both exit 0 |
| `hardening_drive.sh` | `pair-relay` | PASS | `2ffb5cc` relay, this server: 40 of 40 once; both exit 0 |
| `hardening_drive.sh` | `pair-server` | PASS | `2ffb5cc` server, this relay: 40 of 40 once; both exit 0 |
| `hardening_drive.sh` | `pair-relay`, `DRIVE_BASE_SRC` set (`base-src/`) | PASS | the earlier build from an export outside the site; the judge checks each role against the recorded `base_src` |
| `retire_drive.sh` | `retire`, `idle` | PASS | as recorded by relay-retire |
| `topology_drive.sh` | `spread`, `fill`, `kill` | PASS | as recorded by relay-topology |

`relay_smoke.sh` PASS (5 records). Full SQLite suite in the same run: 2116 passed, 101 skipped.
