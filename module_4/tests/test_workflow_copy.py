"""The committed copy of the CI workflow matches the one GitHub runs.

Marked ``integration``: the workflow is what runs this suite end to end, and
this is the only test in the file.

``module_4/.github/workflows/tests.yml`` exists so the module can be read on its
own -- everything the workflow runs belongs to module_4.  GitHub reads workflows
only from the repository root, so that copy is inert, and an inert copy is
exactly the kind of file that goes stale without anyone noticing.  Comparing the
two here turns "somebody edited one of them" into a failing test.
"""

from __future__ import annotations

import pytest

from conftest import PROJECT_ROOT

pytestmark = pytest.mark.integration

#: The workflow GitHub actually runs.
LIVE_WORKFLOW = PROJECT_ROOT.parent / ".github" / "workflows" / "tests.yml"

#: The copy kept with the module it tests.
MODULE_COPY = PROJECT_ROOT / ".github" / "workflows" / "tests.yml"


def read(path):
    """A workflow file's text, with line endings normalized.

    The repository normalizes to LF on checkout, but a Windows working tree can
    hold CRLF; the two files must agree on content, not on how git happened to
    write them out.
    """
    if not path.exists():
        pytest.fail(
            "{path} is missing. The CI workflow is a deliverable and is "
            "expected in two places -- see the header comment in either "
            "file.".format(path=path)
        )
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_module_copy_matches_the_workflow_github_runs():
    """Edit one, edit both -- or this fails.

    Asserted on the whole text rather than on a few keys, because the ways the
    two could drift that matter most are the ones nobody thought to check: a
    changed Python version, a step added, a different test command.
    """
    live = read(LIVE_WORKFLOW)
    copy = read(MODULE_COPY)

    assert copy == live, (
        "{copy} has drifted from {live}. GitHub runs the second one; the first "
        "is a copy kept so module_4 reads on its own. Re-copy it.".format(
            copy=MODULE_COPY, live=LIVE_WORKFLOW
        )
    )


def test_the_workflow_runs_the_documented_command():
    """The workflow runs the suite the way README.md and pytest.ini describe.

    A green check means nothing if the job quietly ran a different command than
    the one the documentation tells a reader to run.
    """
    live = read(LIVE_WORKFLOW)

    assert (
        'python -m pytest module_4 -m "web or buttons or analysis or db or integration"'
        in live
    )
