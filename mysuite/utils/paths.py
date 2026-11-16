from __future__ import annotations

import os
import re
from pathlib import Path

from rich.markup import escape


def tilde(path: Path | str) -> str:
    """Shows a path under the home folder as ~/...: shorter, and it keeps your
    username out of screenshots and screen shares."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def show_path(path: Path | str) -> str:
    """tilde() plus Rich-markup escaping, for putting a path in a RichLog. File
    names are untrusted input: without escaping, one containing "[/bold]" raises
    MarkupError and one containing "[link=...]" injects a clickable link."""
    return escape(tilde(path))


_HOME_WORD = re.compile(r"~[\w.-]*(?=/|$)")


def expand_home(word: str) -> str:
    """Expands a leading ``~`` (or ``~user``) the way a shell would, also after ``--flag=``.

    A shell turns ``--out ~/exports`` into an absolute path before mysuite sees it, but the
    dashboard, the MCP server and pipelines start mysuite without a shell, so the ``~`` arrived
    as is and Path("~/exports") became a folder literally named ``~`` in the current directory.
    Anything else (``~`` in the middle, ``~name`` that is not a user) is left alone.
    """
    prefix, sep, rest = word.partition("=") if word.startswith("--") else ("", "", word)
    if not _HOME_WORD.match(rest):
        return word
    return f"{prefix}{sep}{os.path.expanduser(rest)}"


def expand_home_args(argv: list[str]) -> list[str]:
    return [expand_home(a) for a in argv]
