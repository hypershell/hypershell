"""The site-wide notice shown while the docs are built from a pre-release or development version.

The published site builds from `develop`, so between releases it describes behaviour that the
stable release on PyPI does not have. `conf.py` passes `announcement(release)` to the Furo theme;
a final version yields no notice, so the notice goes away by itself when `develop` carries one.
"""

from typing import Optional

from packaging.version import Version

__all__ = ['announcement']


def announcement(release: str, base: str = '') -> Optional[str]:
    """The notice (HTML) for the docs of `release`, or None for a final release.

    The notice is the same HTML on every page, so its link can't be relative to the page. `base` is
    the site's root URL; the link is absolute under it, so it also works from a build opened from
    disk.
    """
    base = base.rstrip('/')
    version = Version(release)
    if not (version.is_prerelease or version.is_devrelease):
        return None
    line = f'{version.major}.{version.minor}'
    marker = f'{line}.{version.micro}'
    if version.micro:
        stable = f'the {line}.{version.micro - 1} release'
    elif version.minor:
        stable = f'the {version.major}.{version.minor - 1}.x stable release'
    else:
        stable = 'the current stable release'
    return (f'These docs track the {line} development line. '
            f'Items marked &ldquo;Added in version {marker}&rdquo; or &ldquo;Changed in version '
            f'{marker}&rdquo; are not in {stable}. '
            f'Install the pre-release with <code>pip install --pre hypershell</code>. '
            f'See <a href="{base}/whats_new.html">What&rsquo;s new in {line}</a>.')
