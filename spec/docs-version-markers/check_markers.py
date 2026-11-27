"""Check the 2.9.0 version markers in docs/ against the inventory in markers.md.

Every inventory row must match a marker in its file (same directive, version 2.9.0, same set of
:pr:/:commit: links), and every 2.9.0 marker under docs/ (outside docs/_build) must match a row.
Each marker must carry at least one link. Exits 1 with a list of mismatches, 0 otherwise.

Usage: uv run python spec/docs-version-markers/check_markers.py
"""

import re
import sys
from collections import Counter
from pathlib import Path

VERSION = '2.9.0'
SPEC = Path(__file__).resolve().parent
ROOT = SPEC.parents[1]
DOCS = ROOT / 'docs'
INVENTORY = SPEC / 'markers.md'

MARKER = re.compile(r'^(?P<indent>\s*)\.\. (?P<directive>versionadded|versionchanged):: (?P<version>\S+)')
LINK = re.compile(r':(?P<role>pr|commit):`(?P<target>[^`]+)`')
ROW = re.compile(r'^\|\s*(?P<file>docs/\S+)\s*\|[^|]*\|\s*(?P<directive>\w+)\s*\|\s*(?P<links>[^|]*)\|')


def parse_link(token: str) -> tuple[str, str]:
    """Inventory token to (role, target): '#82' is a PR, a hex hash is a commit."""
    if re.fullmatch(r'#\d+', token):
        return 'pr', token[1:]
    if re.fullmatch(r'[0-9a-f]{7,40}', token):
        return 'commit', token
    raise ValueError(f'unrecognised link {token!r} (expected #NN or a commit hash)')


def read_inventory() -> Counter:
    rows = Counter()
    for lineno, line in enumerate(INVENTORY.read_text().splitlines(), 1):
        match = ROW.match(line)
        if not match:
            continue
        links = frozenset(parse_link(t) for t in match['links'].split())
        if match['directive'] not in ('versionadded', 'versionchanged'):
            raise ValueError(f'markers.md:{lineno}: bad directive {match["directive"]!r}')
        rows[(match['file'], match['directive'], links)] += 1
    return rows


def read_docs() -> tuple[Counter, list[str]]:
    found, problems = Counter(), []
    for path in sorted(DOCS.rglob('*.rst')):
        if '_build' in path.relative_to(DOCS).parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            match = MARKER.match(line)
            if not match or match['version'] != VERSION:
                continue
            indent = len(match['indent'])
            body = []
            for following in lines[i + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                body.append(following)
            links = frozenset((m['role'], m['target']) for m in LINK.finditer('\n'.join(body)))
            if not links:
                problems.append(f'{rel}:{i + 1}: {match["directive"]} {VERSION} has no :pr: or :commit: link')
            found[(rel, match['directive'], links)] += 1
    return found, problems


def show(key) -> str:
    file, directive, links = key
    names = ' '.join(sorted(f'#{t}' if r == 'pr' else t for r, t in links)) or '(no links)'
    return f'{file}: {directive} {VERSION} [{names}]'


def main() -> int:
    inventory = read_inventory()
    if not inventory:
        print(f'error: no inventory rows found in {INVENTORY}', file=sys.stderr)
        return 1
    found, problems = read_docs()
    for file in sorted({key[0] for key in inventory}):
        if not (ROOT / file).is_file():
            problems.append(f'{file}: listed in markers.md but does not exist')
    for key, count in sorted((inventory - found).items(), key=str):
        problems.extend(f'missing from docs (in markers.md): {show(key)}' for _ in range(count))
    for key, count in sorted((found - inventory).items(), key=str):
        problems.extend(f'not in markers.md (found in docs): {show(key)}' for _ in range(count))
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        print(f'FAIL: {len(problems)} problem(s)', file=sys.stderr)
        return 1
    added = sum(n for (_, d, _), n in found.items() if d == 'versionadded')
    changed = sum(n for (_, d, _), n in found.items() if d == 'versionchanged')
    files = len({f for f, _, _ in found})
    print(f'OK: {added + changed} markers ({added} versionadded, {changed} versionchanged) '
          f'in {files} files match markers.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
