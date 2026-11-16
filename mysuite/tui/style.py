"""The dashboard's one visual system: a single accent, neutral greys, and colour only for status.

theme.css repeats these as literal hex (Textual CSS here has no shared variables with Python); keep both in step.
"""
from __future__ import annotations

ACCENT = "#7DD3FC"        # the one accent: focus, section titles, primary action, selected chips
TEXT = "#E8E8E8"
MUTED = "#8B93A7"         # labels, descriptions
DIM = "#6B7280"           # hints, shortcut tags
SURFACE = "#161822"       # cards, inputs, bars
SURFACE_HI = "#1D2333"    # focused card / input
BACKGROUND = "#10121A"
BORDER = "#2A2E3F"

OK = "#4ADE80"
WARN = "#FBBF24"
ERROR = "#F87171"

STATUS_MARKS = {"✓": OK, "⚠": WARN, "✗": ERROR, "—": DIM}

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def version() -> str:
    try:
        from importlib.metadata import version as _v

        return _v("mysuite")
    except Exception:  # noqa: BLE001 - running from a source tree
        return "dev"
