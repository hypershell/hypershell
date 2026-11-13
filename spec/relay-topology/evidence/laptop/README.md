# Laptop drive evidence — relay-topology P4, refreshed at review cycle 1

Eight runs of `spec/relay-topology/drive/topology_drive.sh`, one per mode, each in its own throwaway
site (`.agents/factory/bin/temp_site.sh`), with `TD_EVIDENCE` pointing here. Every file was
regenerated on 2026-11-13 from `b8bb537` with the review cycle 1 remediation (RV1–RV3), which the
remediation commit carries; `stale` and `bounce` are new with it. Host: a laptop (macOS, Apple
silicon), loopback only, inside the build sandbox. Host, user, paths and the run key are replaced in
every file.

Each run: one `hs server --autoscaling=dynamic -P 1` with no launcher (`server.evict` 10 s) and two
relays as separate processes, A (`cpu`, `highmem`) and B (`cpu`, `gpu`), heartrate 1 s, site-client
idle timeout 2 s, each launching through `topology_shim.sh`. Each file holds the launch log (epoch,
launcher label, pid, `{zone}` word, `HYPERSHELL_ZONE`, `--zone`), the server's registration,
version, duplicate, departure and eviction lines, each relay's topology reads and applications and
its idle exits, and the checks.

| Mode | Result | What it shows |
|------|--------|---------------|
| `spread` | PASS | `cpu` (40 tasks) at A and B, 2 each (their max); `highmem` only at A; `gpu` only at B; word = env = `--zone` on every launch |
| `fill` | PASS | `sites = B`: B launched its max (2) of `cpu` before A's first `cpu` launch, then A filled |
| `floor` | PASS | B's `gpu` (min 1, group 1) launched 13 s before group 0's last completion; A's `highmem` (min 0, group 1) only after it; B's floor held after the work |
| `idle` | PASS | every client left by idle exit (6 of 6 seen by their relays), nothing launched again, both relays stayed registered until drained, then drained with status 0 |
| `kill` | PASS | `fill`, `sites = B`: B killed (SIGKILL, then its clients) after 10 tasks; evicted 10 s later with its orphans reverted; A launched no `cpu` client before the eviction and 9 after (peak 3, its max); all 40 tasks completed once |
| `lower` | PASS | A2 (`site = A`, `cpu.max` 1) joined while A ran 3 clients (`cpu.max` 3): the server warned naming both relays, A launched nothing after, all 3 of A's clients ended by idle exit, A2 never ran more than 1 and launched 1 s after the warning (A's clients no longer count against the site) |
| `stale` | PASS | server `-P 20` against a relay checking every second, `cpu` `init` 2, `max` 2, no work: A launched exactly 2, its clients left, the server checked again, and no version was applied again |
| `bounce` | PASS | A (`cpu.max` 2, 40 tasks of 2 s, eviction 30 s) killed with SIGKILL once it ran 2 clients, its clients killed, and restarted at once as A2 at site A: the server warned naming both, A2 launched 2 s after its start, 28 s before A's eviction (whose orphans were reverted), and all 40 tasks completed once |

In every mode: every task completed and none failed, no site-zone ran more clients than its
advertised max, and no relay terminated a site client. No run logged a version applied again.

**Red on the reviewed head.** The three checks the remediation added were first run against the
reviewed head's source (`b8bb537`, the drive script alone changed), each failing as the review
reported: `stale` made 12 launches against `init` 2, with 5 versions applied again; in `bounce` A2's
first launch came 32 s after its start, 2 s after A's eviction; in `lower` A2's first launch came
21 s after the warning, once A's clients had left. Those runs are not kept here.
