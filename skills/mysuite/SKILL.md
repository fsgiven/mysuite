---
name: mysuite
description: Use the local mysuite CLI for quick image and logo jobs - export a logo to many sizes/formats incl. CMYK, convert, watermark, cut out, compress, enhance, strip or credit metadata, inspect a file. Offline, JSON output.
---

# mysuite

Read `AGENTS.md` in the mysuite repo (or run `mysuite schema`) before using it. Short version:

1. Add `--json` to every command; check the exit code (0 ok, 1 item failed, 3 sandbox refusal, 4 missing tool).
2. `--dry-run --json` first for anything touching more than one file; never pass `--overwrite` unless asked.
3. Use `mysuite inspect FILE --json` (and `--thumb`) to learn about a file instead of guessing.
4. Run in a sandbox: `mysuite --allow <folder> …`.
5. On exit 4 tell the user which tool to install (`missing_tools[].install_hint`).
