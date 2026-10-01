# mysuite

A CLI suite of tools for repetitive design/asset work. Phase 1: batch logo/asset export.
Phase 2: an interactive terminal dashboard (`mysuite tui`) on top of it.

## Setup

```bash
brew install librsvg imagemagick ghostscript exiftool c2patool python@3.11
brew install mozjpeg webp libavif oxipng pngquant gifsicle   # compress tool's codecs

cd ~/Documents/git/mysuite
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install ".[dev]"

.venv/bin/mysuite doctor
```

`mysuite cutout` needs one more tool, `mysuite-cutout` — a small helper built from source (it's
mysuite-specific, not brew-installable) that wraps macOS's Vision framework
(`VNGenerateForegroundInstanceMaskRequest`, the same on-device subject-isolation tech behind
Preview/Photos "Copy Subject"). Requires macOS 14+ and the Xcode Command Line Tools
(`xcode-select --install` if you don't already have them):

```bash
mysuite/native/cutout/build.sh /opt/homebrew/bin   # builds and installs onto PATH in one step
```

Note: this installs `mysuite` normally, **not** in editable (`-e`) mode. On this machine, files
`pip` writes get tagged with macOS's provenance-tracking flag, which also marks them hidden —
Python's `site.py` silently skips hidden `.pth` files, which is exactly what editable installs
rely on, so `-e` breaks with `ModuleNotFoundError: No module named 'mysuite'` here. A plain install
copies real files into `site-packages` instead of using a `.pth` redirect, sidestepping the whole
issue. The tradeoff: after changing code under `mysuite/`, re-run `.venv/bin/pip install ".[dev]"`
to pick up the change before using `.venv/bin/mysuite ...` again (`pytest` doesn't need this — it
always imports the live source directly).

`mozjpeg` is keg-only (Homebrew won't link it onto PATH — it would shadow the `cjpeg` that ships
with plain `jpeg-turbo`), so `mysuite compress --codec mozjpeg` points at mozjpeg's keg path
directly (`/opt/homebrew/opt/mozjpeg/bin/cjpeg`) rather than relying on PATH; no extra linking step
needed. `mysuite doctor` reports it under the `cjpeg` row.

## Usage

```bash
.venv/bin/mysuite export logo.svg \
  --sizes 16,32,64,128,256,512,1024 \
  --formats png,pdf,eps,svg \
  --profiles rgb,cmyk \
  --out ./exports
```

Supported formats: `png`, `pdf`, `eps`, `svg`, `jpeg`, `webp`, `tiff`, `ico`, `icns`. `ico` and
`icns` are *bundles* — all requested sizes are packed into a single `.ico`/`.icns` file rather than
one file per size (`icns` always builds the full standard macOS iconset — 16 through 1024px —
regardless of `--sizes`, since Apple's `iconutil` requires that exact set). `icns` needs macOS's
built-in `iconutil`, which isn't installed by the `brew` step above (it's already on any Mac).

Extra controls:

```bash
.venv/bin/mysuite export logo.svg --formats jpeg \
  --background white --quality 85 --padding 10% --png-compression 9
```

- `--background <color>` — flattens transparency onto a solid color (e.g. `white`, `#336699`) for
  formats without a native transparency, before writing. JPEG defaults to white automatically if
  you don't set this (JPEG can't hold alpha at all); everything else stays transparent unless asked.
- `--quality 0-100` — JPEG/WebP compression quality.
- `--margin <px|%>` (alias `--padding`) — safe-space whitespace added around the artwork (e.g.
  `20px` or `10%` of each size). No background color is forced — the margin stays transparent
  unless `--background` is also set. Raster formats only (png/jpeg/webp/tiff/ico/icns) — vector
  formats are unaffected. Override a single side with `--margin-top`/`--margin-right`/
  `--margin-bottom`/`--margin-left`, e.g. `--margin 10% --margin-top 5px` uses 5px on top and 10%
  everywhere else.
- `--png-compression 0-9` — PNG zlib compression level. Lossless either way, just trades encode
  time for file size.

Sizes can also be given in physical units — `mm`, `cm`, or `in` — alongside plain pixel numbers,
converted to pixels via `--dpi`/`--ppi` (default 300, both flags are the same thing):

```bash
.venv/bin/mysuite export logo.svg --sizes 16,32,5cm,2in --dpi 300 --formats pdf,png
```

Filenames use the size exactly as you typed it (`logo_5cm.pdf`), not the resolved pixel count —
use the `{size_px}` naming-template token instead of `{size}` if you'd rather see pixels in
filenames. DPI only affects raster output (PNG) and the pixel-equivalent used for sorting; PDF/EPS
pages are sized in real physical units regardless of DPI.

If you're mostly working in one unit, set a default instead of typing the suffix every time:

```bash
.venv/bin/mysuite export logo.svg --sizes 50,80 --unit mm    # same as --sizes 50mm,80mm
```

`--unit` (px/mm/cm/in, also configurable as `default_unit` in `mysuite.toml`) only applies to sizes
without their own explicit suffix — `--sizes 50,80,32px --unit mm` still exports `32px` as pixels.

Preview without writing anything:

```bash
.venv/bin/mysuite export logo.svg --dry-run
```

Only PDF, EPS, and TIFF support true CMYK — TIFF is the one *raster* format that does, unlike PNG.
Every other format (PNG, SVG, JPEG, WebP, ICO, ICNS) has no CMYK concept, so `--profiles rgb,cmyk`
combined with those formats skips the CMYK variant with a warning instead of writing a broken or
misleading file. Pass `--strict` to make that a hard error instead.

Output defaults to a format-first layout:

```
exports/<name>/<format>/<colorspace>/<name>_<size>.<ext>
```

Copy `mysuite.toml.example` to `mysuite.toml` (project dir or `~/.config/mysuite/mysuite.toml`)
to set your own defaults and `[presets.NAME]` bundles, selectable with `--preset NAME`.

## Batch input

Point `export` at multiple files and/or a folder instead of one SVG at a time — every file gets
the same flags, its own output tree named after its own filename:

```bash
.venv/bin/mysuite export ./logos/                       # every *.svg directly inside the folder
.venv/bin/mysuite export ./logos/ --recursive             # include subfolders too
.venv/bin/mysuite export logo-a.svg logo-b.svg icons/     # mix explicit files and folders
```

`--name` only works with a single input file (there's no single name to give multiple outputs).
If two resolved files would produce the same output name (e.g. `brand/icon.svg` and
`social/icon.svg` both stem to `icon`), the run stops with an error up front rather than letting
one silently overwrite the other — rename one of them or process them in separate runs.

## Naming

By default the output name comes from each input file's own filename. Override it for a one-off
export (single input only — see above):

```bash
.venv/bin/mysuite export logo.svg --name acme-brand   # exports/acme-brand/... instead of exports/logo/...
```

Add today's date to filenames so exports stay findable/sortable by when they were generated:

```bash
.venv/bin/mysuite export logo.svg --date-stamp   # logo_512_20260819.png
```

This only changes the tool's own *default* naming templates — a `naming_template`/
`bundle_naming_template` you've already customized (in `mysuite.toml` or a preset) is left exactly
as you wrote it, since `{date}` is available as a token there too if you want to place it yourself.

## Variants & recoloring

`--variant` nests an export under the *same* name folder as the primary one, instead of getting
its own top-level folder — useful for a negative, mono, or minimal version of the same brand asset:

```bash
.venv/bin/mysuite export logo.svg --formats png                              # exports/logo/png/...
.venv/bin/mysuite export logo.svg --formats png --variant negative           # exports/logo/negative/png/...
```

`--recolor FROM=TO` (repeatable) swaps an exact hex color in the SVG source before rendering —
handy for producing that negative/mono variant, or for nudging what a CMYK conversion produces.
Each occurrence can itself hold several `FROM=TO` pairs, separated by commas and/or spaces in any
mix, so these two invocations are equivalent:

```bash
.venv/bin/mysuite export logo.svg --variant negative \
  --recolor "#2b6cb0=#000000" --recolor "#f6ad55=#ffffff"

.venv/bin/mysuite export logo.svg --variant negative \
  --recolor "#2b6cb0=#000000 #f6ad55=#ffffff"
```

The TUI's single Recolor field works the same way — type `#2b6cb0=#000000 #f6ad55=#ffffff`,
space- or comma-separated, into one box.

This is a literal `#hex` text substitution, not full SVG/CSS color parsing — it matches
`fill="#hex"`/`style="fill:#hex"`/`<style>` blocks written as exact hex tokens, not named colors
(`red`) or `rgb()`/`hsl()` functional notation. It only ever touches the temporary copy used for
rendering — your source SVG is never modified. Save a recurring brand colorset as a preset so you
don't retype it:

```bash
.venv/bin/mysuite preset save acme-negative --variant negative \
  --recolor "#2b6cb0=#000000" --recolor "#f6ad55=#ffffff"
.venv/bin/mysuite export logo.svg --preset acme-negative
```

Note: this covers *simple* recoloring — swapping one RGB/hex value for another. It doesn't yet
guarantee an *exact* CMYK output value for a color (Ghostscript's automatic RGB→CMYK conversion can
still produce a muddier result than a manually-tuned brand CMYK value); that's a planned follow-up.

## Presets

Two ready-made presets work even without a `mysuite.toml`:

```bash
.venv/bin/mysuite export logo.svg --preset favicon      # favicon.ico (16/32/48) + individual PNGs
.venv/bin/mysuite export logo.svg --preset macos-icon    # a complete .icns app icon
```

Save your own from any combination of export flags — this writes (or updates) a `[presets.NAME]`
block in `mysuite.toml`, preserving everything else already in the file:

```bash
.venv/bin/mysuite preset save mybrand --sizes 24,48,5cm --formats png,webp --quality 88 --background white
.venv/bin/mysuite preset list
.venv/bin/mysuite export logo.svg --preset mybrand
```

A same-named preset in your own `mysuite.toml` overrides the built-in one.

## Convert

A general-purpose "duplicate and convert" tool — separate from `export`, which is SVG-specific and
size/branding-focused. `convert` takes SVG, PDF, EPS, or common raster formats and writes the result
**beside the source** (same folder, same stem, new extension) rather than into a chosen output
directory:

```bash
.venv/bin/mysuite convert logo.svg --to png,pdf,webp       # logo.png, logo.pdf, logo.webp next to logo.svg
.venv/bin/mysuite convert scan.pdf --to png --dpi 150
.venv/bin/mysuite convert photo.jpg --to webp --quality 80
```

Supported targets: `png`, `jpeg`, `webp`, `tiff`, `bmp`, `gif`, `pdf`, `eps`, `ico`. SVG is
input-only (vectorization is a different problem this tool doesn't attempt), and `icns` stays
`export`'s job (a fixed 7-size Apple iconset bundle, not a one-file-in/one-file-out conversion).
`--quality`/`--dpi`/`--background` mean the same thing as `export`'s equivalents; `--dpi` only
matters when rasterizing a vector source. `--overwrite`/`--recursive`/`--dry-run`/`--config` work
the same way as `export` too.

## Cutout

Isolates a photo's subject and writes a transparent-background PNG, using macOS's on-device Vision
framework (`VNGenerateForegroundInstanceMaskRequest` — the same tech behind Preview/Photos/Safari's
"Copy Subject"). Needs the `mysuite-cutout` helper built separately — see Setup above.

```bash
.venv/bin/mysuite cutout photo.jpg              # writes photo_cutout.png beside it
.venv/bin/mysuite cutout ./product-photos/ --recursive --overwrite
```

Output is always PNG (transparency needs an alpha channel) with a `_cutout` suffix, so it never
collides with the source regardless of its original format. SVG/PDF/EPS sources are rasterized
first (same rsvg-convert/Ghostscript pipeline `export`/`convert` already use) before being handed to
Vision. `--overwrite`/`--recursive`/`--dry-run`/`--config` work the same way as `export`/`convert`.
This does one thing — general subject isolation, no per-instance selection or replacement
background yet — matching everyday "clean this product photo up" use.

## Watermark

Stamps a logo onto image(s) as a visible, scalable watermark — pure ImageMagick compositing, no new
dependency:

```bash
.venv/bin/mysuite watermark photo.jpg --logo brand-mark.png
.venv/bin/mysuite watermark ./gallery/ --logo brand-mark.svg --position top-left --scale 20 --opacity 60
```

Writes `photo_watermarked.jpg` beside the source (raster sources keep their original extension;
`svg`/`pdf`/`eps` sources become `.png`, since watermarking is a raster compositing step). The logo
is resized to `--scale`% of the base image's width (default 15%) before compositing, so it scales
sensibly across output sizes rather than staying a fixed pixel size — and can itself be an SVG,
rasterized the same way as elsewhere in the suite. `--position` is one of the 9-point grid:
`top-left`, `top-center`, `top-right`, `center-left`, `center`, `center-right`, `bottom-left`,
`bottom-center`, `bottom-right` (default `bottom-right`). `--opacity` (default 80) and `--margin`
(breathing room from the edge, % of width, default 3) round it out.
`--overwrite`/`--recursive`/`--dry-run`/`--config` work the same way as the other tools.

## Metadata

Two opposite operations sharing one command group — strip everything for privacy, or embed a signed
provenance record for correct crediting, the same C2PA Content Credentials standard OpenAI/Adobe/
Google use on AI-generated images:

```bash
.venv/bin/mysuite metadata strip photo.jpg                                    # photo_stripped.jpg
.venv/bin/mysuite metadata credit photo.jpg --author "Jane Doe" --copyright "© 2026 Jane Doe"
```

`strip` shells out to `exiftool -all=`, removing standard EXIF/IPTC/XMP/ICC metadata (camera info,
GPS, author fields, ...) into a new file — the source is never mutated. Note this doesn't necessarily
remove a C2PA manifest a prior `credit` run embedded — that's a distinct segment exiftool's metadata
model doesn't fully own.

`credit` shells out to `c2patool`, embedding a signed manifest with `--author`
(required), `--copyright` (optional), and `--generator` (defaults to `mysuite`). It signs with
c2patool's **built-in test certificate** — real enough to embed and read back a provenance record
for personal/internal verification (`c2patool photo_credited.jpg` reads it back), but **not
third-party-trusted** — that needs a certificate from an accredited CA, which is your own separate
step if you ever want one, not something this tool obtains for you.
`--overwrite`/`--recursive`/`--dry-run`/`--config` work the same way as the other tools, applied
per-subcommand (`mysuite metadata strip --dry-run ...` / `mysuite metadata credit --dry-run ...`).

## Compress

Re-encodes image(s) via the same best-in-class, specialized codecs Squoosh.app itself uses under the
hood — not ImageMagick's generic writers — with full per-codec parameter control, plus an optional
unsharp-mask sharpen pass applied before encoding:

```bash
.venv/bin/mysuite compress photo.png --codec mozjpeg --quality 85
.venv/bin/mysuite compress ./gallery/ --codec webp --quality 80 --method 6 --sharpen-amount 1.2
.venv/bin/mysuite compress icon.png --codec oxipng --effort 6
```

Writes `photo_compressed.<ext>` beside the source. `--codec` picks the encoder (each a distinct,
real tool, not a generic fallback):

| codec | tool | good for |
| --- | --- | --- |
| `mozjpeg` | `cjpeg` | JPEG photos — meaningfully smaller than ImageMagick's own JPEG writer at the same visual quality |
| `webp` | `cwebp` | modern lossy/lossless web images |
| `avif` | `avifenc` | best compression ratio of the bunch, slower to encode |
| `oxipng` | `oxipng` | lossless PNG re-optimization (no visual change) |
| `pngquant` | `pngquant` | lossy PNG — quantizes to a palette for a much smaller file |
| `gifsicle` | `gifsicle` | GIF optimization |

`--quality` (0-100) applies to mozjpeg/webp/avif; pngquant uses its own `--quality-range` (e.g.
`65-90`) instead, since it quantizes to a range rather than a single target. Each codec exposes its
own real parameters — `--progressive`/`--subsample` (mozjpeg), `--lossless`/`--method`/
`--alpha-quality` (webp), `--speed` (avif), `--effort`/`--interlace` (oxipng), `--speed`/`--dither`
(pngquant, via `--pngquant-speed`), `--optimize-level`/`--lossy` (gifsicle) — see `--help` for the
full list. A source that isn't in the format its chosen encoder needs natively (mozjpeg's `cjpeg`
only ever accepts uncompressed PPM, for instance) is converted first via `magick`, the same
rasterize-then-convert pattern used throughout the suite for vector sources.

`--sharpen-amount` (unset by default = no sharpening) applies an ImageMagick unsharp mask
(`--sharpen-radius`/`--sharpen-sigma`/`--sharpen-threshold` round out full control over it) before
handing off to the codec — resize is deliberately out of scope here, Export/Convert already own
that.
`--overwrite`/`--recursive`/`--dry-run`/`--config` work the same way as the other tools.

## Terminal dashboard

```bash
.venv/bin/mysuite tui
```

Opens an interactive home screen listing the available tools — **Export**, **Convert**, **Cutout**,
**Watermark**, **Metadata**, **Compress** today, each with its own accent color so it's obvious
which one you're in; more (`palette`, `sort`) will show up here as they're added, no navigation
changes needed. Press a number key to jump straight to a tool, or arrow keys + Enter. The Export
screen mirrors every CLI flag as a two-column form, fields grouped into titled panels (Source,
Adjustments, Formats & color, Output, Options) rather than one long list — including a **Unit**
dropdown (px/mm/cm/in) right next to Sizes, so bare numbers use whatever unit you've picked without
typing the suffix every time, and a **Recolor** section with dedicated FROM/TO fields per swap (hex
or CSS color names) plus +/− buttons, rather than one syntax-heavy text field. Convert/Cutout/
Watermark use the same form/preview/run shape, scaled down to what each actually needs (no
sizes/formats/quality concepts for Cutout, for instance); Metadata adds a **Mode** toggle
(Strip/Credit) that shows or hides the Author/Copyright/Generator fields depending which mode is
selected; Compress adds a **Codec** dropdown that shows only the six mozjpeg/webp/avif/oxipng/
pngquant/gifsicle option groups relevant to whichever codec is selected.
Keyboard shortcuts are shown in the footer: **F5** preview, **Ctrl+R** run, **Ctrl+S** save the
current Export form as a named preset (writes into `mysuite.toml` right from the TUI, no editing by
hand). Every screen's input field also takes a folder, a comma-separated list of files, or a file
dragged straight onto the terminal window (most terminals turn an OS file-drop into a paste of its
path) — the preview shows one branch/pair per file when there's more than one. Every **Browse**
button opens a picker rooted at the filesystem root (`/`), not the project folder, so any file
anywhere on disk is reachable by drilling down. The equivalent CLI commands keep working unchanged
for scripting — the TUI is additive, not a replacement.

## Tests

```bash
.venv/bin/pytest -m "not slow"   # fast unit tests, no external binaries required
.venv/bin/pytest                 # full suite, requires rsvg-convert/gs/magick
```

A handful of tests skip individually rather than needing a marker override: icns-bundle tests need
macOS's `iconutil` (always present on a Mac, so effectively always run there); `cutout`'s real-run
tests need the separately-built `mysuite-cutout` helper (see Setup above); `metadata credit`'s tests
need `c2patool`; each `compress` codec's tests need that codec's own tool (mozjpeg/webp/libavif/
oxipng/pngquant/gifsicle) and skip independently, so a partial install still runs everything it can.
All skip cleanly with a clear reason if their tool isn't found, rather than failing.
