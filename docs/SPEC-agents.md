# mysuite for agents — spec

Supersedes "Phase 5" in the earlier plan. Colour engine, tokens, presets/profiles and `.ai` import
(plan phases 1–4) are separate work that this spec *uses* but does not redefine.

## What it is

mysuite stays a local, free, offline image/asset toolbox. This spec makes it equally usable by
**software agents**: Claude Code or any shell-capable agent (plain commands), MCP clients (Claude
Desktop, Cursor, local-model front ends such as LM Studio/Ollama clients), and Python scripts. An agent
can inspect files, run any of the existing tools, chain several in one declarative pipeline (e.g.
export → strip + credit metadata → compress) and get structured JSON back. It is a **helper, not an
editor**: nothing needs a visual preview, every step is file-in / file-out and repeatable.

## The hard constraint

**Everything is offline, deterministic and file-based; there is no way to look at an image in a
terminal.** So (a) no generative or cloud AI in v1, (b) an agent must never have to *see* pixels to
decide: every operation reports facts as JSON, and a separate `inspect` tool gives agents "eyes" as
numbers (size, colours, sharpness, transparency, orientation). A vision-capable client may additionally
ask for a small thumbnail/contact-sheet file. (c) Agents are less trusted than the user, so sandboxing is
default-on.

## Stack

- Existing: Python 3.11, Typer CLI, Textual TUI, external binaries (rsvg-convert, gs, magick, exiftool, …).
- **One core API** `mysuite/api/` (plain functions returning dataclasses) used by CLI, TUI, MCP, pipelines
  and `import mysuite`. CLI commands become thin wrappers. Follows the existing `*_file()` + outcome-dataclass
  pattern already in every tool; this spec lifts it into a stable public layer.
- `--json` output via Pydantic-free dataclasses → `dataclasses.asdict`, versioned with `schema_version`.
- MCP: official `mcp` Python SDK, stdio transport (`mysuite mcp`), optional extra `mysuite[mcp]`.
- Pipelines: TOML (primary; matches `mysuite.toml`), JSON accepted. YAML only if trivially free (no new
  dependency required).
- Local-model agent loop (`mysuite agent`) is **later**: OpenAI-compatible endpoint (Ollama, LM Studio,
  llama.cpp) configured by URL; off unless configured.

## Data

No new data sources. Inputs/outputs are the user's files. Config (profiles, presets, sandbox roots) lives
in `mysuite.toml`. No network use except already-planned opt-in token sources (git/Figma, later). No audit
log by default (a log is a trace); opt-in `[agent] audit_log = path`.

Failure behaviour: every call returns `ok` plus per-item results; partial failure never aborts silently;
originals are never modified (outputs go beside or into a given folder, same as today).

## Surfaces & files

New or changed (paths relative to repo root):

1. `mysuite/api/__init__.py`, `api/results.py` — public functions: `inspect`, `export`, `convert`,
   `cutout`, `watermark`, `metadata_strip|randomize|credit`, `compress`, `enhance`, `colors`, `transform`,
   `doctor`; result dataclasses with `schema_version`, `ok`, `items[]`, `warnings[]`, `errors[]`.
2. `mysuite/api/sandbox.py` — allowed roots (default: current directory and explicit `--allow`/config),
   realpath checks (no symlink escape), reject `..`/absolute template components (fix S2 generalised),
   no-overwrite default, limits (max files, max megapixels, max bytes).
3. **`--json` on every command** (`mysuite/cli.py` + each tool's `cli.py`): stdout is exactly one JSON
   document, human text to stderr or suppressed; stable exit codes: 0 ok, 1 some items failed, 2 bad usage,
   3 sandbox/policy refusal, 4 missing tool (with `install_hint`). `--dry-run` returns the plan as JSON.
4. `mysuite inspect FILE…` (`mysuite/inspect/`) — facts for agents: format, pixel size, dpi, colour mode/space
   (RGB/CMYK/gray), bit depth, alpha present/used, orientation, frames/pages, file size, dominant palette
   (top-N hex + share), blur score, "mostly blank" flag, metadata summary (GPS present? serial present?),
   SVG: viewBox/size, colours used, text/fonts, embedded images. Optional `--thumb PATH` writes a ≤512 px PNG
   (and `--sheet` a labelled contact sheet) for vision-capable clients.
5. `mysuite transform` (`mysuite/transform/`) — the only new *editing* tool, deliberately small and
   composable: `crop` (box or aspect ratio + gravity), `trim` (border/whitespace), `pad`/`canvas`
   (to square/aspect, with colour), `resize` (fit/fill/exact, px/%), `rotate`/`flip`, `round` (corners),
   `background` (flatten/colour). Each is a pipeline step too. Anything needing visual judgement
   (retouching, free-hand, sliders) is out.
6. `mysuite/pipeline/` — `mysuite pipeline run FILE.toml [--dry-run] [--json]`. Steps name a tool and its
   options; `inputs` globs; each step's outputs feed the next via `from = "previous"`; a single plan covers
   the chain; fail-fast or `continue_on_error`; never overwrites without `overwrite = true`. Example:
   ```toml
   [[step]]  tool = "export"   formats = ["png","pdf"]  sizes = [512, 1024]
   [[step]]  tool = "metadata" action = "credit"  author = "Acme"  from = "previous"
   [[step]]  tool = "compress" codec = "oxipng"   from = "previous"
   ```
7. `mysuite mcp` (`mysuite/mcp/server.py`) — one MCP tool per API function (+ `pipeline_run`), JSON schemas
   generated from the dataclasses, structured errors, sandbox enforced, resources: `mysuite://presets`,
   `mysuite://profiles`, `mysuite://doctor`. Tool output never echoes untrusted text (filenames, metadata)
   unescaped; it is returned as data fields.
8. **Agent docs** (the part that makes agents "use it perfectly"):
   - `AGENTS.md` at repo root (read by Claude Code, Codex, Cursor and others): purpose, install, the
     decision table "user wants X → run Y", exact commands with `--json`, exit codes, sandbox rules,
     do/don't (don't overwrite, always `--dry-run` first on batches, check `doctor` on exit 4), worked
     examples for the 6 most common jobs.
   - `llms.txt` (short index) and `docs/agents/COMMANDS.md` (generated from the Typer app so it cannot go
     stale; a test fails if it differs).
   - A Claude Code skill `skills/mysuite/SKILL.md` that wraps the same decision table.
   - `mysuite schema [TOOL]` prints the JSON schema of any command's options/results (self-describing for
     agents that don't read docs).
9. Python: `import mysuite.api` documented in `docs/agents/PYTHON.md`.
10. Later (milestone 8): `mysuite agent "…"` loop for local models. It plans with the schema, shows the
    pipeline (dry-run JSON), asks approval unless `--yes`, then runs it.

Integrations worth considering for the *tool* side (all local, deterministic, each optional and
doctor-checked): OCR (Apple Vision text recognition via the existing Swift helper pattern), QR/barcode
read/write, perceptual-hash duplicate finder (`inspect --similar`), PDF page splitting/merging (gs), SVG
optimise (svgo-like, optional), video poster-frame extraction (ffmpeg, optional). Ranked in Open questions.

## Out of scope for v1

- Generative image models (diffusion, inpainting) and any cloud API. Reason: size, speed, and the "free,
  local, private" promise; revisit as a separate optional plugin.
- Interactive/visual editing (sliders, brushes, retouching). Reason: no preview in a terminal.
- A built-in LLM agent loop (kept as milestone 8). Reason: tools and docs first.
- Server/HTTP API. stdio MCP and CLI cover the stated clients.
- Audit logging by default.

## Open questions

| Question | Decided by |
| --- | --- |
| Which extra "eyes/hands" integrations to include first (OCR, QR, dedupe, PDF split/merge, SVG optimise, video frames) | user, after seeing `inspect` |
| Default sandbox root: current directory only, or home folder? Recommendation: current directory + explicit `--allow` | user |
| Ship MCP as extra (`mysuite[mcp]`) or core dependency? Recommendation: extra | decide at implementation |
| Pipeline file: TOML only, or also JSON/YAML? Recommendation: TOML + JSON | decide at implementation |
| Does the earlier "Phase 1 colour engine" land before this? Recommendation: yes for `colors`/CMYK, but milestones 1–5 here do not depend on it | user |

## Verification

End-to-end check that proves the feature works (run in a fresh session, repo at `main`):

1. `mysuite doctor --json` → exit 0, `{"schema_version":1,"ok":true,"tools":[…]}`.
2. `mysuite inspect logo.svg photo.jpg --json` → valid JSON, fields listed above present; a CMYK TIFF reports
   `colorspace":"CMYK"`.
3. `mysuite export logo.svg --formats png --sizes 64 --json --dry-run` lists planned files, writes nothing.
4. Sandbox: the same command with `--out ../escape` exits **3** and writes nothing (tested for `..`,
   absolute paths, symlink out of the root).
5. `mysuite pipeline run examples/release.toml --json` produces exported files, then credited, then
   compressed; JSON lists every output with its step; originals unchanged (hash compare).
6. MCP: a scripted client (the `mcp` SDK's own client) lists tools, calls `inspect` and `pipeline_run`,
   receives structured results, and a call outside the sandbox returns a structured refusal.
7. Docs: `mysuite schema export` output validates a sample request; the generated `COMMANDS.md` equals the
   committed one; `AGENTS.md` examples are executed by a test (doc-tests) so they cannot rot.
8. Real agent trial: ask Claude Code, with only `AGENTS.md`, to "make a 512px and 1024px PNG of logo.svg,
   strip metadata, credit me, compress" and confirm it succeeds without hints; repeat with a local model
   through an MCP client if one is available (report honestly if not run).
9. Full pytest suite green; CI green.

## Milestones

1. **API layer + `--json` + exit codes** for the existing 7 tools (CLI stays identical for humans).
   Verify: schema tests per command, golden JSON files.
2. **Sandbox + `doctor --json` + `schema` command.** Verify: escape-attempt tests.
3. **`inspect` (+ `--thumb/--sheet`).** Verify: fixtures with known facts (CMYK, alpha, rotated, blank,
   multipage).
4. **`AGENTS.md`, `llms.txt`, generated `COMMANDS.md`, skill, doc-tests.** Verify: step 7 above; first real
   agent trial (step 8, Claude Code).
5. **Pipelines (`pipeline run/plan`).** Verify: step 5; dry-run equals real run.
6. **`mysuite mcp`.** Verify: step 6; test with Claude Desktop/Cursor config snippets documented.
7. **`transform` tool** (crop/trim/pad/resize/rotate/round/background) as CLI, TUI screen and pipeline step.
8. **Extra integrations** (ranked in Open questions) and **`mysuite agent`** for local models.
