"""The animated front-page scene: drifting file-type tags in three parallax layers, the suitcase bobbing and
blinking in the middle, and its speech bubble typing out tips. One widget draws the whole picture, so every
element stays aligned whatever the terminal size."""
from __future__ import annotations

import math
import random

from rich.text import Text
from textual.widgets import Static

from mysuite.tui import art

TAGS = ["PNG", "PDF", "SVG", "ICO", "JPG", "WEBP", "TIFF", "EPS", "CMYK", "RGB", "@2x", "16", "64", "512", "1024",
        "ICNS", "AVIF", "GIF", "#dd0000", "300dpi", "A4", "1:1", "16:9", "▫", "▪", "·", "✦", "+"]
LAYERS = [  # (colour, speed in rows per tick, share of particles)
    ("#262B3B", 0.05, 0.45),
    ("#38415A", 0.10, 0.35),
    ("#5B6A93", 0.17, 0.20),
]
ACCENTS = ["#7DD3FC", "#C4B5FD", "#FCA5A5", "#5EEAD4", "#93C5FD", "#FDE047", "#F9A8D4"]
FPS = 12


class Scene(Static):
    DEFAULT_CSS = "Scene { width: 100%; height: 100%; }"

    def __init__(self, tips: list[str], hint: str = "press any key to open the toolbox", **kwargs) -> None:
        super().__init__("", **kwargs)
        self.tips = tips
        self.hint = hint
        self._rng = random.Random(7)
        self._particles: list[list] = []
        self._grid_size = (0, 0)
        self.tick_count = 0
        self._tip = 0
        self._typed = 0

    # ----------------------------------------------------------------- lifecycle
    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.advance)

    def _spawn(self, w: int, h: int) -> None:
        count = max(12, (w * h) // 90)
        self._particles = []
        for _ in range(count):
            roll, acc = self._rng.random(), 0.0
            layer = 0
            for i, (_, _, share) in enumerate(LAYERS):
                acc += share
                if roll <= acc:
                    layer = i
                    break
            colour, speed, _ = LAYERS[layer]
            if layer == 2 and self._rng.random() < 0.35:
                colour = self._rng.choice(ACCENTS)
            self._particles.append([
                self._rng.uniform(0, w), self._rng.uniform(0, h), speed * self._rng.uniform(0.8, 1.2),
                self._rng.choice(TAGS), colour, self._rng.uniform(0, 6.28), self._rng.uniform(0.5, 2.0),
            ])
        self._grid_size = (w, h)

    # ------------------------------------------------------------------ animation
    def advance(self) -> None:
        w, h = self.size.width, self.size.height
        if w < 20 or h < 8:
            return
        if (w, h) != self._grid_size:
            self._spawn(w, h)
        self.tick_count += 1
        for p in self._particles:
            p[1] -= p[2]
            p[0] += math.sin(self.tick_count / 40 + p[5]) * 0.06 * p[6]
            if p[1] < -1:
                p[1] = h + self._rng.uniform(0, 3)
                p[0] = self._rng.uniform(0, w)
        tip = self.tips[self._tip % len(self.tips)]
        if self._typed < len(tip):
            self._typed += 1
        elif self.tick_count % (FPS * 5) == 0:       # new tip every ~5 s once the last one is fully typed
            self._tip += 1
            self._typed = 0
        self.update(self.compose_frame(w, h))

    def face(self) -> tuple[str, str, str]:
        tip = self.tips[self._tip % len(self.tips)]
        if self._typed < len(tip):
            return art.FACES["talk"] if self.tick_count % 4 < 2 else art.FACES["open"]
        phase = self.tick_count % 40
        if phase in (38, 39):
            return art.FACES["wink"] if self._tip % 2 else art.FACES["blink"]
        return art.FACES["open"]

    # ------------------------------------------------------------------- drawing
    def compose_frame(self, w: int, h: int) -> Text:
        grid: list[list[tuple[str, str | None]]] = [[(" ", None)] * w for _ in range(h)]

        def put(x: int, y: int, ch: str, style: str | None) -> None:
            if 0 <= y < h and 0 <= x < w:
                grid[y][x] = (ch, style)

        for x, y, _speed, tag, colour, *_ in sorted(self._particles, key=lambda p: p[2]):
            for i, ch in enumerate(tag):
                put(int(x) + i, int(y), ch, colour)

        rows = art.big_mascot_rows(self.face())
        bob = 1 if (self.tick_count // 15) % 2 else 0
        x0 = (w - art.BIG_W) // 2
        y0 = max(3, (h - len(rows)) // 2 - 2) + bob
        shadow_y = y0 + len(rows) - bob
        for i in range(22):                                              # soft ground shadow
            put(x0 + 6 + i, shadow_y, "░", "#1D2130")
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                opaque = 2 <= r <= 8
                if ch != " " or opaque:
                    put(x0 + c, y0 + r, ch, art._big_style(ch, r))

        title = "m y s u i t e"
        for i, ch in enumerate(title):
            put((w - len(title)) // 2 + i, y0 - 2, ch, "bold #E8E8E8")
        sub = "your pocket image toolbox"
        for i, ch in enumerate(sub):
            put((w - len(sub)) // 2 + i, y0 - 1, ch, "#8B93A7")

        tip = self.tips[self._tip % len(self.tips)][: self._typed]
        bubble = f"( {tip}{'▌' if self._typed < len(self.tips[self._tip % len(self.tips)]) else ' '} )"
        for i, ch in enumerate(bubble):
            put((w - len(bubble)) // 2 + i, y0 + len(rows) + 2, ch, "bold #E8E8E8")

        if (self.tick_count // 8) % 2 == 0:
            hint = self.hint
            for i, ch in enumerate(hint):
                put((w - len(hint)) // 2 + i, h - 2, ch, "#8B93A7")

        text = Text(no_wrap=True, overflow="crop")
        for y, row in enumerate(grid):
            run, style = "", row[0][1]
            for ch, st in row:
                if st == style:
                    run += ch
                else:
                    text.append(run, style=style)
                    run, style = ch, st
            text.append(run, style=style)
            if y < h - 1:
                text.append("\n")
        return text
