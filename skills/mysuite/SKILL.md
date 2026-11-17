---
name: mysuite
description: Use the local mysuite CLI for image, logo and PDF jobs - export a logo to many sizes/formats incl. CMYK and brand variants, convert, compress, crop/resize, print sizes, watermark, cut out, enhance, PDF tools, strip or credit metadata, inspect/compare/check files, QR codes, pipelines. Offline, JSON output.
---

# mysuite

Read `AGENTS.md` in the mysuite repo (or run `mysuite schema`) before using it. Short version:

1. Add `--json` to every command; check the exit code (0 ok, 1 item failed, 2 bad usage, 3 sandbox refusal, 4 missing tool).
2. `--dry-run --json` first for anything touching more than one file; never pass `--overwrite` unless asked.
3. Use `mysuite inspect FILE --json` (and `--thumb`) to learn about a file instead of guessing.
4. Run in a sandbox: `mysuite --allow <folder> …`.
5. On exit 4 tell the user which tool to install (`missing_tools[].install_hint`); never install without asking.
6. Don't guess option values: `mysuite schema COMMAND` lists each option's `choices` and runnable `examples`,
   and says whether the command `writes_files` and `has_dry_run`.

## The tools

| Job | Commands |
| --- | --- |
| Logos and brand files | `export`, `kit`, `variants`, `tokens`, `svg`, `profiles` |
| Images | `convert`, `compress`, `transform`, `print`, `profile`, `watermark`, `cutout` (macOS), `enhance` |
| PDFs | `pdf` (merge, split, extract, rotate, resize, number, stamp, images, render, from-images, compress, strip, info) |
| Privacy and ownership | `metadata` (strip, credit, declare, apply), `mark` (embed, detect) |
| Check and compare (read only) | `inspect`, `dupes`, `diff`, `contrast`, `ocr` (macOS), `qr` (make, read) |
| Files | `rename`, `sheet` |
| Several steps | `pipeline` (`validate` a file first, then `run --dry-run`, then `run`) |
| Setup | `doctor`, `install`, `testcases`, `mcp` (`--slim` for small local models), `schema` |
| Experimental (say so to the user) | `relight` (experimental classical relighting), `shield` (experimental, 1.4 GB, no guarantee) |
