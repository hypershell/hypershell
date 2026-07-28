# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.

import datetime
import hypershell

# -- Project information -----------------------------------------------------

year = datetime.datetime.now().year
project = 'hypershell'
copyright = f'2019-{year} Geoffrey Lentner'  # noqa: shadows builtin name?
author = 'Geoffrey Lentner <glentner@purdue.edu>'

# The full version, including alpha/beta/rc tags
release = hypershell.__version__
version = hypershell.__version__

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx.ext.extlinks',
    'sphinx.ext.viewcode',
    'sphinxext.opengraph',
    'sphinx_sitemap',
    'sphinx_inline_tabs',
    'sphinx_copybutton',
    'sphinxcontrib.details.directive',
    'sphinx_reredirects',
]

# The Tutorial, blog, and roadmap pages moved to the hypershell.org site. sphinx-reredirects emits a
# meta-refresh stub at each old path so previously-published ReadTheDocs URLs redirect there instead
# of 404ing. Keys must be explicit (non-wildcard): the source docs are deleted, and a wildcard would
# match only pages that still exist. The durable native RTD dashboard redirects are documented in
# spec/docs-trim-migrated-sections/redirect-runbook.md.
redirects = {
    'tutorial/basic': 'https://www.hypershell.org/tutorials/basic',
    'tutorial/distributed': 'https://www.hypershell.org/tutorials/distributed',
    'tutorial/hybrid': 'https://www.hypershell.org/tutorials/hybrid',
    'tutorial/advanced': 'https://www.hypershell.org/tutorials/advanced',
    'blog/index': 'https://www.hypershell.org/blog',
    'blog/20230329_2_2_0_release': 'https://www.hypershell.org/blog/hypershell-2-2-0',
    'blog/20230413_2_3_0_release': 'https://www.hypershell.org/blog/hypershell-2-3-0',
    'blog/20230602_2_4_0_release': 'https://www.hypershell.org/blog/hypershell-2-4-0',
    'blog/20240518_2_5_0_release': 'https://www.hypershell.org/blog/hypershell-2-5-0',
    'blog/20240706_2_5_2_release': 'https://www.hypershell.org/blog/hypershell-2-5-2',
    'blog/20241115_2_6_0_release': 'https://www.hypershell.org/blog/hypershell-2-6-0',
    'blog/20241115_announce_logo': 'https://www.hypershell.org/blog/new-logo-and-discord',
    'blog/20241231_2_6_1_release': 'https://www.hypershell.org/blog/hypershell-2-6-1',
    'blog/20250215_2_6_5_release': 'https://www.hypershell.org/blog/hypershell-2-6-5',
    'blog/20250405_2_6_6_release': 'https://www.hypershell.org/blog/hypershell-2-6-6',
    'blog/20250504_2_7_0_release': 'https://www.hypershell.org/blog/hypershell-2-7-0',
    'blog/20260705_2_8_0_release': 'https://www.hypershell.org/blog/hypershell-2-8-0',
    'roadmap': 'https://www.hypershell.org/about',
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']
source_suffix = '.rst'

# The master toctree document.
master_doc = 'index'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'dracula'
pygments_dark_style = 'dracula'   # NOTE: specific to Furo theme

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = False

# -- Options for HTML output -------------------------------------------------

html_title = 'HyperShell v2'
html_baseurl = 'https://hypershell.readthedocs.io'
html_theme = 'furo'
html_css_files = [
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/fontawesome.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/solid.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/brands.min.css",
]
html_favicon = '_static/logo-dark-mode.png'
html_theme_options = {
    # 'announcement': 'See Installation page for not on PyPI package name issue!',
    'sidebar_hide_name': True,
    'light_logo': 'logo-light-mode.png',
    'dark_logo': 'logo-dark-mode.png',
    'light_css_variables': {
        'color-brand-primary': '#ee0d7e',
        'color-brand-content': '#ee0d7e',
    },
    'dark_css_variables': {
        'color-brand-primary': '#e7529d',
        'color-brand-content': '#e7529d',
        'color-background-primary': '#161B23',
        'color-sidebar-background': '#11151b',
        'color-sidebar-search-background': '#11151b',
        'color-announcement-background': '#ee0d7e;',
    },
    'footer_icons': [
        {
            'name': 'GitHub',
            'url': 'https://github.com/hypershell/hypershell',
            'html': '',
            'class': 'fa-brands fa-solid fa-github fa-2x',
        },
        {
            'name': 'Discord',
            'url': 'https://discord.gg/wmv5gyUfkN',
            'html': '',
            'class': 'fa-brands fa-solid fa-discord fa-2x',
        },
    ],
}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

# export variables with epilogue
rst_epilog = f"""
.. |release| replace:: {release}
.. |copyright| replace:: {copyright}

.. |br| raw:: html
 
   <br />
"""


# manual pages options
man_pages = [
    (
        'manual',
        'hyper-shell',  # NOTE: do not remove this
        'Process shell commands over a distributed, asynchronous queue',
        'Geoffrey Lentner <glentner@purdue.edu>.',
        '1'
    ),
    (
        'manual',
        'hs',
        'Process shell commands over a distributed, asynchronous queue',
        'Geoffrey Lentner <glentner@purdue.edu>.',
        '1'
    ),
]


def setup(app):
    app.add_css_file('custom.css')
