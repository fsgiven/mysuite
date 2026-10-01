from __future__ import annotations

import typer

from mysuite.compress.cli import compress as compress_command
from mysuite.config import load_config
from mysuite.convert.cli import convert as convert_command
from mysuite.cutout.cli import cutout as cutout_command
from mysuite.doctor import run_doctor
from mysuite.export.cli import export as export_command
from mysuite.metadata.cli import app as metadata_app
from mysuite.preset_cli import app as preset_app
from mysuite.watermark.cli import watermark as watermark_command

app = typer.Typer(
    help="mysuite — a CLI suite of tools for repetitive design/asset work.",
    no_args_is_help=True,
)

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
app.add_typer(preset_app, name="preset")
app.add_typer(metadata_app, name="metadata")


@app.command("doctor", help="Check that required tools (rsvg-convert, gs, magick, mysuite-cutout, exiftool, c2patool, codec encoders) are installed.")
def doctor() -> None:
    config = load_config()
    all_ok = run_doctor(config.tools)
    raise typer.Exit(0 if all_ok else 1)


@app.command("tui", help="Launch the interactive terminal dashboard.")
def tui() -> None:
    from mysuite.tui.app import MysuiteApp  # lazy import — keeps `export`/`doctor` startup fast

    MysuiteApp().run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
