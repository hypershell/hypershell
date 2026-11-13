# Laptop evidence: zone-aware autoscaling

What was run: the four modes of [`../../drive/zone_drive.sh`](../../drive/zone_drive.sh), each in a
throwaway site, from the repository root:

```sh
for mode in founding fixed dynamic restart; do
    ZD_EVIDENCE="$PWD/spec/zone-autoscale/evidence/laptop" \
        .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" "$mode"
done
```

Each mode runs one `hs cluster --restart --autoscaling=POLICY -T 2 -P 1 -I INIT -X 0 -Y MAX` on the
same machine (`INIT` is 1, and 0 for `restart`), launching its clients through
[`../../drive/zone_shim.sh`](../../drive/zone_shim.sh), which records each launch and starts the
client. No scheduler and no cluster are involved. The `restart` mode first runs a plain
`hs cluster --restart --zone gpu -N 1` over two group-1 tasks, so group 1 is the most recently
scheduled group when the autoscaling restart begins.

Host: a laptop (macOS, Apple silicon), Python 3.12, the SQLite database of the throwaway site.

Commit: the tree of `feature/zone-autoscale` at the review-cycle-1 remediation (P4, on top of the P3
remediation commit), whose drive and logs are committed together. `restart-base.txt` is the
`restart` mode run against the source reviewed in cycle 1 (`86fe372`), with the same drive
(`ZD_DEADLINE=40`): it reproduces review finding F1. The logs have this machine's host name, repository path and
site path replaced by `<host>`, `<repo>` and `<site>`.

Each log holds the launch lines (`pid`, the `{zone}` launcher word, `HYPERSHELL_ZONE`, the client's
`--zone`, and the group-0 tasks left when the launch was made), the task lines (zone, the
`HYPERSHELL_ZONE` the task saw, index), and the autoscaler's log lines (since the remediation, the
dynamic policy's per-zone `pressure` and `pause` lines too).

| Mode | Log | Result |
|------|-----|--------|
| founding | [`founding.txt`](founding.txt) | `zone_drive (founding): PASS`: one launch, default zone, no `--zone`, `{zone}` and `HYPERSHELL_ZONE` both `default`; 3 tasks; the client exited on idle. |
| fixed | [`fixed.txt`](fixed.txt) | `zone_drive (fixed): PASS`: one client each for default (no `--zone`), `cpu` and `gpu`; the `gpu` launch made at `g0=0`, after group 0's work was done; 3 tasks per zone, each seeing its own zone; every client exited on idle. |
| dynamic | [`dynamic.txt`](dynamic.txt) | `zone_drive (dynamic): PASS`: default and `gpu` (twelve one-second tasks each) grew to the max-size of 3 on their own pressure, `cpu` (three quick tasks) stayed at one; every `gpu` launch made at `g0=0`; every client exited on idle. |
| restart | [`restart.txt`](restart.txt) | `zone_drive (restart): PASS`: gpu group 1 partly run first, then cpu tasks submitted late in group 0; on restart one `cpu` client first (`g0=3`), then one `gpu` client at `g0=0`; no default-zone client; 3 cpu and 2 gpu tasks, each seeing its own zone; every client exited on idle. |
| restart, before the fix | [`restart-base.txt`](restart-base.txt) | `zone_drive (restart): FAIL`: 24 `gpu` launches in 40 s, all at `g0=3`, each reading `tasks: 2 in group 1`; no `cpu` launch; no task ran. |
