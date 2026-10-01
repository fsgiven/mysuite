# Roadmap (finalised plan)

Source of truth for what is built next. Details per area: [SPEC-agents.md](SPEC-agents.md),
[TEST-FINDINGS.md](TEST-FINDINGS.md). Rule for everything below: **a helper for quick jobs**, local and
offline, file-in/file-out, no preview needed, each step reports JSON, each step has tests before it ships.

## Order

| # | Block | Clears / delivers |
| --- | --- | --- |
| 1 | ✅ **Colour engine** (`mysuite/color/`) — done | C1–C3, R1–R5 fixed; no `xfail` markers left; `mysuite colors` palette command and `tokens` CMYK mode still to add |
| 2 | **Agent layer** (SPEC-agents milestones 1–4) | API layer, `--json`, exit codes, sandbox, `schema`, `inspect`, `AGENTS.md`, `llms.txt`, skill, doc-tests |
| 3 | **Pipelines** (milestone 5) | declarative multi-tool runs, one dry-run plan |
| 4 | **MCP server** (milestone 6) | `mysuite mcp` for Claude Desktop/Cursor/local-model clients |
| 5 | **`transform`** (milestone 7) | crop/trim/pad/resize/rotate/round/background |
| 6 | **More tools** (below) and `relight` | ✅ `relight` tier A shipped (experimental); depth-model tier B needs a spike; the ranked tool list is still open |
| 7 | Tokens/variants, presets/profiles, `.ai` import | from the earlier plan, slotted around 2–6 as needed |

### 1. Colour engine, concretely
1. `color/parse.py`: parse any CSS colour (hex3/4/6/8, `rgb()`, `rgb(%)`, `hsl()`, named, `transparent`, `currentColor` skipped) → RGBA. Clears R1–R3.
2. `color/svg.py`: XML-parse the SVG; walk attributes, `style=""`, `<style>` blocks, gradient stops; `palette()` and `recolor(map, tolerance)` that rewrite values in place. Clears R4. Tolerance matching by CIEDE2000. Clears R5.
3. `color/cmyk.py`: one RGB→CMYK conversion (profile-based via ImageMagick/ICC for flat colours; K-only for neutrals) with modes `exact`, `clean[:step]` (snap to 5, ≥97→100, ≤3→0, ΔE shown), `tokens` later. Clears C2.
4. `color/pdfink.py`: rewrite PDF content-stream `rg/RG` → `k/K` using those numbers; EPS and TIFF derive from that same PDF so all three agree. Gradients/images fall back to profile conversion and are reported. Clears C1.
5. ICC output intent embedded (FOGRA39/SWOP/user file; honest about which profile ships — must be redistributable, else user-supplied). Clears C3.
6. `mysuite colors FILE` (palette, usage, `--json`), CLI flags `--cmyk-mode`, `--cmyk-profile`, `--recolor-tolerance`, TUI fields.
Verification: same swatch gives identical ink numbers read back from pdf/eps/tiff; `clean:5` turns 73/92 into 75/90 numerically; recolor matrix all CAUGHT; CI green.

## Relight (new): feasibility review

What the references do: Clipdrop *Relight* and Photoshop's relighting-style features estimate depth/surface
orientation from one photo with a neural network and re-shade it (some also generate). Tiers for us:

| Tier | How | Quality | Cost | Verdict |
| --- | --- | --- | --- | --- |
| A classical ✅ shipped as `mysuite relight` (experimental) | subject mask (existing Vision cutout) → pseudo-depth (distance transform + luminance) → normals → Lambert + soft specular from a chosen light (direction, height, colour, intensity, softness), ambient kept; presets (key-left, rim, top, golden-hour) | believable "studio light" nudge on products/portraits; not a true relight | no model, ~100 lines + NumPy | **build** |
| B local depth model | monocular depth (Depth Anything V2 small, ~25 MB, via ONNX Runtime/Core ML) → real normals → same shader | clearly better 3-D feel, closer to Clipdrop on faces/products | optional extra `mysuite[ai]`, weights fetched on explicit command, licence checked first | **build after A, behind an extra** |
| C neural relight (IC-Light style, diffusion) | generative | best | multi-GB, slow, licence unverified | **out of scope**; maybe a separate plugin |

Honest limits: A and B re-shade existing pixels, they cannot invent light on the unlit side or cast realistic
shadows from new objects. Verification: a synthetic sphere with a known light direction must be brightest on
the right side (tested numerically); real photos judged by me via screenshots, and I will report where it looks
wrong. Needs a spike first (depth model licence, Core ML vs ONNX, speed on this Mac) before committing to B.

## More tools: shortlist (all local; ranked by usefulness for "quick task without opening Photoshop")

Non-AI, deterministic:
1. `inspect` (in plan), `transform` (in plan), **`sheet`** contact sheet / before-after
2. **Social/web kit**: favicon + app icon + OG image + manifest as one pipeline preset
3. **OCR** via Apple Vision (extends the existing Swift helper) — text from screenshots/scans
4. **QR/barcode** make/read
5. **Dedupe/similar** (perceptual hash) for photo folders
6. **PDF split/merge/images** (Ghostscript, already required)
7. **SVG optimise** and SVG ↔ PDF/EPS/AI import (poppler)
8. **Colour tools**: palette from image, contrast/accessibility check, colour-blind simulation
9. **Video poster frame / GIF from video** (ffmpeg, optional)
10. **Rename/sort by content** (earlier idea) once `inspect` exists

AI, local (optional extras, never required): relight (above), depth map, alternative background removal
(U²-Net/rembg-style) for non-macOS, face/subject-aware smart crop (Vision, no extra model), auto alt-text with a
local VLM through Ollama (only if the user runs one), local upscaler weights for `enhance`
(Real-ESRGAN, still unverified), denoise/colorise models. Generative edit stays out of scope.

## Done criteria for the whole roadmap
All tests pass with **zero** expected failures; CI green; README status table honest; every new tool documented
in `AGENTS.md` and exercised by a doc-test.

## Update 2026-10-02: what was added after this plan

- Dashboard: animated front page, tool cards with icons, helper mascot (F1 / h), and **generated screens**: any CLI command
  gets a form from its own option schema (`tui/screens/auto_screen.py`), so new tools show up as cards without hand-built UI.
- Decided with the user: no free-form editing screen (can't preview in a terminal) - `transform` stays CLI/agent/pipeline only; build
  deterministic one-shot tools instead ("ask for 500, get 500").
- Shipped: `kit` (favicon, iOS, Android, social, retina), `pdf` (13 jobs), `print`, `rename`, `sheet`, `profile`, `ocr`, `qr`,
  `dupes`, `diff`, `contrast`; optional macOS helper `mysuite-vision` (`mysuite/native/vision/build.sh`).
- Still open from the shortlist: SVG optimise, `.ai`/PDF-to-SVG import (needs poppler), video poster frames, content-aware rename,
  the relight depth model, tokens/variants, profiles, `mysuite agent`.
