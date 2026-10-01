"""Machine-readable output for agents (`--json`).

Contract (SCHEMA_VERSION 1): with `--json`, stdout carries exactly ONE JSON document

    {"schema_version": 1, "command": "export", "ok": true, "exit_code": 0,
     "items": [...], "warnings": [...], "errors": [...], ...extra}

Human text (progress, tables) goes to stderr instead, so nothing else can corrupt stdout.
Exit codes: 0 ok · 1 some item failed · 2 bad usage · 3 refused by policy/sandbox · 4 missing tool.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import typer

from mysuite.utils.console import console

SCHEMA_VERSION = 1

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED, EXIT_MISSING_TOOL = 0, 1, 2, 3, 4


@dataclass
class Report:
    command: str
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "command": self.command,
            "ok": self.exit_code == 0,
            "exit_code": self.exit_code,
            "items": self.items,
            "warnings": self.warnings,
            "errors": self.errors,
            **self.extra,
        }


_current: Report | None = None


def active() -> bool:
    return _current is not None


def add_item(**fields: Any) -> None:
    if _current is not None:
        _current.items.append({k: _jsonable(v) for k, v in fields.items()})


def add_warning(message: str) -> None:
    if _current is not None and message not in _current.warnings:
        _current.warnings.append(message)


def add_error(message: str) -> None:
    if _current is not None:
        _current.errors.append(message)


def set_extra(**fields: Any) -> None:
    if _current is not None:
        _current.extra.update({k: _jsonable(v) for k, v in fields.items()})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


@contextmanager
def session(enabled: bool, command: str) -> Iterator[Report | None]:
    """Collects a command's results; when `enabled`, prints one JSON document on exit and
    sends human output to stderr. Records typer.Exit codes; an uncaught exception becomes
    an error entry (exit 1) instead of a traceback on stdout."""
    global _current
    if not enabled:
        yield None
        return
    report = Report(command)
    previous, _current = _current, report
    real_file = console.file
    console.file = sys.stderr
    try:
        try:
            yield report
        except typer.Exit as exc:
            report.exit_code = exc.exit_code or 0
            if report.exit_code and not report.errors:
                report.errors.append(f"exited with code {report.exit_code}")
            raise
        except Exception as exc:  # noqa: BLE001 - agents get a structured error, not a traceback
            report.exit_code = EXIT_FAILED
            report.errors.append(f"{type(exc).__name__}: {exc}")
            raise typer.Exit(EXIT_FAILED) from exc
        else:
            if any(i.get("status") == "failed" for i in report.items):
                report.exit_code = EXIT_FAILED
                raise typer.Exit(EXIT_FAILED)
    finally:
        console.file = real_file
        _current = previous
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def with_json(command: str):
    """Decorator: adds `--json` to a Typer command and wraps it in a session()."""
    import functools
    import inspect

    def decorate(fn):
        sig = inspect.signature(fn)
        option = inspect.Parameter(
            "json_output", inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                False, "--json",
                help="Print one machine-readable JSON document on stdout (human text goes to stderr). "
                "Exit codes: 0 ok, 1 some item failed, 2 bad usage, 3 refused by policy, 4 missing tool.",
            ),
            annotation=bool,
        )

        @functools.wraps(fn)
        def wrapper(*args, json_output: bool = False, **kwargs):
            with session(json_output, command):
                return fn(*args, **kwargs)

        params = [p for p in sig.parameters.values()]
        wrapper.__signature__ = sig.replace(parameters=[*params, option])  # type: ignore[attr-defined]
        wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "json_output": bool}
        return wrapper

    return decorate
