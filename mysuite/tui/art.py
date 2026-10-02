"""ASCII art for the dashboard: the suitcase mascot (two handles on top), a small helper version, and tool icons.

Everything is built from fixed-width rows so every line of a figure is exactly the same width.
"""
from __future__ import annotations

from rich.text import Text

CASE = "#FDE047"
HANDLE = "#EAB308"
CHEEK = "#F9A8D4"
EYE = "#FFFFFF"
SKY = "#7DD3FC"

BIG_W = 34
OPEN = ("●", "●", "‿")
BLINK = ("─", "─", "‿")
WINK = ("●", "─", "◡")
TALK = ("●", "●", "o")

# eye/mouth frames, by name
FACES = {"open": OPEN, "blink": BLINK, "wink": WINK, "talk": TALK}


def big_mascot_rows(face: tuple[str, str, str] = OPEN) -> list[str]:
    """The full suitcase: two handles on top, brows, eyes, cheeks, latches, a label, feet."""
    l, r, m = face
    w, inner = BIG_W, BIG_W - 2

    def pad(s: str) -> str:
        return s.center(w)

    def inside(s: str) -> str:
        return "│" + s.center(inner) + "│"

    handle_w, gap = 10, 6
    left = (w - (2 * handle_w + gap)) // 2
    top = [" "] * w
    mid = [" "] * w
    for start in (left, left + handle_w + gap):
        top[start:start + handle_w] = list("╭" + "─" * (handle_w - 2) + "╮")
        mid[start:start + handle_w] = list("│" + " " * (handle_w - 2) + "│")
    lid = list("╭" + "─" * inner + "╮")
    for start in (left, left + handle_w + gap):
        lid[start] = "┴"
        lid[start + handle_w - 1] = "┴"
    return [
        "".join(top), "".join(mid), "".join(lid),
        inside("╲" + " " * 12 + "╱"),
        inside(l + " " * 12 + r),
        inside("◖◗" + " " * 4 + m + " " * 4 + "◖◗"),
        "├──[▪]" + "─" * (inner - 10) + "[▪]──┤",
        inside("»  m y s u i t e   ┃ a-z ┃"),
        "╰───┬" + "─" * (inner - 9) + "┬────╯",
        "    ╰╯" + " " * (w - 12) + "╰╯    ",
    ]


def _big_style(ch: str, row: int) -> str:
    if row <= 1:
        return HANDLE
    if row == 2:
        return HANDLE if ch == "┴" else CASE
    if ch in "◖◗":
        return CHEEK
    if ch in "●─‿◡╲╱o" and 3 <= row <= 5:
        return EYE
    if ch == "»":
        return SKY
    return CASE


def big_mascot(face: tuple[str, str, str] = OPEN) -> Text:
    text = Text()
    rows = big_mascot_rows(face)
    for row, line in enumerate(rows):
        for ch in line:
            text.append(ch, style=_big_style(ch, row))
        if row < len(rows) - 1:
            text.append("\n")
    return text


SMALL_W = 11


def small_mascot_rows(face: tuple[str, str, str] = OPEN) -> list[str]:
    l, r, m = face
    return [
        " ╭─╮   ╭─╮ ",
        "╭┴─┴───┴─┴╮",
        f"│  {l} {m} {r}  │",
        "╰┬───────┬╯",
    ]


def small_mascot(face: tuple[str, str, str] = OPEN) -> Text:
    text = Text()
    rows = small_mascot_rows(face)
    for i, line in enumerate(rows):
        for ch in line:
            style = HANDLE if i == 0 or (i == 1 and ch == "┴") else CASE
            if i == 2 and ch in "●─‿◡o":
                style = EYE
            text.append(ch, style=style)
        if i < len(rows) - 1:
            text.append("\n")
    return text


# 3-line, 7-wide icons per tool key. Plain box-drawing/block characters: no emoji (width differs by terminal).
ICONS: dict[str, tuple[str, str, str]] = {
    "export":    ("┌─┐ ▪▪ ", "│▣│▶▪▪ ", "└─┘ ▪▪ "),
    "convert":   ("┌─┐ ┌─┐", "│A│⇄│B│", "└─┘ └─┘"),
    "cutout":    ("┌╌╌╌╌┐ ", "╎ ◖◗ ╎ ", "└╌╌╌╌┘ "),
    "watermark": ("▒▒▒▒▒▒▒", "▒ ◈◈  ▒", "▒▒▒▒▒▒▒"),
    "metadata":  (" ┌──┐  ", "<│ i│  ", " └──┘  "),
    "compress":  ("▐█▌ ▐▌ ", "▐█▌▶▐▌ ", "▐█▌ ▐▌ "),
    "enhance":   ("  ✦    ", " ✦▓✦ ✧ ", "  ✦    "),
    "pdf":       ("┌────┐ ", "│PDF▟│ ", "└────┘ "),
    "tokens":    ("▐█▌▐▓▌▐░▌", "▐█▌▐▓▌▐░▌", "▐█▌▐▓▌▐░▌"),
    "exact":     ("├─────┤", "│ 500 │", "├─────┤"),
    "kits":      ("▫▫▫▫▫▫▫", "▫◻◼◻◼▫▫", "▫▫▫▫▫▫▫"),
    "helpers":   ("▛▀▜ Aa ", "▌▫▐ ─── ", "▙▄▟ ─── "),
}
DEFAULT_ICON = ("┌─────┐", "│  ?  │", "└─────┘")


def icon_rows(key: str) -> tuple[str, str, str]:
    rows = ICONS.get(key, DEFAULT_ICON)
    width = max(len(r) for r in rows)
    return tuple(r.ljust(width) for r in rows)  # type: ignore[return-value]
