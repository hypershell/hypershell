"""Check the built site's notice and the What's new page.

- The built index and a nested page each carry the announcement, linking whats_new.html.
- whats_new.html exists with its three sections.
- The notice logic (docs/_ext/site_notice.py) yields a notice for pre-release and development
  versions, and none for a final version.

Usage: uv run python spec/docs-version-markers/check_site.py docs/_build/html

The script itself uses only the standard library; the notice helper it loads imports `packaging`,
which the docs build already requires.
"""

import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'docs' / '_ext' / 'site_notice.py'
PAGES = ['index.html', 'cli/relay.html']
SECTIONS = ['Upgrading from 2.8', 'New and changed', 'Not yet released']
ANNOUNCEMENT = re.compile(r'<div class="announcement">(?P<body>.*?)</div>', re.S)


class Headings(HTMLParser):
    """Collect the text of h1-h3 elements."""

    def __init__(self) -> None:
        super().__init__()
        self.depth, self.current, self.found = 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'h2', 'h3'):
            self.depth, self.current = 1, []
        elif self.depth and tag == 'a' and ('class', 'headerlink') in attrs:
            self.depth = 2  # skip the pilcrow link

    def handle_endtag(self, tag):
        if tag in ('h1', 'h2', 'h3') and self.depth:
            self.found.append(''.join(self.current).strip())
            self.depth = 0
        elif tag == 'a' and self.depth == 2:
            self.depth = 1

    def handle_data(self, data):
        if self.depth == 1:
            self.current.append(data)


def load_helper():
    spec = importlib.util.spec_from_file_location('site_notice', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.announcement


def check_pages(build: Path) -> list[str]:
    problems = []
    for page in PAGES:
        path = build / page
        if not path.is_file():
            problems.append(f'{page}: not built')
            continue
        match = ANNOUNCEMENT.search(path.read_text())
        if not match:
            problems.append(f'{page}: no announcement')
            continue
        body = match['body']
        for needle in ('href="https://hypershell.readthedocs.io/whats_new.html"', 'pip install --pre hypershell', 'development line'):
            if needle not in body:
                problems.append(f'{page}: announcement lacks {needle!r}')
    return problems


def check_whats_new(build: Path) -> list[str]:
    path = build / 'whats_new.html'
    if not path.is_file():
        return ['whats_new.html: not built']
    parser = Headings()
    parser.feed(path.read_text())
    return [f'whats_new.html: no section heading {name!r}' for name in SECTIONS if name not in parser.found]


def check_logic() -> list[str]:
    announcement = load_helper()
    problems = []
    for final in ('2.9.0', '2.9.1', '3.0.0'):
        if announcement(final) is not None:
            problems.append(f'site_notice: final version {final} yields a notice')
    cases = {'2.9.0a6': ('2.9 development line', '2.9.0', '2.8.x'),
             '2.9.0rc1': ('2.9 development line', '2.9.0', '2.8.x'),
             '2.10.0.dev3': ('2.10 development line', '2.10.0', '2.9.x')}
    for release, needles in cases.items():
        notice = announcement(release)
        if not notice:
            problems.append(f'site_notice: {release} yields no notice')
            continue
        problems.extend(f'site_notice: {release} notice lacks {n!r}' for n in needles if n not in notice)
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print('usage: check_site.py BUILD_DIR', file=sys.stderr)
        return 2
    build = Path(sys.argv[1])
    if not build.is_dir():
        print(f'error: {build} is not a directory', file=sys.stderr)
        return 2
    problems = check_pages(build) + check_whats_new(build) + check_logic()
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        print(f'FAIL: {len(problems)} problem(s)', file=sys.stderr)
        return 1
    print(f'OK: notice on {", ".join(PAGES)}; whats_new.html has {len(SECTIONS)} sections; '
          f'no notice for a final version')
    return 0


if __name__ == '__main__':
    sys.exit(main())
