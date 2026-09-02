"""Shared setup for both suites.

The suites are ordinary pytest modules: every check is a plain `assert`, so
pytest reports the failing line and its values with no translation layer in
between. `ok(name, cond)` and `has(name, text, *needles)` survive only as
assertion helpers -- they attach the sentence a check is testing to the failure
message, which a bare `assert "x" in out` cannot.

The package under test is imported from `src/` rather than from an installed
copy, so the suite tests the tree you are editing and not whatever `pip` last
put on the path.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "src"))
