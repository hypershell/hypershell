#!/usr/bin/env python3
"""Compare drive results: RESULTS/<run_id>/server.json grouped by config; baseline label first."""
import glob, json, os, statistics, sys
results, base, new = sys.argv[1], sys.argv[2], sys.argv[3]
groups = {}
for p in glob.glob(os.path.join(results, '*', 'server.json')):
    r = json.load(open(p))
    clients = [json.load(open(c)) for c in glob.glob(os.path.join(os.path.dirname(p), 'client-*.json'))]
    r['client_rcs'] = sorted(c['rc'] for c in clients)
    key = (r['tls'], r['clients_expected'], r['threads'], r['tasks'], r['bundlesize'])
    groups.setdefault(key, {}).setdefault(r['label'], []).append(r)
ok = True
for key, by in sorted(groups.items()):
    line = [f'tls={key[0]} N={key[1]} T={key[2]} M={key[3]} b={key[4]}']
    stats = {}; all_hists = set()
    for label in (base, new):
        rs = by.get(label, [])
        if not rs:
            line.append(f'{label}: (none)'); continue
        tps = [r['p10_p90_tasks_per_s'] for r in rs]
        stats[label] = (statistics.median(tps), max(tps) - min(tps))
        hists = {json.dumps(r['exit_hist'], sort_keys=True) for r in rs}; all_hists |= hists
        clean = all(r['server_rc'] == 0 and r['incomplete'] == 0 and r['rows'] == r['tasks']
                    and set(r['client_rcs']) <= {0} for r in rs)
        peak = {k: max(r['peak'][k] for r in rs) for k in ('children', 'tree_threads', 'tree_fds', 'tree_rss_mb')}
        line.append(f'{label}: n={len(rs)} median={stats[label][0]:.1f} spread={stats[label][1]:.1f} '
                    f'hist={"/".join(hists)} clean={clean} peak={peak}')
        ok &= clean and len(hists) == 1
    ok &= len(all_hists) <= 1
    line.append(f'results {"SAME" if len(all_hists) <= 1 else "DIFFER"} across labels')
    if len(stats) == 2:
        (mb, sb), (mn, sn) = stats[base], stats[new]
        verdict = mn >= mb - max(sb, sn)
        ok &= verdict
        line.append(f'throughput {"OK" if verdict else "REGRESSED"} ({mn - mb:+.1f} vs tolerance {max(sb, sn):.1f})')
    print('\n  '.join(line))
sys.exit(0 if ok else 1)
