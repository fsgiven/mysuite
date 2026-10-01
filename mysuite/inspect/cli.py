from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import MysuiteConfigError, load_config
from mysuite.convert._parsing import InvalidInputError, resolve_input_files
from mysuite.inspect.inspect import InspectError, inspect_file, write_contact_sheet, write_thumbnail
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error
from mysuite.utils.subprocess_utils import MysuiteToolError


@jsonout.with_json("inspect")
def inspect_command(
    inputs: List[Path] = typer.Argument(..., exists=True, readable=True, help="Files and/or folders to examine."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories."),
    thumb: Optional[Path] = typer.Option(
        None, "--thumb", help="Write a <=512 px PNG preview of the (single) input, for clients that can look at images."
    ),
    sheet: Optional[Path] = typer.Option(
        None, "--sheet", help="Write a labelled contact sheet PNG of all inputs."
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml."),
) -> None:
    try:
        files = resolve_input_files(inputs, recursive=recursive)
        config = load_config(config_path)
    except (InvalidInputError, MysuiteConfigError) as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    failed = 0
    for f in files:
        try:
            info = inspect_file(f, config.tools)
        except (InspectError, MysuiteToolError) as exc:
            failed += 1
            jsonout.add_item(path=f, status="failed", error=str(exc))
            log_error(f"{escape(str(f))}: {escape(str(exc))}")
            continue
        jsonout.add_item(status="ok", **info)
        _print(info)

    if thumb:
        if len(files) != 1:
            log_error("--thumb needs exactly one input (use --sheet for several)")
            raise typer.Exit(1)
        write_thumbnail(files[0], thumb, config.tools)
        jsonout.set_extra(thumbnail=thumb)
    if sheet:
        write_contact_sheet(files, sheet, config.tools)
        jsonout.set_extra(contact_sheet=sheet)
    if failed:
        raise typer.Exit(1)


def _print(info: dict) -> None:
    table = Table(title=escape(info["path"]), show_header=False)
    for key, value in info.items():
        if key == "path":
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = ", ".join(f"{v.get('hex')} {v.get('share', v.get('uses', ''))}" for v in value)
        elif isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        table.add_row(escape(str(key)), escape(str(value)))
    console.print(table)
