from __future__ import annotations

import os
import subprocess
from pathlib import Path


class MysuiteToolError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        cmd_str = " ".join(cmd)
        stderr_tail = "\n".join(stderr.strip().splitlines()[-20:])
        super().__init__(
            f"command failed ({returncode}): {cmd_str}\n{stderr_tail}"
        )


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MysuiteToolError(cmd, result.returncode, result.stderr)
    return result


def atomic_write_via(output_path: Path, write_fn, *, preserve_extension: bool = False) -> None:
    """Calls write_fn(tmp_path) to produce output_path's contents at a sibling
    temp path, then atomically renames it into place — a partially-written
    output (e.g. a subprocess killed mid-write) is never left at output_path.

    preserve_extension=True names the temp file "<stem>.tmp<suffix>" (e.g.
    "photo.tmp.jpg") instead of the default "<stem><suffix>.tmp" — some
    external tools (iconutil, c2patool) infer the output format from the
    file's real extension and fail or misbehave if it ends in something else
    like ".tmp"."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if preserve_extension:
        tmp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)
    else:
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    write_fn(tmp_path)
    os.replace(tmp_path, output_path)
