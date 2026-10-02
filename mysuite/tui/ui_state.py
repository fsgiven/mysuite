"""Remembered TUI form values: what you last typed per tool, and recently used paths.

Stored only on this machine in ``~/.cache/mysuite/ui-state.json`` (override with ``MYSUITE_UI_STATE``;
turn off with ``MYSUITE_NO_UI_STATE=1``). It holds field *values* and file *paths* — never file contents.
Every read and write is best-effort: a missing, unreadable or corrupt file just means "no memory".
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MAX_RECENT = 12
_VERSION = 1


def enabled() -> bool:
    return os.environ.get("MYSUITE_NO_UI_STATE", "").strip() not in ("1", "true", "yes")


def state_path() -> Path:
    override = os.environ.get("MYSUITE_UI_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "mysuite" / "ui-state.json"


def _load() -> dict[str, Any]:
    if not enabled():
        return {}
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("version") == _VERSION else {}


def _save(data: dict[str, Any]) -> None:
    if not enabled():
        return
    data["version"] = _VERSION
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ui-state-")
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def tool_values(tool: str) -> dict[str, Any]:
    """The values last used in *tool*'s form (empty when nothing is remembered)."""
    values = _load().get("tools", {}).get(tool, {})
    return values if isinstance(values, dict) else {}


def remember_tool(tool: str, values: dict[str, Any]) -> None:
    data = _load()
    data.setdefault("tools", {})[tool] = values
    _save(data)


def forget_tool(tool: str) -> None:
    data = _load()
    if data.get("tools", {}).pop(tool, None) is not None:
        _save(data)


def recent_paths(kind: str = "files") -> list[str]:
    """Recently used input paths (most recent first), skipping ones that no longer exist."""
    paths = _load().get("recent", {}).get(kind, [])
    return [p for p in paths if isinstance(p, str) and Path(p).exists()]


def add_recent(paths: list[Path | str], kind: str = "files") -> None:
    if not paths:
        return
    data = _load()
    current = data.setdefault("recent", {}).get(kind, [])
    merged: list[str] = []
    for item in [*(str(p) for p in paths), *current]:
        if item not in merged:
            merged.append(item)
    data["recent"][kind] = merged[:MAX_RECENT]
    _save(data)


def forget_all() -> None:
    if not enabled():
        return
    try:
        state_path().unlink(missing_ok=True)
    except OSError:
        pass
