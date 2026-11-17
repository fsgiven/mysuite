"""WS-D options with fixed values are declared as choices, so `mysuite schema` (and a local model) can't invent values."""
from __future__ import annotations

import pytest

from mysuite import schema
from mysuite.metadata.metadata import DATA_MINING
from mysuite.pdf import ops


def choices(command: str, flag: str):
    entry = schema.build([command])
    cmds = entry.get("commands", entry)
    opts = next(iter(cmds.values()))["options"]
    return next(o for o in opts if flag in o["flags"]).get("choices")


@pytest.mark.parametrize("command,flag,expected", [
    ("pdf number", "--position", list(ops.POSITIONS)),
    ("pdf stamp", "--position", list(ops.POSITIONS)),
    ("pdf render", "--format", ["png", "jpeg", "jpg"]),
    ("pdf compress", "--level", list(ops.COMPRESS_LEVELS)),
    ("metadata declare", "--policy", list(DATA_MINING)),
    ("metadata protect", "--policy", list(DATA_MINING)),
    ("mark embed", "--strength", ["subtle", "standard", "strong"]),
    ("mark embed", "--format", ["png", "jpeg", "jpg", "tiff", "webp"]),
    ("shield", "--strength", ["light", "standard", "strong"]),
    ("shield", "--device", ["cpu", "mps", "cuda"]),
])
def test_fixed_values_are_choices(command, flag, expected):
    assert sorted(choices(command, flag)) == sorted(expected)
