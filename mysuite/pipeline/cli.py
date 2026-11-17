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
        help="Validate everything and show the plan; nothing is written. Step 1 is planned exactly; later steps that "
        "consume earlier outputs are listed symbolically (their file names exist only after the run). Read-only steps "
        "(inspect, dupes, contrast, pdf info…) really run. A step that could write files and has no --dry-run of its "
        "own is not run at all (status skipped, with a warning).",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml, passed to every step."),
    keep_intermediate: bool = typer.Option(
        False, "--keep-intermediate",
        help="Keep the files earlier steps wrote and later steps replaced (by default they are deleted after a "
        "successful run; the file's `keep_intermediate = true` does the same). Files that already existed are never deleted.",
    ),
) -> None:
    try:
        pipeline = engine.load(file)
        results = engine.run(pipeline, dry_run=dry_run, config=config_path, keep_intermediate=keep_intermediate or None)
    except engine.PipelineError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc

    jsonout.set_extra(pipeline=pipeline.name, dry_run=dry_run)
    failed = False
    for res in results:
        child = res.doc or {}
        jsonout.add_item(
            step=res.step.index, tool=" ".join(res.step.command), id=res.step.id, status=res.status,
            exit_code=res.exit_code, inputs=res.inputs, outputs=res.outputs, removed=res.removed, note=res.note,
            errors=child.get("errors", []), result=child,
        )
        for w in child.get("warnings", []):
            jsonout.add_warning(f"step {res.step.index}: {w}")
        if res.note == engine.OVERWRITE_KEEPS:
            jsonout.add_warning(res.note)
        for e in child.get("errors", []):
            jsonout.add_error(f"step {res.step.index}: {e}")
        if res.status == "failed":
            failed = True
            log_error(f"step {escape(res.step.label)} failed" + (f": {escape(res.note)}" if res.note else ""))
        elif res.status == "skipped":
            log_skip(f"step {escape(res.step.label)} - {escape(res.note or '')}")
        elif res.status == "planned" and not res.outputs and res.note:
            log_step(f"step {escape(res.step.label)}: [dim]{escape(res.note)}[/dim]")
        else:
            verb = "would write" if dry_run else "wrote"
            log_step(f"step {escape(res.step.label)}: {verb} {len(res.outputs)} file(s)"
                     + (f", removed {len(res.removed)} intermediate" if res.removed else "")
                     + (f" [dim]{escape(res.note)}[/dim]" if res.note else ""))
    removed = sum(len(r.removed) for r in results)
    jsonout.set_extra(removed_intermediate=removed)
    if failed:
        code = max((r.exit_code or 1) for r in results if r.status == "failed")
        raise typer.Exit(code)
    if dry_run:
        skipped = sum(1 for r in results if r.status == "skipped")
        console.print(f"\n[bold green]dry run[/bold green] - nothing was written. {len(results)} step(s)"
                      + (f"; {skipped} can't be previewed and will only run in a real run" if skipped else ""))
    else:
        console.print(f"\n[bold green]done[/bold green] - {len(results)} step(s)"
                      + (f"; {removed} intermediate file(s) removed (keep them with --keep-intermediate)" if removed else ""))


@app.command("validate", help="Check a pipeline file without running anything: tools, actions, option names and "
             "values (with 'did you mean'), step ids and input globs. Exit 0 when it would start.")
@jsonout.with_json("pipeline-validate")
def validate(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Pipeline file (.toml or .json)."),
) -> None:
    try:
        pipeline = engine.load(file)
        inputs = engine.resolve_inputs(pipeline) if pipeline.inputs else []
    except engine.PipelineError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    from mysuite import schema

    commands = schema.build()["commands"]
    jsonout.set_extra(pipeline=pipeline.name, inputs=[str(p) for p in inputs], keep_intermediate=pipeline.keep_intermediate)
    for step in pipeline.steps:
        spec = commands[" ".join(step.command)]
        argv = engine.preview_argv(step, commands)
        jsonout.add_item(step=step.index, tool=" ".join(step.command), id=step.id, status="ok", source=step.source,
                         has_dry_run=engine.has_dry_run(step, commands), writes_files=not engine.is_read_only(step, commands),
                         argv=argv)
        note = "" if engine.has_dry_run(step, commands) or engine.is_read_only(step, commands) else \
            " [dim](no --dry-run: a dry run skips it)[/dim]"
        log_step(f"step {escape(step.label)}: {escape(' '.join(argv))}{note}")
    console.print(f"\n[bold green]valid[/bold green] - {len(pipeline.steps)} step(s), {len(inputs)} input file(s); nothing was run")
