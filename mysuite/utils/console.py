from __future__ import annotations

from rich.console import Console

console = Console()

# Matches mysuite/tui/theme.css's palette exactly, so CLI output and the TUI
# read as the same app rather than relying on the terminal's own ANSI
# green/yellow/red, which vary by terminal color scheme.
SUCCESS = "#4ADE80"
WARNING = "#FBBF24"
ERROR = "#F87171"


def log_step(message: str) -> None:
    console.print(f"[{SUCCESS}]✓[/{SUCCESS}] {message}")


def log_skip(message: str) -> None:
    console.print(f"[{WARNING}]⚠ skipping[/{WARNING}] {message}")


def log_error(message: str) -> None:
    console.print(f"[{ERROR}]✗[/{ERROR}] {message}")
