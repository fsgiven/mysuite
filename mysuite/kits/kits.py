"""Asset kits: one command makes a whole set of files with exact, well-known specs (favicon set, iOS/Android
app icons, social images, retina set). Deterministic: ask for a 1200x630 image and you get exactly that."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from mysuite.color.parse import parse_color
from mysuite.config import ToolPaths
from mysuite.convert._parsing import RASTER_FORMATS, detect_source_format
from mysuite.utils.subprocess_utils import atomic_write_via, run

MAX_SOURCE_PIXELS = 100_000_000


class KitError(ValueError):
    pass


@dataclass(frozen=True)
class FileSpec:
    path: str                       # relative to the kit folder, e.g. "mipmap-hdpi/ic_launcher.png"
    kind: str                       # png | ico | text | svg
    width: int = 0
    height: int = 0
    opaque: bool = False            # flatten on the background (iOS icons must have no transparency)
    padding: float | None = None    # percent of the canvas left empty around the logo (None = kit default)
    sizes: tuple[int, ...] = ()     # ico
    text: Callable[[str], str] | None = None   # text files: f(name) -> content


@dataclass(frozen=True)
class Kit:
    name: str
    description: str
    files: tuple[FileSpec, ...]
    default_padding: float = 0.0
    needs_background: bool = False   # banners need a colour behind the logo


# ------------------------------------------------------------------ kit definitions
def _manifest(name: str) -> str:
    return json.dumps({
        "name": name, "short_name": name,
        "icons": [
            {"src": "android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "theme_color": "#ffffff", "background_color": "#ffffff", "display": "standalone",
    }, indent=2) + "\n"


def _favicon_html(_name: str) -> str:
    return (
        '<link rel="icon" href="/favicon.ico" sizes="48x48">\n'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">\n'
        '<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">\n'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
        '<link rel="manifest" href="/site.webmanifest">\n'
    )


FAVICON = Kit("favicon", "Browser favicon set: favicon.ico (16/32/48), PNGs, Apple touch icon, Android icons, web manifest, HTML snippet.", (
    FileSpec("favicon.ico", "ico", sizes=(16, 32, 48)),
    FileSpec("favicon-16x16.png", "png", 16, 16),
    FileSpec("favicon-32x32.png", "png", 32, 32),
    FileSpec("apple-touch-icon.png", "png", 180, 180, opaque=True, padding=10),
    FileSpec("android-chrome-192x192.png", "png", 192, 192),
    FileSpec("android-chrome-512x512.png", "png", 512, 512),
    FileSpec("favicon.svg", "svg"),
    FileSpec("site.webmanifest", "text", text=_manifest),
    FileSpec("favicon-snippet.html", "text", text=_favicon_html),
))

# (idiom, point size, scale) from Apple's AppIcon.appiconset
_IOS = [("iphone", 20, 2), ("iphone", 20, 3), ("iphone", 29, 2), ("iphone", 29, 3), ("iphone", 40, 2), ("iphone", 40, 3),
        ("iphone", 60, 2), ("iphone", 60, 3), ("ipad", 20, 1), ("ipad", 20, 2), ("ipad", 29, 1), ("ipad", 29, 2),
        ("ipad", 40, 1), ("ipad", 40, 2), ("ipad", 76, 1), ("ipad", 76, 2), ("ipad", 83.5, 2), ("ios-marketing", 1024, 1)]


def _ios_filename(idiom: str, pts: float, scale: int) -> str:
    label = f"{pts:g}"
    return f"icon-{idiom}-{label}@{scale}x.png"


def _ios_contents(_name: str) -> str:
    images = []
    for idiom, pts, scale in _IOS:
        images.append({"size": f"{pts:g}x{pts:g}", "idiom": idiom, "filename": _ios_filename(idiom, pts, scale), "scale": f"{scale}x"})
    return json.dumps({"images": images, "info": {"version": 1, "author": "mysuite"}}, indent=2) + "\n"


IOS = Kit("ios-app-icon", "iOS AppIcon.appiconset: every iPhone/iPad size plus the 1024 App Store icon, opaque, with Contents.json.", tuple(
    FileSpec(f"AppIcon.appiconset/{_ios_filename(i, p, s)}", "png", round(p * s), round(p * s), opaque=True, padding=0)
    for i, p, s in _IOS
) + (FileSpec("AppIcon.appiconset/Contents.json", "text", text=_ios_contents),))

_ANDROID = [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)]
ANDROID = Kit("android-icons", "Android launcher icons for every density (mipmap-*/ic_launcher.png) plus the 512 px Play Store icon.", tuple(
    FileSpec(f"mipmap-{d}/ic_launcher.png", "png", px, px, padding=8) for d, px in _ANDROID
) + (FileSpec("play-store-icon-512.png", "png", 512, 512, padding=8),))

_SOCIAL = [
    ("open-graph-1200x630.png", 1200, 630), ("twitter-card-1200x675.png", 1200, 675), ("linkedin-1200x627.png", 1200, 627),
    ("facebook-cover-820x312.png", 820, 312), ("youtube-banner-2560x1440.png", 2560, 1440), ("youtube-thumbnail-1280x720.png", 1280, 720),
    ("instagram-square-1080x1080.png", 1080, 1080), ("instagram-portrait-1080x1350.png", 1080, 1350),
    ("instagram-story-1080x1920.png", 1080, 1920), ("avatar-400x400.png", 400, 400),
]
SOCIAL = Kit("social", "Social images at their exact sizes (Open Graph, Twitter, LinkedIn, Facebook, YouTube, Instagram, avatar): logo centred on your background colour.",
             tuple(FileSpec(p, "png", w, h, opaque=True) for p, w, h in _SOCIAL), default_padding=25.0, needs_background=True)

KITS: dict[str, Kit] = {k.name: k for k in (FAVICON, IOS, ANDROID, SOCIAL)}


def retina_kit(size: int) -> Kit:
    if not 1 <= size <= 4096:
        raise KitError("retina size must be between 1 and 4096")
    return Kit("retina", f"@1x/@2x/@3x PNGs at a {size}px base size.", (
        FileSpec("{name}.png", "png", size, size),
        FileSpec("{name}@2x.png", "png", size * 2, size * 2),
        FileSpec("{name}@3x.png", "png", size * 3, size * 3),
    ))


# --------------------------------------------------------------------------- rendering
class Logo:
    """A vector or raster logo that can be rendered into any canvas size."""

    def __init__(self, path: Path, tools: ToolPaths):
        self.path = Path(os.path.abspath(path))
        self.tools = tools
        kind = detect_source_format(self.path)
        if kind is None:
            raise KitError(f"unrecognized logo format: {self.path.suffix or 'no extension'}")
        self.kind = kind
        self._raster: Image.Image | None = None
        if kind in RASTER_FORMATS:
            try:
                with Image.open(self.path) as im:
                    if im.width * im.height > MAX_SOURCE_PIXELS:
                        raise KitError("logo image is too large")
                    im.seek(0)
                    self._raster = im.convert("RGBA")
            except KitError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise KitError(f"can't read {self.path.name}: {exc}") from exc

    def _svg_png(self, w: int, h: int) -> Image.Image:
        handle = tempfile.NamedTemporaryFile(suffix=".png", prefix="mysuite-kit-", delete=False)
        handle.close()
        out = Path(handle.name)
        try:
            cmd = [self.tools.rsvg_convert, "--format=png", f"--width={w}", f"--height={h}", "--keep-aspect-ratio",
                   f"--output={out}", str(self.path)]
            if self.kind != "svg":
                cmd = [self.tools.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pngalpha", "-dFirstPage=1",
                       "-dLastPage=1", "-r300", f"-sOutputFile={out}", str(self.path)]
            run(cmd)
            with Image.open(out) as im:
                return im.convert("RGBA")
        except FileNotFoundError as exc:
            raise KitError(f"{self.kind.upper()} logos need rsvg-convert / Ghostscript installed") from exc
        finally:
            out.unlink(missing_ok=True)

    def render(self, width: int, height: int, padding_pct: float, background: str | None, opaque: bool) -> Image.Image:
        """The logo, fitted inside the canvas minus padding, centred."""
        pad = padding_pct / 100
        box_w, box_h = max(1, round(width * (1 - 2 * pad))), max(1, round(height * (1 - 2 * pad)))
        if self._raster is not None:
            scale = min(box_w / self._raster.width, box_h / self._raster.height)
            size = (max(1, round(self._raster.width * scale)), max(1, round(self._raster.height * scale)))
            logo = self._raster.resize(size, Image.Resampling.LANCZOS)
        elif self.kind == "svg":
            logo = self._svg_png(box_w, box_h)
        else:
            logo = self._svg_png(box_w, box_h)
            logo.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
        fill = (0, 0, 0, 0)
        if background:
            c = parse_color(background)
            fill = (c.r, c.g, c.b, 255 if c.alpha is None else round(c.alpha * 255))
        elif opaque:
            fill = (255, 255, 255, 255)
        canvas = Image.new("RGBA", (width, height), fill)
        canvas.alpha_composite(logo, ((width - logo.width) // 2, (height - logo.height) // 2))
        return canvas.convert("RGB") if opaque or (background and fill[3] == 255) else canvas


# --------------------------------------------------------------------------- running
@dataclass
class KitFile:
    path: Path
    kind: str
    status: str                       # planned | written | skipped_existing | failed
    size: tuple[int, int] | None = None
    note: str | None = None
    error: str | None = None


@dataclass
class KitResult:
    kit: str
    out_dir: Path
    files: list[KitFile] = field(default_factory=list)


def plan(kit: Kit, out_dir: Path, name: str) -> list[tuple[FileSpec, Path]]:
    return [(spec, out_dir / spec.path.replace("{name}", name)) for spec in kit.files]


def validate_background(background: str | None) -> None:
    if background and parse_color(background) is None:
        raise KitError(f"background: not a colour: {background!r}")


def make_kit(kit: Kit, logo_path: Path, out_root: Path, *, tools: ToolPaths, name: str | None = None,
             background: str | None = None, padding: float | None = None, overwrite: bool = False,
             dry_run: bool = False) -> KitResult:
    validate_background(background)
    if padding is not None and not 0 <= padding < 50:
        raise KitError("padding must be at least 0 and below 50 (percent of the canvas)")
    logo_path = Path(os.path.abspath(logo_path))
    name = name or logo_path.stem
    out_dir = Path(os.path.abspath(out_root)) / name / kit.name if kit.name != "retina" else Path(os.path.abspath(out_root)) / name
    result = KitResult(kit.name, out_dir)
    entries = plan(kit, out_dir, name)
    if dry_run:
        result.files = [KitFile(p, s.kind, "planned", (s.width, s.height) if s.width else None) for s, p in entries]
        return result
    logo = Logo(logo_path, tools)
    for spec, path in entries:
        if path.exists() and not overwrite:
            result.files.append(KitFile(path, spec.kind, "skipped_existing"))
            continue
        # an explicit --padding wins; otherwise the file's own default, then the kit's
        pad = padding if padding is not None else spec.padding if spec.padding is not None else kit.default_padding
        note = None
        if spec.kind == "svg":
            if logo.kind != "svg":
                result.files.append(KitFile(path, "svg", "skipped_existing", note="skipped: the logo is not an SVG"))
                result.files[-1].status = "skipped"
                continue

            def write_svg(tmp: Path, src: Path = logo.path) -> None:
                shutil.copyfile(src, tmp)

            atomic_write_via(path, write_svg, preserve_extension=True)
        elif spec.kind == "text":
            content = spec.text(name) if spec.text else ""

            def write_text(tmp: Path, content: str = content) -> None:
                tmp.write_text(content, encoding="utf-8")

            atomic_write_via(path, write_text, preserve_extension=True)
        elif spec.kind == "png":
            bg = background if (spec.opaque or kit.needs_background) else background
            image = logo.render(spec.width, spec.height, pad, bg, spec.opaque)
            if kit.needs_background and not background:
                note = "no --background given: white used"

            def write_png(tmp: Path, image: Image.Image = image) -> None:
                image.save(tmp, format="PNG", optimize=True)

            atomic_write_via(path, write_png, preserve_extension=True)
        elif spec.kind == "ico":
            largest = max(spec.sizes)
            image = logo.render(largest, largest, pad, background, False)

            def write_ico(tmp: Path, image: Image.Image = image, sizes: tuple[int, ...] = spec.sizes) -> None:
                image.save(tmp, format="ICO", sizes=[(s, s) for s in sizes])

            atomic_write_via(path, write_ico, preserve_extension=True)
        else:  # pragma: no cover
            raise KitError(f"unknown file kind {spec.kind}")
        result.files.append(KitFile(path, spec.kind, "written", (spec.width, spec.height) if spec.width else None, note))
    return result
