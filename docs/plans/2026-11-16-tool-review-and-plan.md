# Tool review and work plan (2026-11-16)

Source: four parallel reviews of all 66 commands on `main` @ `7211e1c`. Each reviewer ran its tools on
`mysuite testcases make` files and changed nothing in the repo. Every defect below has a reproduction in the
review notes; UNVERIFIED means the needed helper isn't installed (`mysuite-cutout`, `mysuite-vision`, `c2patool`,
`pdftocairo`).

**Who it is for:** a brand/editorial designer who makes logo sets (positive, negative/knockout, cut-out, inactive;
RGB and CMYK; PNG/SVG/PDF/EPS; house naming), social assets and video corner bugs, quick photo jobs, and PDFs for
approval and print. What they need, in order: **exactly what I asked for**, then speed, then features.

How to use this file: Phase 0 runs first, as one agent. After that, each workstream (WS) is one agent and one
PR, and the "Owns" lists don't overlap within a phase. A workstream that needs a file another WS owns waits for
that phase (see "Order"). Every WS ends the way today's PRs did: tests first, full suite green,
`docs/agents/COMMANDS.md` regenerated, before/after evidence (screenshots or contact sheets) in the PR, never
merged by the agent.

---

## 1. Verdict per tool

| Tool | Verdict | Why |
|---|---|---|
| `export` | **keep, core** | The main deliverable tool. Needs brand naming, vector-once output and a faithful SVG (WS-A). |
| `variants list/make` | **keep the engine, hide `make`** | Variants belong inside `export --variants`; `make` stays for SVG-only output. |
| `kit list/make` | **keep, web/app specs only** | Fix safe areas; take over the `favicon`/`macos-icon` presets; **cut the `retina` kit** (`--size-by` does it better). |
| `svg import/optimise` | keep | Small fixes. |
| `tokens list/show/check` | keep | Load Figma JSON files; consistent `--tokens`; fall back to the profile's tokens. |
| `profiles …` | **keep, renamed `brand …` (the one saved brand setup)** | Absorbs presets; names its own CMYK ICC. Fix relative paths; add `--update`. "Profile" is kept for ICC only (decision 4). |
| `preset list/save` | **merge into `brand`** | Three ways to save "a set of outputs" (preset/profile/kit) is two too many. Keep `--preset` as an alias. |
| `convert` + `compress` | **merge the UX into "Save for web"** | One step: format + quality or a target size, always sRGB, GPS stripped. Both commands stay in the CLI. |
| `enhance` | keep, **hide `history`** | Fix the dashboard default mismatch (auto white balance). |
| `cutout` | keep (UNVERIFIED) | Add trim-to-subject and a background colour. |
| `watermark` | keep | Fix the 4× file size and speed; add brand presets. |
| `transform` | **keep, add a dashboard card** | The "quick crop / social size" tool; add named social sizes. |
| `relight` | **hide: kept in the background (CLI-only, experimental)** | Doesn't serve "fast and exact" today; the code stays in case it becomes interesting (decision 2). |
| `print` | keep | `--cmyk` (via the colour engine); paper names; effective-ppi warning. |
| `profile` (colour) | **renamed `colour`: `colour convert` + `colour profiles`** | "Profile" means an ICC profile (sRGB, P3, FOGRA39, PSO = print profiles). `colour profiles` lists the built-in and your own ICC files (decision 4). |
| `rename`, `sheet` | keep | Small fixes. |
| `pdf` (13) | keep | Fix integrity and safety first (WS-D). |
| `metadata strip` | keep, core | Keep the ICC profile; add a "keep credit" mode; don't re-process its own outputs. |
| `metadata randomize` | **hide behind `--advanced`** | A fake camera and date is false provenance for a publisher: off the dashboard and out of the main help; it runs only with an explicit `--advanced` (decision 1). |
| `metadata credit/declare/apply` | **merge into one "Protect & credit" flow** | IPTC credit always, via exiftool; C2PA when available; one output, no leftover intermediate files. |
| `mark embed/detect` | keep | Solid. Fix secret-key handling in the dashboard. |
| `shield` | **hide: kept in the background (experimental)** | Its own docs say it didn't stop ChatGPT; 1.4 GB. The code stays for later (decision 2). |
| `inspect` | **keep, and make it the dashboard's file info panel** | Currently unreachable from the dashboard. |
| `ocr`, `qr`, `dupes`, `diff`, `contrast` | keep, as "Check & compare" | `diff` should accept SVG/PDF (logo vs logo). |
| `install` + `doctor` | **merge into a "Setup" screen** | Split required from optional; an Install button on exit 4. |
| `testcases` | **dev/demo only, no card** | Offer it as "Try with sample files". |
| `schema`, `mcp` | keep | Schema needs choices, examples and write/dry-run flags (WS-E). |
| `pipeline run` | keep, **after the dry-run fix** | It currently writes files in dry-run (Phase 0). |
| `tui` | keep | WS-F. |

---

## 2. Phase 0: safety (one agent, first, small PRs)

These are silent "not what I asked for" or data-safety failures. Fix them before anything else.

| # | Defect | Repro (short) | Files |
|---|---|---|---|
| P0-1 | **`pipeline run --dry-run` writes files** (also MCP `pipeline_run(dry_run=true)`): `--dry-run` is added only when the command has it, and 43 of 66 don't. | a pipeline with step `pdf rotate` plus `--dry-run` writes `_rotated.pdf` | `pipeline/engine.py`, `mcpserver.py`: in dry mode, refuse or skip steps without `--dry-run` and say so. Commands get real `--dry-run` in their own WS. |
| P0-2 | **16-bit greyscale becomes solid white** in profile, transform, print, enhance, relight, sheet, inspect (Pillow `I;16 → RGB` clips). | `print tc/photos/grey_16bit.png --size 5x5cm` → mean 1.0 | new `utils/imageio.py` (one loader: orientation, 16→8 bit, ICC kept), and use it in `transform/transform.py`, `specs/core.py`, `enhance/enhance.py`, `relight/relight.py`, `inspect/inspect.py` |
| P0-3 | **`pdf strip` writes a broken PDF** (a regex edit after pypdf writes, so the xref is wrong) and keeps annotation authors. | `pdf strip text_3_pages.pdf` → `PdfReader(strict=True)` fails | `pdf/ops.py` |
| P0-4 | **The secret mark key shows in the command and is stored in plaintext** in `~/.cache/mysuite/ui-state.json`. | Protect card → mark embed | `tui/screens/auto_screen.py` (masked input, never remembered), `tui/ui_state.py`; pass it via env/stdin, not argv |
| P0-5 | **Every note prints as "⚠ skipping"** (`log_skip` used for warnings), e.g. "skipping … image turned 90°" while the file *is* written. | many | `utils/console.py`: add `log_note`, switch callers. A shared hotspot, so it goes here once. |
| P0-6 | **Merge can overwrite its own input**; a "compressed" PDF larger than the original is written anyway. | `pdf merge a.pdf b.pdf -o a.pdf --overwrite` | `pdf/ops.py`, `pdf/cli.py` (with P0-3) |

---

## 3. Workstreams

### WS-A Export: brand deliverables (L)
**Owns:** `export/naming.py`, `export/planner.py`, `export/renderer.py`, `export/cli.py`, `config.py` (export settings), `tui/screens/export_screen.py`.
1. Naming:
   - Fix `{width}`/`{height}` (both are currently the size label).
   - A friendly error for unknown tokens (now a traceback).
   - Uppercase tokens or a filter (`{COLORSPACE}`, `{FORMAT}`).
   - An option to drop the `<name>/` root folder.
   - `{variant}` in the default file name.
   - `--naming`/`--path` CLI flags.
   - Goal: reproduce `RGB/PNG/20250514_BILD_Logo_positive_512_RGB.png` exactly.
2. Vector once:
   - SVG/PDF/EPS once per variant × colour space, at the artwork size (1:1 pt) or a chosen size, not once per pixel size.
   - Fix the PDF↔EPS bounding-box mismatch.
3. SVG output is the recoloured/variant **source**, optimised, not an rsvg/cairo re-render (now `rgb(%)`, lost ids, stray rects).
4. Small fixes:
   - `--recolor-tolerance` can't override the config value.
   - Duplicate skip lines.
   - A near-black CMYK hint ("consider --cmyk-map").
5. Dashboard:
   - Remove the `tokens` CMYK mode, which the CLI rejects.
   - Add fields for `--size-by`, `--cmyk-profile`, `--negative-map` and naming.
   - Drop the duplicate `on-dark` chip.
6. Declare `choices` for its options (see WS-E).

### WS-B Brand setup: variants, profiles, presets, kits, tokens (M–L), after WS-A (shares `config.py` and `export_screen.py`)
**Owns:** `color/variants.py`, `color/knockout.py`, `color/cli.py`, `profiles.py`, `profiles_cli.py`, `config_write.py`, `preset_cli.py`, `tui/screens/save_preset_modal.py`, `kits/*`, `tokens/*`, `vector/*`.
1. Variants:
   - A `map:#dd0000=#c8ced4+white=#e6e9ec` step, so `inactive=map:…` works inside `--variants`.
   - A negative that matched nothing must fail, not write the original.
   - A `variants make` default that works without tokens.
   - The knockout refusal suggests an alternative.
2. Profiles:
   - Store or resolve token paths relative to the toml.
   - `save --update` (merge, not wipe).
   - `save` can set naming, size-by and cmyk_map.
   - **Presets become export-only profiles** (`--preset` stays as an alias).
3. A **ready-made `bild`-style example profile**, plus `export --compare REF_DIR` (in `export/cli.py`, after WS-A has merged): names, dimensions, CMYK inks against a reference folder (today's manual check, automated).
4. Kits:
   - YouTube 1546×423 and Instagram story safe areas.
   - PDF/EPS logos rasterised at the right scale.
   - Take over `favicon`/`macos-icon`; cut `retina`.
   - `--variants`; a corner-bug kit.
5. Watermark brand presets: a profile's `[watermark]` table (logo, position, scale, opacity) and `watermark --profile`. Touches `watermark/cli.py` after WS-C1 has merged.
6. Tokens: load Figma variables from a file; `--tokens` everywhere, falling back to the profile; nearest token for off-palette colours.
7. SVG optimise: strip Illustrator leftovers; no 0%-smaller copies; `-o` naming. `import` needs `pdftocairo` (UNVERIFIED).

### WS-C1 Image pipeline and "Save for web" (L), after Phase 0 (uses `utils/imageio.py`)
**Owns:** `compress/*`, `convert/*`, `watermark/*`, `enhance/*`, `relight/*`, `tui/screens/{compress,convert,watermark,enhance}_screen.py`.
1. Compress prep:
   - Flatten on white (transparent becomes **black** today).
   - Auto-orient (portrait shows sideways today).
   - Convert ICC to sRGB (P3 goes dull today).
   - Keep animation frames or warn.
   - Never write a result larger than the source.
2. **"Save for web":**
   - `--max-kb N` (quality search) and `--max-size PX`.
   - Strip GPS, embed sRGB.
   - Bytes before/after in JSON and in the dashboard.
   - The convert and compress screens become one flow (both commands stay).
3. Convert:
   - `jpg` alias.
   - "Not an image" errors instead of raw magick/cwebp stderr.
   - A truncated-file warning.
   - A name clash must not abort the batch.
4. Watermark:
   - JPEG output with quality and subsampling (2.2 MB → 9.5 MB today).
   - One magick pass (7 s/photo today).
   - Position aliases (`bottom`), `--size-by`, warn on animation.
   - Brand presets via profile are WS-B's (they need `profiles.py`).
5. Enhance: the auto-white-balance checkbox default must match the preset; scale placeholder; keep EXIF. Hide `history`.
6. Relight: hide; `--format`/`--quality`.
7. Cross-cutting rule (written into `utils/imageio.py` docs): **web output = sRGB embedded, GPS and serials stripped, copyright kept.**

### WS-C2 Sizes: print, transform, colour (M), after WS-C1 (shares the image loader conventions)
**Owns:** `specs/*` (print, profile, rename, sheet), `transform/*`, `color/cmyk.py` (`convert_image` only).
1. Named sizes shared by transform and print: instagram-square/portrait/story, og, linkedin, A4/A5/10×15.
2. `print`:
   - An effective-ppi warning.
   - Paper names.
   - `--cmyk`.
   - Keep or convert ICC.
   - `-q`.
3. `profile`: fold into `print --cmyk` and "Save for web"; whatever remains is named `colour`.
4. CMYK image conversion **vectorised** (`ImageCms.applyTransform`): 55 s → under 1 s for 12 MP. Configurable FOGRA/PSO default (user-supplied ICC; licence).
5. Transform:
   - `--resize 0` traceback.
   - JPEG quality 75 → source quality/90.
   - Don't change the format silently.
   - Subject-aware crop gravity.
6. `rename`: honour EXIF orientation for `{w}x{h}`. `sheet` is done (PR #9).

### WS-D Documents & privacy (M–L), after Phase 0 (P0-3/P0-6 already touched `pdf/ops.py`)
**Owns:** `pdf/*`, `metadata/*`, `mark/*`, `shield/*`, `camera_profiles.py`, `tui/screens/metadata_screen.py`.
1. PDF:
   - Escape `%` in gs output names (and stop reporting "gs missing" for every error).
   - Open owner-password-only PDFs.
   - The page selection in the `extract`/`split` suffix (silent wrong skip today).
   - Corner stamps clipped.
   - SVG logos for `stamp`/`from-images`.
   - Numbering speed (one overlay per page today: more than 7 min on 4000 pages).
   - from-images honours EXIF orientation.
   - `--dry-run` on every PDF writer.
   - `compress --max-size 10MB`.
   - Next: preflight `info` (fonts, colour spaces, image dpi, boxes, PDF/X).
2. `metadata strip`:
   - **Keep ICC** (`--drop-icc` as an option).
   - "Keep credit" mode (drop GPS/camera/serials, keep creator/copyright/credit).
   - Skip its own `_stripped`/`_marked`/`_declared` outputs.
   - One "remove hidden data" engine shared with `pdf strip`.
3. **"Protect & credit"**:
   - credit writes IPTC Core (By-line, Credit Line, Copyright Notice, Source, Usage Terms) via exiftool, and C2PA only with `c2patool` (labelled test certificate).
   - Then declare, then optional mark.
   - One output, no leftover intermediate files.
   - `declare` warns that GPS is kept.
   - Help text matches the code.
   - The renamed policy names in `profiles.py` (`POLICIES`) are the one edit WS-D makes there; WS-B starts after it.
4. `randomize`: hide behind `--advanced` (decision 1). `shield`: kept but off the Protect card; help says what it doesn't stop (decision 2).
5. `mark`: check the key once, up front; keep the input format (JPEG → JPEG); keep metadata; 16-bit note; `--dry-run`.

### WS-E Agent layer (M), in parallel with A/C1/D (owns `schema.py`: everyone else only *declares* choices on their own options)
**Owns:** `schema.py`, `mcpserver.py`, `pipeline/*`, `AGENTS.md`, `llms.txt`, `skills/mysuite/SKILL.md`, `docs/agents/*`, `helpers/*`, `inspect/*`, `doctor.py`, `components.py`, `install_cli.py`.
1. The schema exposes **choices** (from `click.Choice`), **examples**, and the flags `writes_files` / `has_dry_run`, plus a status enum that matches reality. This is the grammar `mysuite ask` needs so a local model can't invent values.
2. Pipeline:
   - The dry-run refusal is replaced by real dry-runs as commands gain them.
   - `print`/`rename`/`sheet`/`profile`/`qr`/`diff` as steps.
   - Intermediate files cleaned up.
   - "Did you mean".
   - `pipeline validate`.
3. MCP: `dry_run` on `mysuite_pdf`; a slim description mode for small local models.
4. Helpers:
   - `qr make` refuses `.jpg` (or writes a real JPEG) and checks contrast.
   - `dupes` shows the name first and checks the false-positive group.
   - `diff` accepts SVG/PDF.
   - `contrast --tokens`.
   - The missing-tool message is one line (the whole doctor table today).
5. `inspect`: malformed SVG gives a warning; readable labels; PDF colour spaces.
6. `doctor`: required vs optional, and which commands each tool unlocks; exit 0 when only optional tools are missing.
7. `llms.txt`/`SKILL.md` list all tools, not only the first 7.

### WS-F Dashboard (M–L), after WS-E (uses schema choices); Phase 0's P0-4 is already in
**Owns:** `tui/shell.py`, `tui/registry.py`, `tui/home.py`, `tui/helper.py`, `tui/theme.css`, `tui/screens/auto_screen.py`, `tui/widgets/*`, new screens (Transform, Setup, file info).
1. Results:
   - Read-only commands no longer say "N written" (contrast, `pdf info`, tokens).
   - Table output shown as a DataTable, not wrapped box-drawing characters.
   - Exit 4 gives one line plus an **Install** button.
2. Generated forms:
   - Dropdowns from choices.
   - Visible defaults.
   - Readable labels ("Dpi" → "DPI").
   - Split the 46-field "Profiles create".
   - F1 shows the *selected* action, not the first.
3. Cards:
   - Add **Transform** (with relight inside it as experimental).
   - **Setup** (doctor + install) replaces Parts.
   - "Exact" → "Print & size".
   - "Helpers" → "Check & compare" plus Inspect.
   - One "Protect & credit" card.
   - Variants move out of "Tokens".
   - Add a **type-to-filter / command bar** on home. All tools become reachable; numbers stop at 9.
4. A file info panel from `inspect` when a file is picked.
5. A result **thumbnail**, recent jobs with their command (rerun/copy), and "save as pipeline".

### WS-N Naming (S–M): one agent, **between Phase 1 and Phase 2**, nothing else running
A rename across many files, so nobody else may be editing them while it runs. Old names stay as hidden aliases with a one-line deprecation note.
1. `profile` (ICC conversion) → `colour convert`; new `colour profiles` lists sRGB/P3/SWOP plus ICC files registered in `mysuite.toml` (`[colour.profiles] fogra39 = "~/ICC/ISOcoated_v2.icc"`).
2. Company `profiles …` → `brand …` (`brand save/list/show/check/delete`); global `--profile/-P NAME` → `--brand NAME`. The token option `--brand` (which brand inside a token source) → `--token-brand`.
3. `export --profiles rgb,cmyk` → `--colour rgb,cmyk`; `profiles save --colour-profiles` follows.
4. Docs, MCP tool names/params, the dashboard cards and AGENTS.md updated in the same PR; COMMANDS.md regenerated; every old spelling has a test that it still works.

### WS-G `mysuite ask` (exists, parked)
Built and committed in its own session (`feat/ask`, 51 tests). Needs the LM Studio address, then a rebase onto `main`. It gets much better once **WS-E item 1** (choices/examples) and **Phase 0 P0-1** (trustworthy dry-run) are in, so land it after those.

---

## 4. Order

```
Phase 0  (1 agent)          P0-1 … P0-6
Phase 1  (4 agents)         WS-A   WS-C1   WS-D   WS-E
Phase 1.5 (1 agent)         WS-N naming (nothing else running)
Phase 2  (3 agents)         WS-B (after A)   WS-C2 (after C1)   WS-F (after E)
Phase 3                     WS-G ask, README/screenshots refresh, release notes
```

**Shared hotspots** (only the owner edits; others ask): `config.py` (A then B), `profiles.py` (D's policy names, then B),
`export/cli.py` (A, then B for `--compare`), `watermark/cli.py` (C1, then B for presets), `export_screen.py` (A then B),
`tui/shell.py`, `registry.py`, `theme.css`, `auto_screen.py` (F; Phase 0 touches `auto_screen.py` only for P0-4),
`schema.py` (E), `utils/console.py` (Phase 0), `utils/imageio.py` (Phase 0, then C1/C2 use it).
Each WS adds `--dry-run` and declares option `choices` **for its own commands**.

## 5. Decisions (made 2026-11-16)

1. **`metadata randomize`:** hidden behind `--advanced` (WS-D): off the dashboard, out of the main help.
2. **`shield` and `relight`:** kept in the background. The code and CLI commands stay; they're off the dashboard cards and the main docs, and clearly marked experimental (WS-D, WS-C1).
3. **Presets merge into brand setups;** `--preset NAME` stays as an alias (WS-B).
4. **"Profile" means ICC only:** `colour convert` / `colour profiles`; company setups become `brand`; `export --colour rgb,cmyk` (WS-N).
5. **CMYK ICC per brand:** a brand setup names your FOGRA39/PSO `.icc`; export, print and colour warn when they fall back to SWOP. Brand CMYK values (`cmyk_map`) still win for logo colours (WS-B, plus the warning in WS-A/WS-C2).
6. **Retina kit cut;** the docs show the `export --size-by` command that replaces it (WS-B).
