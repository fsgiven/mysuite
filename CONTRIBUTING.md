# Contributing

Thanks for helping. mysuite is a small personal project, so keep changes focused.

- **Bugs:** open an issue using the template. Include the exact command, the file type and the output of
  `mysuite doctor`.
- **Security problems:** don't open an issue. Use
  [private vulnerability reporting](https://github.com/fsgiven/mysuite/security/advisories/new) (see
  [SECURITY.md](SECURITY.md)).
- **Code:** run `pip install ".[dev]"` then `pytest`. Add a test with any behaviour change. Several tools
  currently have no automated tests (see the README's Status section), and tests for them are very welcome.
- **File names and paths are untrusted input.** Escape them before printing (`mysuite.utils.paths.show_path`
  for the dashboard, `rich.markup.escape` for the CLI) and pass absolute paths to external tools.
