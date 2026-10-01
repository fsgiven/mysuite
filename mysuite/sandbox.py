"""Optional sandbox for agent use.

Off by default (humans can do what they like). Switched on by the global `--allow DIR` option,
`--sandbox` (allow only the current directory), or the MYSUITE_ROOTS environment variable
(os.pathsep-separated); the MCP server always runs inside it. When on:

* every input and every output path must resolve (symlinks followed) inside an allowed root;
* at most `max_files` input files per run (MYSUITE_MAX_FILES, default 500);
* a refusal is exit code 3 / a structured error, never a partial write.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import typer

DEFAULT_MAX_FILES = 500


class PolicyError(typer.Exit):
    """Prints the refusal (it becomes an `errors` entry under --json) and exits with code 3."""

    def __init__(self, message: str):
        from mysuite.utils.console import log_error
        from rich.markup import escape

        log_error(escape(message))
        self.message = message
        super().__init__(3)


@dataclass
class Policy:
    roots: list[Path] = field(default_factory=list)
    max_files: int = DEFAULT_MAX_FILES

    def __post_init__(self) -> None:
        self.roots = [Path(os.path.realpath(r)) for r in self.roots]

    def _inside(self, path: Path) -> bool:
        real = Path(os.path.realpath(path))
        return any(real == root or root in real.parents for root in self.roots)

    def check(self, path: Path, *, write: bool) -> None:
        if not self._inside(path):
            allowed = ", ".join(str(r) for r in self.roots)
            verb = "write" if write else "read"
            raise PolicyError(f"refused: cannot {verb} {path} - outside the allowed folder(s): {allowed}")


_active: Policy | None = None


def configure(policy: Policy | None) -> None:
    global _active
    _active = policy


def active() -> Policy | None:
    return _active


def from_environment() -> Policy | None:
    raw = os.environ.get("MYSUITE_ROOTS", "").strip()
    if not raw:
        return None
    roots = [Path(p).expanduser() for p in raw.split(os.pathsep) if p]
    try:
        max_files = int(os.environ.get("MYSUITE_MAX_FILES", DEFAULT_MAX_FILES))
    except ValueError:
        max_files = DEFAULT_MAX_FILES
    return Policy(roots, max_files)


def check_read(path: Path) -> None:
    if _active is not None:
        _active.check(path, write=False)


def check_write(path: Path) -> None:
    if _active is not None:
        _active.check(path, write=True)


def check_inputs(files: list[Path]) -> None:
    if _active is None:
        return
    if len(files) > _active.max_files:
        raise PolicyError(f"refused: {len(files)} input files exceeds the limit of {_active.max_files}")
    for f in files:
        _active.check(f, write=False)
