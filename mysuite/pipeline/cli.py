from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape

from mysuite.pipeline import engine
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step

app = typer.Typer(help="Run several tools in one go from a declarative file (export -> metadata -> compress ...).", no_args_is_help=True)


@app.command("run", help="Run a pipeline file (TOML or JSON). Steps run in order; each takes the previous step's outputs.")
@jsonout.with_json("pipeline")
def run(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Pipeline file (.toml or .json)."),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Validate everything and show the plan. Step 1 is planned exactly; later steps that consume earlier "
        "outputs are listed symbolically (their file names exist only after the run).",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml, passed to every step."),
) -> None:
    try:
        pipeline = engine.load(file)
        results = engine.run(pipeline, dry_run=dry_run, config=config_path)
    except engine.PipelineError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    jsonout.set_extra(pipeline=pipeline.name, dry_run=dry_run)
    failed = False
    for res in results:
        child = res.doc or {}
        jsonout.add_item(
            step=res.step.index, tool=" ".join(res.step.command), id=res.step.id, status=res.status,
            exit_code=res.exit_code, inputs=res.inputs, outputs=res.outputs, note=res.note,
            errors=child.get("errors", []), result=child,
        )
        for w in child.get("warnings", []):
            jsonout.add_warning(f"step {res.step.index}: {w}")
        for e in child.get("errors", []):
            jsonout.add_error(f"step {res.step.index}: {e}")
        if res.status == "failed":
            failed = True
            log_error(f"step {escape(res.step.label)} failed" + (f": {escape(res.note)}" if res.note else ""))
        elif res.status == "skipped":
            log_skip(f"step {escape(res.step.label)} - {escape(res.note or '')}")
        else:
            log_step(f"step {escape(res.step.label)}: {len(res.outputs)} file(s)" + (f" [dim]{escape(res.note)}[/dim]" if res.note else ""))
    if failed:
        code = max((r.exit_code or 1) for r in results if r.status == "failed")
        raise typer.Exit(code)
    console.print(f"\n[bold green]{'plan ok' if dry_run else 'done'}[/bold green] - {len(results)} step(s)")
