"""A folder of test files made entirely by this program (so they are ours to give away: dedicated CC0).

`mysuite testcases make` writes it. Everything is generated from a fixed seed - no real photos, people, brands or places.
Each file exists to exercise something that has gone wrong (or could) in real use: orientation and GPS in a phone photo,
CMYK and 16-bit data, transparency, animated formats, truncated and fake images, awkward file names, SVGs with colours in
every notation, PDFs with and without text/metadata/passwords, a screenshot with text and a QR code, and textured pictures
for the ownership mark and the shield.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SEED = 20261002


@dataclass
class Item:
    path: str
    description: str
    exercises: list[str]
    needs: list[str] = field(default_factory=list)       # external programs this item needs


# ------------------------------------------------------------------------------ picture makers
def _rng(tag: str) -> np.random.Generator:
    return np.random.default_rng(int.from_bytes(hashlib.sha256(f"{SEED}-{tag}".encode()).digest()[:8], "big"))


def fractal(h: int, w: int, tag: str, octaves: int = 5, persistence: float = 0.55) -> np.ndarray:
    """Smooth natural-looking noise in 0..1 (value noise, summed octaves)."""
    rng = _rng(tag)
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 2 ** (o + 2)
        grid = rng.random((cells + 1, max(2, round(cells * w / h)) + 1))
        layer = np.asarray(Image.fromarray((grid * 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC), float) / 255
        out += amp * layer
        total += amp
        amp *= persistence
    out /= total
    return (out - out.min()) / (np.ptp(out) + 1e-9)


def colourise(field_: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    xs = [s[0] for s in stops]
    chans = [np.interp(field_, xs, [s[1][c] for s in stops]) for c in range(3)]
    return np.stack(chans, axis=-1)


def landscape(w: int, h: int, tag: str) -> Image.Image:
    sky = colourise(np.linspace(0, 1, h)[:, None] * np.ones((1, w)), [(0, (60, 110, 190)), (0.55, (200, 215, 235)), (1, (250, 220, 190))])
    hills = fractal(h, w, tag + "h", 4)
    ridge = (np.linspace(0, 1, h)[:, None] > 0.55 + 0.25 * (hills - 0.5)).astype(float)
    ground = colourise(fractal(h, w, tag + "g", 6), [(0, (40, 70, 35)), (0.5, (90, 120, 55)), (1, (160, 150, 90))])
    img = sky * (1 - ridge[..., None]) + ground * ridge[..., None]
    grain = _rng(tag + "n").normal(0, 3, (h, w, 1))
    return Image.fromarray(np.clip(img + grain, 0, 255).astype(np.uint8))


def texture_photo(w: int, h: int, tag: str, palette: str = "warm") -> Image.Image:
    stops = {
        "warm": [(0, (30, 20, 40)), (0.4, (160, 70, 50)), (0.7, (230, 170, 90)), (1, (250, 240, 200))],
        "cool": [(0, (10, 25, 50)), (0.5, (50, 110, 150)), (1, (200, 230, 240))],
        "green": [(0, (15, 35, 20)), (0.5, (80, 130, 60)), (1, (210, 225, 150))],
    }[palette]
    f = fractal(h, w, tag, 6)
    detail = fractal(h, w, tag + "d", 3, 0.8)
    img = colourise(0.8 * f + 0.2 * detail, stops)
    img += _rng(tag + "n").normal(0, 4, (h, w, 1))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def subject_on_busy_background(w: int = 900, h: int = 700) -> Image.Image:
    bg = texture_photo(w, h, "busybg", "green").filter(ImageFilter.GaussianBlur(1.5))
    d = ImageDraw.Draw(bg)
    cx, cy = w // 2, h // 2 + 30
    d.ellipse([cx - 170, cy - 120, cx + 170, cy + 200], fill=(205, 70, 60))           # body
    d.ellipse([cx - 90, cy - 250, cx + 90, cy - 70], fill=(235, 190, 150))            # head
    d.rectangle([cx - 150, cy + 190, cx - 110, cy + 300], fill=(60, 60, 110))
    d.rectangle([cx + 110, cy + 190, cx + 150, cy + 300], fill=(60, 60, 110))
    return bg


def scratched_scan(w: int = 720, h: int = 480) -> Image.Image:
    base = landscape(w, h, "scan").convert("L")
    sepia = colourise(np.asarray(base, float) / 255, [(0, (35, 22, 12)), (0.6, (170, 130, 85)), (1, (245, 225, 185))])
    rng = _rng("scratch")
    img = Image.fromarray(np.clip(sepia + rng.normal(0, 8, (h, w, 1)), 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for x in rng.integers(40, w - 40, 5):
        d.line([(int(x), 0), (int(x) + int(rng.integers(-6, 6)), h)], fill=(250, 245, 235), width=1)
    for _ in range(40):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        d.ellipse([x, y, x + 2, y + 2], fill=(30, 25, 20))
    return img


def night_shot(w: int = 800, h: int = 600) -> Image.Image:
    f = fractal(h, w, "night", 5)
    img = colourise(f * 0.35, [(0, (3, 4, 12)), (1, (40, 45, 80))])
    rng = _rng("lights")
    pts = rng.integers(0, (w, h), (60, 2))
    layer = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(layer)
    for x, y in pts:
        c = tuple(int(v) for v in rng.choice([(255, 220, 140), (255, 255, 255), (255, 120, 80)]))
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=c)
    layer = layer.filter(ImageFilter.GaussianBlur(3))
    img = np.clip(img + np.asarray(layer, float) * 1.2 + rng.normal(0, 7, (h, w, 1)), 0, 255)
    return Image.fromarray(img.astype(np.uint8))


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                    # very old Pillow
        return ImageFont.load_default()


def text_screenshot() -> Image.Image:
    import segno

    im = Image.new("RGB", (900, 600), (245, 246, 248))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 900, 46], fill=(40, 44, 52))
    d.text((16, 10), "Invoice 4711 - Acme Test GmbH", fill=(255, 255, 255), font=font(24))
    for i, line in enumerate(["Customer: Max Mustermann", "Total: 1.234,50 EUR", "Due date: 2026-12-31", "Reference: TEST-0001"]):
        d.text((24, 80 + i * 44), line, fill=(30, 30, 30), font=font(28))
    buf = io.BytesIO()
    segno.make("https://example.com/pay?ref=TEST-0001", error="m").save(buf, kind="png", scale=6, border=2)
    qr = Image.open(buf).convert("RGB")
    im.paste(qr, (900 - qr.width - 30, 600 - qr.height - 30))
    return im


# ---------------------------------------------------------------------------------- logos (SVG text)
NS = 'xmlns="http://www.w3.org/2000/svg"'
LOGOS: dict[str, tuple[str, str, list[str]]] = {
    "logo_flat.svg": (f'<svg {NS} width="240" height="120" viewBox="0 0 240 120"><rect width="120" height="120" fill="#dd0000"/><circle cx="180" cy="60" r="50" fill="#0057b8"/></svg>',
                      "Two flat colours; the basic export/recolor/CMYK case.", ["export", "recolor", "cmyk", "kit"]),
    "logo_colour_notations.svg": (f'<svg {NS} width="300" height="100" viewBox="0 0 300 100"><style>.a{{fill:red}} .b{{fill:hsl(210,100%,36%)}}</style>'
                                  '<rect class="a" width="60" height="100"/><rect class="b" x="60" width="60" height="100"/><rect x="120" width="60" height="100" fill="#d00"/>'
                                  '<rect x="180" width="60" height="100" fill="rgb(221,0,0)"/><rect x="240" width="60" height="100" style="fill:rgb(86.7%,0%,0%)"/></svg>',
                                  "The same red written six ways (name, hsl, #d00, rgb(), rgb %, style=).", ["recolor", "tokens check", "inspect"]),
    "logo_gradient.svg": (f'<svg {NS} width="200" height="100" viewBox="0 0 200 100"><defs><linearGradient id="g"><stop offset="0" stop-color="#dd0000"/><stop offset="1" stop-color="#0057b8"/></linearGradient></defs><rect width="200" height="100" fill="url(#g)"/></svg>',
                          "A gradient (CMYK converts it with Ghostscript, not the exact engine).", ["export", "cmyk notes"]),
    "logo_text.svg": (f'<svg {NS} width="260" height="90" viewBox="0 0 260 90"><text x="10" y="62" font-family="Helvetica, Arial, sans-serif" font-size="56" font-weight="bold" fill="#222628">Acme</text></svg>',
                      "Live text with a font stack: output depends on installed fonts.", ["export", "inspect fonts"]),
    "logo_no_viewbox.svg": (f'<svg {NS} width="120" height="60"><rect width="120" height="60" fill="#336699"/></svg>', "No viewBox, only width/height.", ["export", "kit"]),
    "logo_no_size.svg": (f'<svg {NS} viewBox="0 0 50 50"><circle cx="25" cy="25" r="22" fill="#2a9d8f"/></svg>', "A viewBox but no width/height.", ["export", "kit"]),
    "logo_default_fill.svg": (f'<svg {NS} viewBox="0 0 100 100"><path d="M10 10h80v80H10z M30 30v40h40V30z" fill-rule="evenodd"/></svg>',
                              "No fill anywhere (renders black): variants must give it a default fill.", ["variants", "negative", "mono-white"]),
    "logo_on_light.svg": (f'<svg {NS} width="160" height="80" viewBox="0 0 160 80"><rect width="160" height="80" fill="#1d1d1b"/><circle cx="40" cy="40" r="26" fill="#ffffff"/></svg>',
                          "Dark-on-light mark; pair with logo_on_dark.svg for the negative variant.", ["variants negative"]),
    "logo_on_dark.svg": (f'<svg {NS} width="160" height="80" viewBox="0 0 160 80"><rect width="160" height="80" fill="#ffffff"/><circle cx="40" cy="40" r="26" fill="#1d1d1b"/></svg>',
                         "The hand-made negative of logo_on_light.svg (ground truth).", ["variants negative"]),
    "logo_clip_use.svg": (f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="160" height="80" viewBox="0 0 160 80"><defs><clipPath id="c"><circle cx="40" cy="40" r="30"/></clipPath><rect id="r" width="160" height="80" fill="#e76f51"/></defs><g clip-path="url(#c)"><use xlink:href="#r"/></g></svg>',
                          "clipPath and <use>.", ["export"]),
    "logo_embedded_image.svg": (f'<svg {NS} width="100" height="100" viewBox="0 0 100 100"><image width="100" height="100" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="/></svg>',
                                "An embedded raster image.", ["export", "inspect"]),
    "logo_huge_viewbox.svg": (f'<svg {NS} width="50000" height="25000" viewBox="0 0 50000 25000"><rect width="25000" height="25000" fill="#264653"/></svg>', "A 50 000 px canvas.", ["export limits"]),
    "logo_tiny.svg": (f'<svg {NS} width="2" height="2" viewBox="0 0 2 2"><rect width="2" height="2" fill="#f4a261"/></svg>', "A 2 px logo.", ["export upscaling"]),
    "logo_external_reference.svg": (f'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/hostname">]><svg {NS} width="100" height="40"><text x="5" y="25">&x;</text></svg>',
                                    "An XML external entity: must never leak a local file.", ["security export"]),
    "logo_malformed.svg": (f'<svg {NS} width="100" height="100"><rect width="50"', "Truncated XML.", ["error handling"]),
}


def _tokens_css() -> str:
    return """/* invented tokens for tests (not any real design system) */
:root, :host {
  --color-acme-red-50: #DD0000;
  --color-acme-blue-50: #0057B8;
  --color-neutral-15: #222628;
  --color-neutral-100: #FFFFFF;
  --color-neutral-95: #F2F4F5;
  --space-4: 4px;
}
[data-color-brand="acme"], :host([data-color-brand="acme"]) {
  --text-color-brand: var(--color-acme-red-50, #DD0000);
  --text-color-primary: var(--color-neutral-15, #222628);
  --bg-color-brand-solid: var(--color-acme-blue-50, #0057B8);
}
[data-color-brand="acme"][data-theme="dark"] {
  --text-color-brand: #F75849;
  --text-color-primary: var(--color-neutral-100, #FFFFFF);
}
"""


def _tokens_json() -> dict:
    return {"brand": {"$type": "color", "red": {"$value": "#dd0000", "$extensions": {"mysuite": {"dark": "#ff6b6b"}}},
                      "blue": {"$value": "#0057b8", "$extensions": {"mysuite": {"dark": "#6bb0ff"}}}, "link": {"$value": "{brand.blue}"}},
            "size": {"gap": {"$type": "dimension", "$value": "4px"}}}


def _figma_response() -> dict:
    return {"meta": {"variableCollections": {"c1": {"name": "Brand", "defaultModeId": "m1", "modes": [{"modeId": "m1", "name": "Light"}, {"modeId": "m2", "name": "Dark"}]}},
                     "variables": {"v1": {"name": "brand/red", "resolvedType": "COLOR", "variableCollectionId": "c1",
                                          "valuesByMode": {"m1": {"r": 0.867, "g": 0, "b": 0, "a": 1}, "m2": {"r": 0.97, "g": 0.345, "b": 0.286, "a": 1}}}}}}


# ------------------------------------------------------------------------------------ the builder
class Builder:
    def __init__(self, out: Path):
        self.out = out
        self.items: list[Item] = []
        self.skipped: list[tuple[str, str]] = []

    def path(self, rel: str) -> Path:
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def add(self, rel: str, description: str, exercises: list[str], needs: list[str] | None = None) -> None:
        self.items.append(Item(rel, description, exercises, needs or []))

    def skip(self, rel: str, why: str) -> None:
        self.skipped.append((rel, why))

    # ---- photos
    def photos(self) -> None:
        # a phone-style photo: stored landscape, Orientation 6 (rotate 90 CW), fake GPS + serial
        img = landscape(1600, 1200, "phone")
        exif = Image.Exif()
        exif[271], exif[272], exif[274], exif[305] = "TestPhone Inc.", "TestPhone 1", 6, "TestOS 1.0"
        exif[306] = "2026:07:04 12:34:56"
        sub = exif.get_ifd(0x8769)
        sub[36867] = "2026:07:04 12:34:56"
        sub[0xA431] = "TESTSERIAL0001"
        sub[0xA434] = "TestPhone 4.2mm f/1.8"
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (12.0, 20.0, 42.0), "W", (45.0, 40.0, 30.0)     # a point in the Atlantic: not a real place
        img.save(self.path("photos/phone_portrait_gps.jpg"), quality=90, exif=exif)
        self.add("photos/phone_portrait_gps.jpg", "Phone photo: stored landscape with EXIF Orientation 6, fake GPS in the Atlantic, fake serial number.",
                 ["inspect", "metadata strip/randomize/apply", "watermark corner", "transform auto-orient", "cutout metadata"])
        landscape(4200, 2800, "camera").save(self.path("photos/camera_12mp.jpg"), quality=92)
        self.add("photos/camera_12mp.jpg", "A 12-megapixel camera-style JPEG.", ["speed", "compress", "enhance", "shield tiling", "export limits"])
        subject_on_busy_background().save(self.path("photos/subject_busy_background.png"))
        self.add("photos/subject_busy_background.png", "A figure on a textured green background.", ["cutout", "relight", "transform", "print"])
        scratched_scan().save(self.path("photos/scratched_scan.jpg"), quality=88)
        self.add("photos/scratched_scan.jpg", "A sepia 'old scan' with vertical scratches and dust specks.", ["enhance old-photo", "scratch removal"])
        landscape(1400, 900, "sky").crop((0, 0, 1400, 700)).save(self.path("photos/sky_and_hills.png"))
        self.add("photos/sky_and_hills.png", "Large smooth sky: flat areas show watermark/shield patterns.", ["mark", "shield visibility", "compress", "relight"])
        night_shot().save(self.path("photos/night_lights.png"))
        self.add("photos/night_lights.png", "A dark, noisy night scene with point lights.", ["enhance denoise", "compress", "mark"])
        # 16-bit grey and RGB
        g16 = (fractal(400, 600, "g16", 5) * 65535).astype(np.uint16)
        Image.fromarray(g16).save(self.path("photos/grey_16bit.png"))
        self.add("photos/grey_16bit.png", "A 16-bit greyscale PNG.", ["bit-depth notes", "convert", "enhance"])
        rgb16 = (np.stack([fractal(300, 450, f"r{c}", 5) for c in range(3)], -1) * 65535).astype(np.uint16)
        if shutil.which("magick"):
            tmp = self.path("photos/_tmp16.png")
            subprocess.run(["magick", "-size", "450x300", "-depth", "16", "gradient:red-blue", "-depth", "16", "-strip", str(tmp)], check=True, capture_output=True)
            tmp.rename(self.path("photos/rgb_16bit.png"))
            self.add("photos/rgb_16bit.png", "A 16-bit RGB PNG (ImageMagick).", ["bit-depth notes", "convert", "enhance"], ["magick"])
        else:
            self.skip("photos/rgb_16bit.png", "needs ImageMagick")
        Image.fromarray((fractal(300, 400, "gray8", 5) * 255).astype(np.uint8), "L").save(self.path("photos/grey_8bit.png"))
        self.add("photos/grey_8bit.png", "A normal 8-bit greyscale PNG.", ["watermark colour logo on grey", "convert"])
        # CMYK
        cm = texture_photo(500, 350, "cmyk", "warm").convert("CMYK")
        cm.save(self.path("photos/cmyk.tiff"), compression="tiff_lzw")
        cm.save(self.path("photos/cmyk.jpg"), quality=90)
        self.add("photos/cmyk.tiff", "A CMYK TIFF.", ["inspect colourspace", "profile to srgb", "compress", "convert"])
        self.add("photos/cmyk.jpg", "A CMYK JPEG (Adobe-style).", ["inspect", "convert", "enhance"])
        # transparency
        alpha = Image.new("RGBA", (500, 400), (0, 0, 0, 0))
        d = ImageDraw.Draw(alpha)
        d.rounded_rectangle([60, 60, 440, 340], 60, fill=(231, 111, 81, 255))
        d.ellipse([160, 130, 340, 310], fill=(244, 162, 97, 200))
        alpha.save(self.path("photos/transparent_shape.png"))
        self.add("photos/transparent_shape.png", "A PNG with hard and semi-transparent areas.", ["alpha handling", "convert to jpeg", "watermark", "transform round", "print", "kit"])
        # animated
        frames = [Image.new("RGB", (160, 120), c) for c in ((220, 40, 40), (40, 160, 70), (40, 80, 220))]
        frames[0].save(self.path("photos/animated.gif"), save_all=True, append_images=frames[1:], duration=200, loop=0)
        frames[0].save(self.path("photos/animated.webp"), save_all=True, append_images=frames[1:], duration=200, loop=0, lossless=True)
        self.add("photos/animated.gif", "Three-frame animated GIF.", ["convert notes (first frame)", "compress gifsicle", "inspect frames"])
        self.add("photos/animated.webp", "Three-frame animated WebP.", ["convert", "inspect frames", "metadata strip keeps frames"])
        # wide gamut, if this machine has a profile to embed
        for cand in (Path("/System/Library/ColorSync/Profiles/Display P3.icc"), Path("/usr/share/color/icc/colord/DisplayP3.icc")):
            if cand.exists():
                texture_photo(500, 350, "p3", "cool").save(self.path("photos/wide_gamut_p3.png"), icc_profile=cand.read_bytes())
                self.add("photos/wide_gamut_p3.png", "Pixels tagged with a Display P3 profile.", ["profile to srgb", "inspect icc"])
                break
        else:
            self.skip("photos/wide_gamut_p3.png", "no Display P3 profile on this system")

    # ---- broken and awkward files
    def broken(self) -> None:
        buf = io.BytesIO()
        landscape(600, 400, "trunc").save(buf, "JPEG", quality=90)
        self.path("broken/truncated.jpg").write_bytes(buf.getvalue()[: len(buf.getvalue()) // 2])
        self.add("broken/truncated.jpg", "A JPEG cut in half.", ["error handling in every raster tool"])
        self.path("broken/not_an_image.png").write_bytes(b"this is plain text pretending to be a png\n")
        self.add("broken/not_an_image.png", "Text with a .png name.", ["error handling"])
        self.path("broken/empty.png").write_bytes(b"")
        self.add("broken/empty.png", "A zero-byte file.", ["error handling"])
        self.path("broken/wrong_extension.jpg").write_bytes(self._png_bytes())
        self.add("broken/wrong_extension.jpg", "A PNG stored with a .jpg name.", ["format detection", "convert"])
        base = self._png_bytes()
        for name in ("file with spaces.png", "[bold]-all=.png", "Größe & Maße (1).png", "emoji 😀 name.png", "-starts-with-dash.png", "a'quote\".png", "x" * 120 + ".png"):
            self.path(f"names/{name}").write_bytes(base)
            self.add(f"names/{name}", "A valid image with an awkward file name.", ["path handling", "rich markup safety", "exiftool option safety"])

    def _png_bytes(self) -> bytes:
        buf = io.BytesIO()
        texture_photo(200, 150, "name", "cool").save(buf, "PNG")
        return buf.getvalue()

    # ---- logos and tokens
    def logos(self) -> None:
        for name, (svg, desc, ex) in LOGOS.items():
            self.path(f"logos/{name}").write_text(svg, encoding="utf-8")
            self.add(f"logos/{name}", desc, ex)
        png = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
        d = ImageDraw.Draw(png)
        d.rectangle([0, 0, 200, 200], fill=(221, 0, 0, 255))
        d.ellipse([220, 20, 380, 180], fill=(0, 87, 184, 255))
        png.save(self.path("logos/logo_raster.png"))
        self.add("logos/logo_raster.png", "The flat logo as a PNG (kits and watermark accept raster logos).", ["kit", "watermark logo"])
        if shutil.which("rsvg-convert"):
            src = self.path("logos/logo_flat.svg")
            subprocess.run(["rsvg-convert", "--format=pdf", f"--output={self.path('logos/logo_flat.pdf')}", str(src)], check=True, capture_output=True)
            subprocess.run(["rsvg-convert", "--format=ps", f"--output={self.path('logos/logo_flat.ps')}", str(src)], check=True, capture_output=True)
            (self.out / "logos/logo_flat.ps").unlink()
            self.add("logos/logo_flat.pdf", "The flat logo as a vector PDF.", ["convert", "kit", "future .ai/PDF import"], ["rsvg-convert"])
        else:
            self.skip("logos/logo_flat.pdf", "needs rsvg-convert")

    def tokens(self) -> None:
        self.path("tokens/tokens.css").write_text(_tokens_css(), encoding="utf-8")
        self.add("tokens/tokens.css", "Invented CSS-variable tokens: brand 'acme', light/dark themes, aliases.", ["tokens list/show/check", "recolor token:", "variants negative"])
        self.path("tokens/tokens.json").write_text(json.dumps(_tokens_json(), indent=2), encoding="utf-8")
        self.add("tokens/tokens.json", "W3C design tokens with dark extensions and an alias.", ["tokens"])
        self.path("tokens/figma_variables_response.json").write_text(json.dumps(_figma_response(), indent=2), encoding="utf-8")
        self.add("tokens/figma_variables_response.json", "A recorded-shape Figma Variables API response (no live Figma file was used).", ["figma parser"])

    # ---- documents
    def documents(self) -> None:
        from pypdf import PdfReader, PdfWriter

        def page_pdf(i: int, text: str, tmp: Path) -> Path:
            svg = (f'<svg {NS} width="595pt" height="842pt" viewBox="0 0 595 842"><rect width="595" height="842" fill="#ffffff"/>'
                   f'<text x="60" y="110" font-family="Helvetica, Arial, sans-serif" font-size="36" font-weight="bold" fill="#222222">Test document</text>'
                   f'<text x="60" y="170" font-family="Helvetica, Arial, sans-serif" font-size="18" fill="#444444">Page {i}: {text}</text>'
                   f'<rect x="60" y="220" width="475" height="180" fill="hsl({40 * i + 200},60%,60%)"/></svg>')
            src = tmp / f"p{i}.svg"
            src.write_text(svg)
            dst = tmp / f"p{i}.pdf"
            subprocess.run(["rsvg-convert", "--format=pdf", f"--output={dst}", str(src)], check=True, capture_output=True)
            return dst

        if shutil.which("rsvg-convert"):
            tmp = self.out / "_tmp"
            tmp.mkdir(exist_ok=True)
            w = PdfWriter()
            for i, t in enumerate(["introduction", "the middle part", "conclusion"], start=1):
                for page in PdfReader(str(page_pdf(i, t, tmp))).pages:
                    w.add_page(page)
            w.add_metadata({"/Title": "Test document", "/Author": "Test Author", "/Subject": "Generated by mysuite testcases", "/Creator": "mysuite"})
            with open(self.path("documents/text_3_pages.pdf"), "wb") as fh:
                w.write(fh)
            self.add("documents/text_3_pages.pdf", "Three text pages with a title, author and subject set.", ["pdf info/strip/extract/rotate/number/stamp/resize/split/merge", "ocr after render"], ["rsvg-convert"])
            enc = PdfWriter(clone_from=PdfReader(str(self.out / "documents/text_3_pages.pdf")))
            enc.encrypt("secret")
            with open(self.path("documents/password_protected.pdf"), "wb") as fh:
                enc.write(fh)
            self.add("documents/password_protected.pdf", "The same document, password 'secret'.", ["refusal of protected PDFs"], ["rsvg-convert"])
            for i in (1, 2):
                shutil.copyfile(tmp / f"p{i}.pdf", self.path(f"documents/single_page_{i}.pdf"))
                self.add(f"documents/single_page_{i}.pdf", f"A one-page PDF ({i} of 2) for merging.", ["pdf merge"], ["rsvg-convert"])
            shutil.rmtree(tmp)
        else:
            for n in ("text_3_pages.pdf", "password_protected.pdf", "single_page_1.pdf", "single_page_2.pdf"):
                self.skip(f"documents/{n}", "needs rsvg-convert")
        pages = [landscape(800, 1100, f"scan{i}").convert("L").convert("RGB") for i in range(3)]
        pages[0].save(self.path("documents/scanned_pages.pdf"), "PDF", save_all=True, append_images=pages[1:], resolution=150.0)
        self.add("documents/scanned_pages.pdf", "Three pages that are only pictures (no text).", ["pdf render/images/compress", "ocr"])

    def screens(self) -> None:
        text_screenshot().save(self.path("screens/invoice_text_and_qr.png"))
        self.add("screens/invoice_text_and_qr.png", "A screenshot with readable text and a QR code.", ["ocr", "qr read", "dupes", "diff"])
        im = Image.open(self.out / "screens/invoice_text_and_qr.png").convert("RGB")
        ImageDraw.Draw(im).rectangle([24, 80, 400, 120], fill=(255, 255, 255))
        im.save(self.path("screens/invoice_text_and_qr_edited.png"))
        self.add("screens/invoice_text_and_qr_edited.png", "The same screenshot with one line painted over.", ["diff", "dupes similar"])
        small = Image.open(self.out / "screens/invoice_text_and_qr.png").convert("RGB").resize((450, 300), Image.Resampling.LANCZOS)
        small.save(self.path("screens/invoice_text_and_qr_small.jpg"), quality=70)
        self.add("screens/invoice_text_and_qr_small.jpg", "A smaller, recompressed copy of the screenshot.", ["dupes (resized and recompressed copy)"])

    def textured(self) -> None:
        for i, (pal, size) in enumerate([("warm", (640, 480)), ("cool", (720, 540)), ("green", (600, 600)), ("warm", (800, 500)), ("cool", (512, 512)), ("green", (680, 460))], start=1):
            texture_photo(size[0], size[1], f"tex{i}", pal).save(self.path(f"textured/texture_{i}.png"))
            self.add(f"textured/texture_{i}.png", f"Photo-like generated texture {i} ({size[0]}x{size[1]}).", ["mark embed/detect", "shield", "relight", "enhance", "compress"])


MANIFEST_NAME = "MANIFEST.json"
LICENSE_TEXT = """CC0 1.0 Universal (public domain dedication)

Every file in this folder was generated by the program `mysuite testcases` from scratch, using a fixed random seed. It contains
no real photographs, people, brands, places or third-party designs. To the extent possible under law, the author of
mysuite has waived all copyright and related rights to these generated files worldwide.

https://creativecommons.org/publicdomain/zero/1.0/
"""


def readme_text(items: list[Item], skipped: list[tuple[str, str]]) -> str:
    lines = ["# mysuite test files", "",
             "Generated by `mysuite testcases make` (fixed seed, CC0: see LICENSE.txt). Add your own images next to these - "
             "keep your own files out of any public repository.", "",
             "Try them:", "", "```bash", "mysuite inspect photos/ --recursive --json",
             "mysuite metadata strip photos/phone_portrait_gps.jpg", "mysuite export logos/logo_flat.svg --formats png,pdf --profiles rgb,cmyk --cmyk-mode clean",
             "mysuite variants make logos/logo_on_light.svg --variants negative --tokens tokens/tokens.css --brand acme",
             "mysuite mark embed textured/texture_1.png --key YOUR-SECRET", "mysuite pdf info documents/text_3_pages.pdf",
             "mysuite ocr screens/invoice_text_and_qr.png   # macOS, needs `mysuite install vision`", "```", "", "| file | what it is | exercises |", "| --- | --- | --- |"]
    for it in items:
        lines.append(f"| `{it.path}` | {it.description} | {', '.join(it.exercises)} |")
    if skipped:
        lines += ["", "Not generated here:", ""] + [f"- `{p}`: {why}" for p, why in skipped]
    return "\n".join(lines) + "\n"


def generate(out: Path) -> tuple[list[Item], list[tuple[str, str]]]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    b = Builder(out)
    b.photos()
    b.broken()
    b.logos()
    b.tokens()
    b.documents()
    b.screens()
    b.textured()
    manifest = []
    for it in b.items:
        data = (out / it.path).read_bytes()
        manifest.append({"path": it.path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "description": it.description,
                         "exercises": it.exercises, "needs": it.needs})
    (out / MANIFEST_NAME).write_text(json.dumps({"generator": "mysuite testcases", "seed": SEED, "license": "CC0-1.0", "files": manifest,
                                                  "not_generated": [{"path": p, "reason": w} for p, w in b.skipped]}, indent=2), encoding="utf-8")
    (out / "LICENSE.txt").write_text(LICENSE_TEXT, encoding="utf-8")
    (out / "README.md").write_text(readme_text(b.items, b.skipped), encoding="utf-8")
    return b.items, b.skipped
