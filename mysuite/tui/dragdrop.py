from __future__ import annotations

from pathlib import Path

_QUOTE_PAIRS = [('"', '"'), ("'", "'")]


def _unquote(token: str) -> str:
    for open_q, close_q in _QUOTE_PAIRS:
        if len(token) >= 2 and token.startswith(open_q) and token.endswith(close_q):
            return token[1:-1]
    return token


def _unescape(token: str) -> str:
    # Terminal.app/iTerm2-style drops backslash-escape shell-special characters,
    # e.g. "My\ File.svg" — undo that so the path matches the real filesystem name.
    result = []
    i = 0
    while i < len(token):
        if token[i] == "\\" and i + 1 < len(token):
            result.append(token[i + 1])
            i += 2
        else:
            result.append(token[i])
            i += 1
    return "".join(result)


DEFAULT_DROP_EXTENSIONS: frozenset[str] = frozenset({".svg"})


def parse_dropped_paths(
    text: str, extensions: frozenset[str] = DEFAULT_DROP_EXTENSIONS
) -> list[Path]:
    """Parses the raw text a terminal inserts when a file is dragged onto the
    window (delivered to Textual as a Paste event) into real, existing paths.

    A dropped file's path may arrive quoted ('...'/"...") or backslash-escaped
    (Terminal.app/iTerm2 style), and a multi-file drop may arrive as one
    newline-separated paste. Only entries that actually exist on disk and are
    either a directory or a file whose suffix is in extensions are kept —
    anything else (including an ordinary text paste that isn't a file path at
    all) is silently dropped, so this returns [] rather than raising for
    non-path pastes. Defaults to {".svg"} to preserve the original SVG-only
    drop behavior for existing callers; pass a different set (or the full
    recognized-format set from mysuite.convert._parsing) for a general-purpose
    drop target."""
    paths: list[Path] = []
    for line in text.splitlines():
        token = line.strip()
        if not token:
            continue
        token = _unescape(_unquote(token))
        candidate = Path(token)
        if candidate.is_dir() or (candidate.is_file() and candidate.suffix.lower() in extensions):
            paths.append(candidate)
    return paths


def parse_input_files_field(value: str) -> list[Path]:
    """Parses an #input-files field's value into individual paths. The field
    normally holds a comma-separated list, but a single selected/dropped path
    can itself contain a literal comma in its name — common in macOS's own
    default export filenames, e.g. "ChatGPT Image 26 ago 2026, 12_32_12.png"
    — so a trimmed value that is itself one real, existing path is returned
    as-is rather than being split apart on that comma. Only falls back to
    comma-splitting when the whole value isn't a single existing path."""
    trimmed = value.strip()
    if trimmed and Path(trimmed).exists():
        return [Path(trimmed)]
    return [Path(p.strip()) for p in value.split(",") if p.strip()]


def merge_paths_into_input(existing_value: str, new_paths: list[Path]) -> str:
    """Appends new_paths to the existing comma-separated #input-files value,
    de-duplicated and order-preserving, matching how the form fields parse
    that field back out (parse_input_files_field)."""
    entries = [str(p) for p in parse_input_files_field(existing_value)]
    seen = set(entries)
    for path in new_paths:
        token = str(path)
        if token not in seen:
            seen.add(token)
            entries.append(token)
    return ",".join(entries)
