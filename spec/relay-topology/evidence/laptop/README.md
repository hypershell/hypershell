# Laptop drive evidence — relay-topology P4, refreshed at review cycle 2

Ten runs of `spec/relay-topology/drive/topology_drive.sh`, one per mode, each in its own throwaway
site (`.agents/factory/bin/temp_site.sh`), with `TD_EVIDENCE` pointing here. Every file was
regenerated on 2026-11-13 from `37e0a60` with the review cycle 2 remediation (RV4, the reconciler
restated as one rule), which the remediation commit carries; `stall3` and `stall-min2` are new with
it, as `stale` and `bounce` were with review cycle 1. Host: a laptop (macOS, Apple
silicon), loopback only, inside the build sandbox. Host, user, paths and the run key are replaced in
every file.

Each run: one `hs server --autoscaling=dynamic -P 1` with no launcher (`server.evict` 10 s) and two
relays as separate processes, A (`cpu`, `highmem`) and B (`cpu`, `gpu`), heartrate 1 s, site-client
idle timeout 2 s, each launching through `topology_shim.sh`. The `lower`, `stale`, `bounce` and stall
modes run relay A alone (with A2 where named); the stall modes check every 4 s, give site clients 30
idle seconds and evict a killed site client at A's site server after 20 s. Each file holds the launch log (epoch,
launcher label, pid, `{zone}` word, `HYPERSHELL_ZONE`, `--zone`), the server's registration,
version, duplicate, departure and eviction lines, each relay's topology reads and applications and
its idle exits, and the checks.

| Mode | Result | What it shows |
|------|--------|---------------|
| `spread` | PASS | `cpu` (40 tasks) at A and B, 2 each (their max); `highmem` only at A; `gpu` only at B; word = env = `--zone` on every launch |
| `fill` | PASS | `sites = B`: B launched its max (2) of `cpu` before A's first `cpu` launch, then A filled |
| `floor` | PASS | B's `gpu` (min 1, group 1) launched 14 s before group 0's last completion; A's `highmem` (min 0, group 1) only after it; B's floor held after the work |
| `idle` | PASS | every client left by idle exit (7 of 7 seen by their relays), nothing launched again, both relays stayed registered until drained, then drained with status 0 |
| `kill` | PASS | `fill`, `sites = B`: B killed (SIGKILL, then its clients) after 10 tasks; evicted 9 s later with its orphans reverted; A launched no `cpu` client before the eviction and 10 after (peak 3, its max); all 40 tasks completed once |
| `lower` | PASS | A2 (`site = A`, `cpu.max` 1) joined while A ran 3 clients (`cpu.max` 3): the server warned naming both relays, A launched nothing after, all 3 of A's clients ended by idle exit, A2 never ran more than 1 and launched 2 s after the warning (A's clients no longer count against the site) |
| `stale` | PASS | server `-P 20` against a relay checking every second, `cpu` `init` 2, `max` 2, no work: A launched exactly 2, its clients left, the server checked again, and no version was applied again |
| `bounce` | PASS | A (`cpu.max` 2, 40 tasks of 2 s, eviction 30 s) killed with SIGKILL once it ran 2 clients, its clients killed, and restarted at once as A2 at site A: the server warned naming both, A2 launched 2 s after its start, 28 s before A's eviction (whose orphans were reverted), and all 40 tasks completed once |
| `stall3` | PASS | server `-P 4`, A alone with `cpu.max` 3, 90 tasks of 2 s: one of A's clients was killed as the server published target 3, seen 2; A read that plan with 1 running and launched 1, the server's next plans were the same entry, and once it had planned twice on the 2 running A applied it again and ran 3, 9 s after the kill (4 launches in all) |
| `stall-min2` | PASS | as `stall3` with `cpu` `min = init = max = 3` and 60 tasks: one client killed once the server saw 3, a second as it published target 3, seen 2; A ran 3 again 10 s after the second kill (5 launches in all) |

In every mode: every task completed and none failed, no site-zone ran more clients than its
advertised max, and no relay terminated a site client. Only the two stall runs logged a plan applied
again, once each, which is the case they exist for.

**Red on the reviewed head.** The three checks the remediation added were first run against the
reviewed head's source (`b8bb537`, the drive script alone changed), each failing as the review
reported: `stale` made 12 launches against `init` 2, with 5 versions applied again; in `bounce` A2's
first launch came 32 s after its start, 2 s after A's eviction; in `lower` A2's first launch came
21 s after the warning, once A's clients had left. Those runs are not kept here.

**Red on the review cycle 2 head.** `stall3` and `stall-min2` were first run with the reviewed
head's `relay/site.py` (`37e0a60`) in the worktree, the drive script alone changed. Both reproduced
the race and failed the one check RV4 is about: A never ran 3 clients again after the kill (in
`stall3` its peak stayed 2 for the rest of the work; in `stall-min2` it sat at 2 under a held floor
of 3), with no plan applied again. Those runs are not kept here.
