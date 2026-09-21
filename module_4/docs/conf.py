"""Sphinx configuration for the Grad Cafe analytics documentation.

``src/`` is a source root rather than a package -- the modules import each other
by bare name, as ``clean.py`` does with ``from scrape import ...`` -- so it goes
on ``sys.path`` here exactly as ``tests/conftest.py`` puts it there.  Autodoc
then imports ``flask_app``, ``scrape`` and the rest by the same names the
application uses.

Autodoc imports the modules it documents, so every one of them must be safe to
import with no database running.  They are: :mod:`models` builds its Engine on
first use rather than at import, and nothing else opens a connection until it is
asked a question.

Built with ``make -C docs html`` (or ``docs\make.bat html`` on Windows), which
puts the doctree cache in ``_build/doctrees`` rather than inside the published
tree.  ``make strict`` builds it the way CI and Read the Docs do, with warnings
as errors.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DOCS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

# A connection string that describes a server rather than one that reaches one.
# db_config reads the environment at import time nowhere, but `describe()` runs
# while autodoc renders default values, and pointing it here keeps a developer's
# real credentials out of a built page.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres@localhost:5432/gradcafe"
)

# -- Project information -----------------------------------------------------
project = "Grad Cafe Analytics"
author = "Nipul Jayasekera"
copyright = "2026, Nipul Jayasekera"
release = "Module 4"
version = "4.0"

# -- General configuration ---------------------------------------------------
extensions = [
    # Pulls the API reference out of the modules' own docstrings, so there is
    # one description of each function rather than two that can disagree.
    "sphinx.ext.autodoc",
    # Renders the :param:/:returns:/:raises: fields the docstrings already use.
    "sphinx.ext.napoleon",
    # Turns :func:`load_data.load_into_database` into a link.
    "sphinx.ext.intersphinx",
    # "View page source" links to a highlighted copy of the module.
    "sphinx.ext.viewcode",
    # Lets a page pull in a literal chunk of another file.
    "sphinx.ext.autosectionlabel",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Section labels are per-document, so "Overview" in two pages is two labels.
autosectionlabel_prefix_document = True

# -- Autodoc -----------------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}

# Signatures read better with the module's own annotations than with the
# resolved ones; the modules use `from __future__ import annotations` throughout.
autodoc_typehints = "description"
autodoc_preserve_defaults = True

# Documenting these would add nothing: they are third-party objects re-exported
# by the modules that use them.
autodoc_mock_imports: list = []

# -- Viewcode ----------------------------------------------------------------
# Off: with it on, viewcode follows a re-exported name back to where it was
# defined and renders that module too -- which for the typing aliases these
# modules use means half a megabyte of the standard library's own source.
viewcode_follow_imported_members = False


# -- Napoleon ----------------------------------------------------------------
napoleon_google_docstring = False
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True

# -- Intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "flask": ("https://flask.palletsprojects.com/en/stable/", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/20/", None),
    "psycopg": ("https://www.psycopg.org/psycopg3/docs/", None),
    "pytest": ("https://docs.pytest.org/en/stable/", None),
}

# Read the Docs builds without network access to some inventories; a missing one
# should warn rather than fail the build.
intersphinx_timeout = 10

# -- HTML output -------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "Grad Cafe Analytics"
html_short_title = "Grad Cafe Analytics"
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
}

# -- Nitpicking --------------------------------------------------------------
# CI builds with -W, so anything listed here would otherwise fail the build.
# These are annotations autodoc renders as cross-references but which have no
# documented target: module-level type aliases and the Flask/SQLAlchemy classes
# whose inventories do not publish them.
nitpick_ignore = [
    ("py:class", "optional"),
]
