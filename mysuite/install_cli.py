from __future__ import annotations

import shutil
import sys
from typing import Optional

import typer
from rich.markup import escape
from rich.table import Table

from mysuite import components
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_step


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


@jsonout.with_json("install")
def install(
    name: Optional[str] = typer.Argument(None, help="Component to install (see the list). Leave out to list them."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Go ahead without asking (required when there is no terminal, and with --json)."),
    remove: bool = typer.Option(False, "--remove", help="Delete the component again."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be fetched/built and where, change nothing."),
) -> None:
    """Fetch the heavier parts once, when you need them - mysuite itself stays small."""
    if name is None:
        table = Table(title="components")
        for col in ("name", "what", "size", "installed"):
            table.add_column(col)
        for c in components.COMPONENTS.values():
            ok = components.supported(c)
            state = "yes" if c.is_installed() else ("no" if ok else "not on this system")
            jsonout.add_item(name=c.name, description=c.description, size=c.size, needs=c.needs, installed=c.is_installed(),
                             supported=ok, disk_bytes=components.disk_usage(c), status="ok")
            table.add_row(c.name, escape(c.description), escape(c.size), state)
        console.print(table)
        console.print(f"[dim]install with: mysuite install NAME   ·   files live in {escape(str(components.root()))}[/dim]")
        return
    try:
        c = components.get(name)
    except components.ComponentError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    if not components.supported(c):
        log_error(f"{c.name} is not available on this system ({', '.join(c.platforms)} only)")
        raise typer.Exit(1)
    if remove and c.name == "tools":
        log_error("the Homebrew tools are shared with other programs: remove them with `brew uninstall` if you really want to")
        raise typer.Exit(1)
    if remove:
        gone = c.remove()
        for p in gone:
            log_step(f"removed {escape(str(p))}")
        jsonout.add_item(name=c.name, status="removed" if gone else "not_installed", removed=gone)
        if not gone:
            console.print("[dim]nothing to remove[/dim]")
        return
    if c.is_installed():
        jsonout.add_item(name=c.name, status="already_installed")
        console.print(f"{c.name} is already installed")
        return
    steps = list(c.steps)
    if c.name == "tools":
        steps = [f"brew install {' '.join(components._missing_formulae())}"]
    jsonout.add_item(name=c.name, status="planned" if dry_run else "installing", size=c.size, needs=c.needs, steps=steps)
    console.print(f"[bold]{escape(c.name)}[/bold] - {escape(c.description)}")
    console.print(f"  size: {escape(c.size)}\n  needs: {escape(c.needs)}")
    for s in steps:
        console.print(f"  - {escape(s)}")
    console.print("  [dim]this is the only time mysuite uses the network for it; remove it any time with `mysuite install NAME --remove`[/dim]")
    if dry_run:
        return
    if not yes:
        if jsonout.active() or not sys.stdin.isatty():
            log_error("this downloads/builds things: pass --yes to confirm")
            raise typer.Exit(1)
        if not typer.confirm("Go ahead?", default=False):
            raise typer.Exit(1)
    try:
        c.install(lambda line: console.print(f"[dim]{escape(line)}[/dim]"))
    except components.ComponentError as exc:
        # leave nothing half-installed behind
        if c.name == "shield" and not (components.env_dir("shield") / ".mysuite-ready").exists():
            shutil.rmtree(components.env_dir("shield"), ignore_errors=True)
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    jsonout.add_item(name=c.name, status="installed")
    log_step(f"{escape(c.name)} installed")
