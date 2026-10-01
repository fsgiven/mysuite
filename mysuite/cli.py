from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from mysuite import sandbox

from mysuite.compress.cli import compress as compress_command
from mysuite.config import load_config
from mysuite.convert.cli import convert as convert_command
from mysuite.cutout.cli import cutout as cutout_command
from mysuite.doctor import TOOL_SPECS, check_tools, run_doctor
from mysuite.utils import jsonout
from mysuite.enhance.cli import app as enhance_app
from mysuite.export.cli import export as export_command
from mysuite.inspect.cli import inspect_command
from mysuite.metadata.cli import app as metadata_app
from mysuite.pipeline.cli import app as pipeline_app
from mysuite.preset_cli import app as preset_app
from mysuite.transform.cli import transform as transform_command
from mysuite.watermark.cli import watermark as watermark_command

app = typer.Typer(
    help="mysuite — a CLI suite of tools for repetitive design/asset work.",
    no_args_is_help=True,
)


@app.callback()
def _global_options(
    allow: Optional[List[Path]] = typer.Option(
        None, "--allow",
        help="Sandbox: only read/write inside this folder (repeatable). Also set by MYSUITE_ROOTS. "
        "A path outside is refused with exit code 3. Meant for agents.",
    ),
    sandbox_here: bool = typer.Option(
        False, "--sandbox", help="Sandbox: allow only the current folder (same as --allow .)."
    ),
) -> None:
    roots = list(allow or [])
    if sandbox_here:
        roots.append(Path.cwd())
    env_policy = sandbox.from_environment()
    if roots:
        sandbox.configure(sandbox.Policy(roots, env_policy.max_files if env_policy else sandbox.DEFAULT_MAX_FILES))
    else:
        sandbox.configure(env_policy)

app.command("export", help="Batch-export an SVG into multiple sizes, formats, and color profiles.")(
    export_command
)
app.command("convert", help="Convert file(s) to another format, in place beside the source.")(
    convert_command
)
app.command("cutout", help="Isolate a photo's subject into a transparent-background PNG (macOS Vision).")(
    cutout_command
)
app.command("watermark", help="Stamp a logo onto image(s) as a visible, scalable watermark.")(
    watermark_command
)
app.command("compress", help="Re-encode image(s) via a best-in-class codec (mozjpeg/webp/avif/oxipng/pngquant/gifsicle), with optional sharpening.")(
    compress_command
)
app.command("inspect", help="Report facts about image/vector files (size, colour mode, transparency, palette, sharpness, GPS...) - for agents and humans.")(
    inspect_command
)
app.command("transform", help="Quick edits: trim, crop, rotate, flip, resize, pad, round corners, background (fixed, predictable order).")(
    transform_command
)
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(preset_app, name="preset")
app.add_typer(metadata_app, name="metadata")
app.add_typer(enhance_app, name="enhance")


@app.command("doctor", help="Check that required tools (rsvg-convert, gs, magick, mysuite-cutout, exiftool, c2patool, codec encoders) are installed.")
@jsonout.with_json("doctor")
def doctor() -> None:
    config = load_config()
    resolved = check_tools(config.tools)
    hints = {spec.attr: spec.install_hint for spec in TOOL_SPECS}
    for name, path in resolved.items():
        jsonout.add_item(tool=name, found=path is not None, path=path,
                         install_hint=None if path else hints[name])
    all_ok = run_doctor(config.tools)
    raise typer.Exit(0 if all_ok else 1)


@app.command("tui", help="Launch the interactive terminal dashboard.")
def tui() -> None:
    from mysuite.tui.app import MysuiteApp  # lazy import — keeps `export`/`doctor` startup fast

    MysuiteApp(show_welcome=True).run()


@app.command("schema", help="Describe every command's options, the --json result and exit codes (for agents).")
def schema(
    command: Optional[List[str]] = typer.Argument(None, help="Limit to one command, e.g. `export` or `metadata strip`."),
    markdown: bool = typer.Option(False, "--markdown", help="Print the command reference as Markdown instead of JSON."),
) -> None:
    import json

    from mysuite import schema as schema_module

    data = schema_module.build(command)
    if command and not data["commands"]:
        typer.echo(f"unknown command: {' '.join(command)}", err=True)
        raise typer.Exit(2)
    typer.echo(schema_module.markdown(data) if markdown else json.dumps(data, indent=2, ensure_ascii=False), nl=not markdown)


@app.command("mcp", help="Run the MCP server (stdio) so MCP clients can use mysuite. Needs `pip install 'mysuite[mcp]'`. Sandboxed to --allow folders (default: the current folder).")
def mcp_command(
    allow: Optional[List[Path]] = typer.Option(None, "--allow", help="Folder the server may read/write (repeatable). Default: the current folder."),
) -> None:
    try:
        from mysuite import mcpserver
    except ImportError as exc:
        typer.echo("the MCP server needs the optional dependency: pip install 'mysuite[mcp]'", err=True)
        raise typer.Exit(4) from exc
    mcpserver.serve(list(allow or []))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
