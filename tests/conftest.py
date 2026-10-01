from __future__ import annotations

import shutil

import pytest

from mysuite import doctor

ORIGINAL_TOOL_SPECS = list(doctor.TOOL_SPECS)


@pytest.fixture(autouse=True)
def _only_check_tools_that_exist_for_unrelated_commands(monkeypatch):
    """FINDING D1 (docs/TEST-FINDINGS.md): export/convert/watermark/metadata refuse to
    run unless EVERY tool in doctor.TOOL_SPECS is installed, including the macOS-only
    mysuite-cutout helper. CI and most users don't have it, so for the *other* tests we
    drop tools that aren't installed from that all-or-nothing gate. The D1 test itself
    uses ORIGINAL_TOOL_SPECS to exercise the real behaviour."""
    from mysuite.config import ToolPaths

    paths = ToolPaths()
    present = [s for s in ORIGINAL_TOOL_SPECS if shutil.which(getattr(paths, s.attr)) is not None]
    monkeypatch.setattr(doctor, "TOOL_SPECS", present)
