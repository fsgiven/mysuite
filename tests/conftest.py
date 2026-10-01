from __future__ import annotations

import pytest

from mysuite import sandbox


@pytest.fixture(autouse=True)
def _no_sandbox_leaks_between_tests():
    """`mysuite --allow …` configures a process-wide policy; a CLI test must not leave it on for the next test."""
    sandbox.configure(None)
    yield
    sandbox.configure(None)
