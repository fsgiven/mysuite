# Test findings (phase 0)

In-depth testing of every tool, **no fixes made in the pass itself** (fixes followed, see below). Each finding below is pinned by an
`xfail(strict=True)` test, so the suite stays green today and fails loudly the day a bug is fixed
(that is your cue to delete the marker). Run `pytest -rx` to list them with their reasons.

**Suite now:** 369 passed, **0 expected failures**. All 22 findings are fixed (rows marked ✅).
Before this pass the suite had 142 tests and covered none of export, convert, watermark, cutout or
`metadata credit`.

How the tests work: black-box through the real CLI (`typer` runner) and the Textual pilot, against real
exiftool/ImageMagick/Ghostscript/rsvg/c2patool/Vision. Outputs are checked by reading pixels back and, for
CMYK, by decompressing the PDF content stream and reading the ink numbers it actually stores.

## Findings, by severity

| ID | Sev | Tool | What is wrong | Evidence | Proposed fix (phase) |
| --- | --- | --- | --- | --- | --- |
| ✅ D1 | **High** | export, convert, watermark, metadata | These commands refuse to run (they print the doctor table and do nothing) unless **every** tool is installed, including the macOS-only `mysuite-cutout` Swift helper and `c2patool`. A fresh clone that follows the README without building the helper cannot export a single file; it is also why CI failed on first run | Simulated config pointing those two at missing binaries: export prints "Install the missing tool(s)" and writes nothing | Check only the tools each command needs (compress already does) (quick win) |
| ✅ C1 | **High** | export | CMYK differs by format: PDF/EPS use Ghostscript, TIFF uses ImageMagick | `#dd0000` → PDF **C6 M100 Y100 K1**, TIFF **C0 M100 Y100 K34** | One CMYK engine for all formats (1) |
| ✅ C2 | **High** | export | Neutral grey becomes a noisy "rich" mix | `#222222` → PDF **69/66/65/72**; TIFF gets clean K87 | Keep greys K-only; engine (1) |
| ✅ C3 | Med-High | export | No ICC profile / output intent in CMYK PDFs, so the numbers have no defined meaning for a printer | no `OutputIntent`/`ICCBased` in the file | Embed a chosen profile (1) |
| ✅ R1 | **High** | export | Recolor misses short hex: `#d00` is not `#dd0000` | recolor matrix | Parse-based colour engine (1) |
| ✅ R2 | **High** | export | `rgb()` and `rgb(%)` are never recoloured | recolor matrix | (1) |
| ✅ R3 | **High** | export | `hsl()` is never recoloured | recolor matrix | (1) |
| ✅ R4 | Med | export | Named colours inside `<style>` blocks are missed (known, documented limit) | recolor matrix | (1) |
| ✅ R5 | Med | export | No tolerance matching: `#dc0100` is not seen as the brand red | recolor matrix | ΔE matching (1) |
| ✅ V1 | Med | convert | Animated GIF → PNG crashes with a traceback **and leaves `x.png-0.tmp`, `-1.tmp`, `-2.tmp` in the user's folder** | reproduced | Detect multi-frame, clean up temp files (fix list) |
| ✅ S2 | Med | export | A `naming_template`/path template containing `../` writes outside the output folder (config-controlled, but profiles/agents will make config less trusted) | test writes `../../esc_32.png` | Reject `..`/absolute components (3, 5) |
| ✅ W2 | Med | watermark | A colour logo on a **grayscale** base (any B&W photo) comes out as a white blob | red logo → `gray(255)` | Convert base to sRGB first |
| ✅ K2 | Med | cutout | When Vision finds no subject it exits 0 and writes an empty/mostly-transparent PNG | **3 of 20** clean synthetic subjects failed this way | Check mask coverage, warn/fail |
| ✅ K1 | Med | cutout | Passthrough keeps *all* source metadata including GPS and serial numbers into the derived file | reproduced | Make passthrough opt-in or strip GPS/serials by default |
| ✅ W1 | Low-Med | watermark | An EXIF-rotated photo gets the logo in the wrong corner (placed on unrotated pixels) | reproduced | `-auto-orient` first |
| ✅ V2 | Low-Med | convert | A multipage PDF → PNG writes **only page 1**, silently dropping the rest | 3-page PDF → one red PNG | Per-page outputs or an explicit warning |
| ✅ V4 | Low-Med | convert | ICO is capped at 256 px: big rasters and 300 dpi EPS/PDF fail with a raw ImageMagick error | `width or height exceeds limit` | Downscale to ≤256 |
| ✅ Q2 | Low-Med | compress | `--codec webp` fails on a CMYK TIFF (handed to `cwebp` as-is) | reproduced | Convert to RGB first |
| ✅ Q1 | Low | enhance | 16-bit sources silently become 8-bit | reproduced | Say so, or keep 16-bit for PNG/TIFF |
| ✅ E1 | Low-Med | export | A malformed SVG surfaces as a raw `MysuiteToolError` traceback | reproduced | One-line error (all tools) |
| ✅ E2 | Low | export | An output path that is a file → raw `NotADirectoryError` traceback | reproduced | Validate up front |

## Checked and fine (so you can trust these)

- **Export:** every single-file format (png/pdf/eps/svg/jpeg/webp/tiff) valid; ICO contains exactly the
  requested sizes; ICNS valid and ignores `--sizes` as documented; size means *width* and keeps aspect
  ratio; `cm`/`in` honour `--dpi`; margin and background; `--strict` makes the CMYK skip an error; PNG/WebP
  CMYK correctly skipped with a warning; CMYK PDFs contain only CMYK operators; recolor works on CMYK
  output; same-stem collisions stop the run before writing; overwrite/skip/dry-run; stray commas in a size
  list ignored; absurd/negative/garbage sizes are clean errors; `--name ../x` cannot escape; XML external
  entities are **not** followed (no file leak); eight unusual-but-valid SVGs (no viewBox, no size,
  50 000 px, 2 px, gradients, `<use>`, missing font, clipPath+opacity) all export.
- **Presets:** built-in `favicon`/`macos-icon`; user TOML preset; flag overrides preset; user preset
  overrides built-in; save/list/use round trip; saving keeps other sections and comments; a typo in a
  preset field is rejected, not ignored; malformed TOML is a clean error.
- **Convert:** every source × every target pair converts to a valid file (EPS→ICO aside, see V4);
  transparency flattened on white (or `--background`); vector `--dpi` scales; quality changes JPEG size;
  CMYK TIFF and 16-bit PNG convert; EXIF orientation survives as a tag; animated GIF → WebP keeps frames.
- **Watermark:** all nine positions land in the right corner; opacity blends; scale sizes the logo; SVG
  logos; logo larger than base; transparent bases keep alpha; bad input is a clean error.
- **Cutout:** real Vision runs write a PNG with alpha beside the source; the source is never modified.
- **`metadata credit`:** JPG, PNG, WebP and TIFF all embed and **read back** the author and copyright;
  never touches the source; skips existing; survives quotes, accents, CJK, `[/bold]` and 300-char authors;
  re-crediting a credited file doesn't crash.
- **Other:** strip on PNG text chunks/XMP leaves nothing; animated WebP keeps its frames; `randomize`
  varies; compress works on CMYK/16-bit/alpha sources for five of six codec paths; enhance accepts CMYK,
  16-bit, gray, palette and alpha inputs; all ten TUI behaviours (Export/Convert/Watermark/Cutout) work.

## Notes that are not bugs but should be documented

- `--dpi` only matters for SVGs sized in physical units (`in`, `mm`); a pixel-sized SVG ignores it.
- Export's `--sizes ""` falls back to the default seven sizes.
- Cutout is probabilistic: it is Apple's Vision model, not a deterministic matte.

## Suggested order for fixing

1. **Phase 1 (colour engine):** C1, C2, C3, R1–R5 (one design solves all eight).
2. **Quick wins, no design needed:** **D1 first (it blocks other people's use today)**, then V1 (+litter), W1, W2, V2, V4, Q2, E1, E2, K2.
3. **Decisions for you:** K1 (should cutout keep any metadata by default?) and S2 (reject `..` outright, or
   confine to the output root?).
4. **Phase 3/5:** S2 as part of profiles and the agent sandbox.

## How the quick fixes were resolved

- **D1:** each command now checks only the tools it uses (`doctor.NEEDS`); `mysuite doctor` still lists all.
- **V1/V2:** single-image targets take frame/page 1 and print a warning (even with `-q`); failed conversions leave no temp files.
- **V4:** ICO output is downscaled to at most 256 px.
- **W1/W2:** the base is auto-oriented and converted to true-colour sRGB before compositing.
- **K1 (decided: yes):** cutout removes GPS, serial-number and owner tags from its output; author/copyright and camera settings stay.
- **K2:** a cutout with under 0.5% opacity is a failure, writes nothing. Picking the *wrong* subject can't be detected and stays a known limit.
- **S2 (decided: reject):** a template that resolves outside the output folder is a clear error (`..`, absolute paths).
- **E1/E2:** malformed SVG and a file-as-output-folder give one-line errors.
- **Q1:** enhance still writes 8-bit but says so. **Q2:** compress converts CMYK TIFFs to RGB before WebP.
- **C1–C3, R1–R5 (colour engine):** `mysuite/color/`. Recolor parses real colours and compares by value with CIEDE2000
  tolerance; PDF, EPS and TIFF share one ICC-based CMYK engine (`exact`, `clean[:N]`, K-only greys); PDFs carry an
  output intent. See the README's "CMYK: one engine" section for modes and limits.
