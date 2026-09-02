"""Packaging invariants -- local to this checkout, not part of the sdist's
own test-against-itself story that test_docs.py covers.

Unlike hooks/pre-commit, this never ships to a consumer repo (the sdist only
carries tests/test*.py plus fixtures, and nobody runs kb-graph's own pytest
suite against their project), so it is the right place for a check that only
makes sense inside kb-graph's own checkout.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
    # README/CHANGELOG carry em dashes; without this the child's stdout
    # encodes with the console's codepage (cp1252 on Windows) instead of
    # UTF-8, and decoding those bytes as UTF-8 back in the parent mangles them.
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-m", "kbgraph.cli"] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       env=env)
    return p.returncode, p.stdout


def test_manual_and_changelog_match_root_docs():
    """`kb manual` and `kb changelog` both read one payload (pyproject's
    dynamic `readme` key concatenates README.md then CHANGELOG.md into
    dist-info's Description field) and split it back apart on CHANGELOG's top
    heading. No installed dist-info exists in this checkout, so this also
    exercises the fallback that reads the two root files directly -- if that
    split logic or the fallback ever drifts from the real files, this fails
    loudly instead of shipping a manual with a truncated tail or a changelog
    with a stray README fragment glued to its front."""
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    changelog = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()

    rc, out = run("manual")
    assert rc == 0
    assert out == readme

    rc, out = run("changelog")
    assert rc == 0
    assert out == changelog
