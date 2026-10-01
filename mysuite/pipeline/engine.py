"""Declarative multi-tool runs: `mysuite pipeline run release.toml`.

A pipeline is a list of steps; each step names a mysuite command and its options (option names are the
long CLI flags with underscores: `cmyk_mode = "clean"` is `--cmyk-mode clean`). By default a step takes the
previous step's outputs as inputs. Every step runs as its own `mysuite … --json` process, so a pipeline
behaves exactly like the same commands typed by hand - including the sandbox - and nothing new has to be
trusted. Options are validated against the live CLI before anything runs.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mysuite import sandbox, schema
from mysuite.convert._parsing import SOURCE_EXTENSIONS

MAX_STEPS = 20
RESERVED = {"tool", "action", "id", "from", "inputs", "only"}
SINGLE = {"export", "convert", "cutout", "watermark", "compress", "inspect", "transform", "relight"}
METADATA_ACTIONS = {"strip", "randomize", "credit"}


class PipelineError(ValueError):
    pass


@dataclass
class Step:
    index: int
    tool: str
    command: list[str]              # e.g. ["metadata", "strip"]
    options: dict[str, Any]
    source: str = "previous"        # previous | inputs | <step id>
    only: list[str] | None = None
    id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.index}: {' '.join(self.command)}" + (f" ({self.id})" if self.id else "")


@dataclass
class Pipeline:
    name: str
    base: Path
    inputs: list[str]
    steps: list[Step]
    overwrite: bool = False
    continue_on_error: bool = False


# ------------------------------------------------------------------ loading
def load(path: Path) -> Pipeline:
    try:
        raw = path.read_bytes()
        data = json.loads(raw) if path.suffix.lower() == ".json" else tomllib.loads(raw.decode("utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise PipelineError(f"can't read pipeline file {path}: {exc}") from exc
    return parse(data, base=path.resolve().parent, default_name=path.stem)


def parse(data: dict[str, Any], *, base: Path, default_name: str = "pipeline") -> Pipeline:
    allowed_top = {"name", "inputs", "overwrite", "continue_on_error", "step", "steps"}
    unknown = set(data) - allowed_top
    if unknown:
        raise PipelineError(f"unknown top-level key(s): {', '.join(sorted(unknown))} (allowed: {', '.join(sorted(allowed_top))})")
    raw_steps = data.get("step") or data.get("steps") or []
    if not raw_steps:
        raise PipelineError("a pipeline needs at least one [[step]]")
    if len(raw_steps) > MAX_STEPS:
        raise PipelineError(f"too many steps ({len(raw_steps)}); the limit is {MAX_STEPS}")
    inputs = data.get("inputs", [])
    if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
        raise PipelineError("`inputs` must be a list of paths/globs")
    commands = schema.build()["commands"]
    steps: list[Step] = []
    ids: set[str] = set()
    for i, raw in enumerate(raw_steps, start=1):
        step = _parse_step(i, raw, commands)
        if step.id:
            if step.id in ids:
                raise PipelineError(f"step {i}: duplicate id {step.id!r}")
            ids.add(step.id)
        if step.source not in ("previous", "inputs") and step.source not in ids:
            raise PipelineError(f"step {i}: from = {step.source!r} is not an earlier step id")
        if i == 1 and step.source == "previous":
            step.source = "inputs"
        if step.source == "inputs" and not inputs:
            raise PipelineError(f"step {i} needs input files: set `inputs = [...]` at the top")
        steps.append(step)
    return Pipeline(
        name=str(data.get("name", default_name)), base=base, inputs=inputs, steps=steps,
        overwrite=bool(data.get("overwrite", False)), continue_on_error=bool(data.get("continue_on_error", False)),
    )


def _parse_step(index: int, raw: dict[str, Any], commands: dict[str, Any]) -> Step:
    if not isinstance(raw, dict) or "tool" not in raw:
        raise PipelineError(f"step {index}: each step needs a `tool`")
    tool = str(raw["tool"])
    if tool == "metadata":
        action = raw.get("action")
        if action not in METADATA_ACTIONS:
            raise PipelineError(f"step {index}: metadata needs action = one of {', '.join(sorted(METADATA_ACTIONS))}")
        command = ["metadata", action]
    elif tool == "enhance":
        command = ["enhance", "run"]
    elif tool in SINGLE:
        command = [tool]
    else:
        raise PipelineError(
            f"step {index}: unknown tool {tool!r} - expected one of "
            f"{', '.join(sorted(SINGLE | {'metadata', 'enhance'}))}"
        )
    spec = commands[" ".join(command)]
    valid = {}
    for opt in spec["options"]:
        for flag in opt["flags"]:
            if flag.startswith("--") and not flag.startswith("--no-"):
                valid[flag[2:].replace("-", "_")] = opt
    options = {k: v for k, v in raw.items() if k not in RESERVED}
    for key in options:
        if key not in valid or key in ("json", "help", "config", "dry_run"):
            raise PipelineError(
                f"step {index} ({tool}): unknown or reserved option {key!r}. Valid: "
                f"{', '.join(sorted(k for k in valid if k not in ('json', 'help', 'config', 'dry_run')))}"
            )
    only = raw.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(x, str) for x in only)):
        raise PipelineError(f"step {index}: `only` must be a list of file extensions")
    return Step(index, tool, command, options, source=str(raw.get("from", "previous")),
                only=[x.lower().lstrip(".") for x in only] if only else None, id=raw.get("id"))


# ----------------------------------------------------------------- building argv
def _flag_for(key: str, spec: dict[str, Any]) -> tuple[str, bool]:
    target = "--" + key.replace("_", "-")
    for opt in spec["options"]:
        if target in opt["flags"]:
            return target, bool(opt["repeatable"])
    return target, False


def build_argv(step: Step, inputs: list[Path], *, overwrite: bool, dry_run: bool, commands: dict[str, Any] | None = None) -> list[str]:
    commands = commands or schema.build()["commands"]
    spec = commands[" ".join(step.command)]
    argv: list[str] = []
    for key, value in step.options.items():
        flag, repeatable = _flag_for(key, spec)
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            else:
                alt = next((f for o in spec["options"] for f in o["flags"] if f == "--no-" + flag[2:]), None)
                if alt:
                    argv.append(alt)
        elif isinstance(value, (list, tuple)):
            if repeatable:
                for v in value:
                    argv += [flag, str(v)]
            else:
                argv += [flag, ",".join(str(v) for v in value)]
        elif isinstance(value, dict):
            for k, v in value.items():                       # e.g. recolor = {"#dd0000" = "#0057b8"}
                argv += [flag, f"{k}={v}"]
        else:
            argv += [flag, str(value)]
    option_flags = {f for o in spec["options"] for f in o["flags"]}
    if overwrite and "--overwrite" in option_flags and "--overwrite" not in argv:
        argv.append("--overwrite")
    if dry_run and "--dry-run" in option_flags:
        argv.append("--dry-run")
    argv += ["--json"]
    return [*step.command, *argv, "--", *[str(p) for p in inputs]]


def resolve_inputs(pipeline: Pipeline) -> list[Path]:
    found: list[Path] = []
    for pattern in pipeline.inputs:
        full = pattern if os.path.isabs(pattern) else str(pipeline.base / pattern)
        hits = sorted(glob.glob(os.path.expanduser(full), recursive=True))
        if not hits:
            raise PipelineError(f"input {pattern!r} matches nothing (relative paths are relative to the pipeline file)")
        found += [Path(h) for h in hits]
    return found


# ------------------------------------------------------------------ running
@dataclass
class StepResult:
    step: Step
    status: str                      # ok | failed | skipped | planned
    exit_code: int | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    doc: dict[str, Any] | None = None
    note: str | None = None


def _child_env() -> dict[str, str]:
    """Children must run the same mysuite as the parent (not another copy that happens to be installed)."""
    env = dict(os.environ)
    package_parent = str(Path(__file__).resolve().parent.parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH", "")]))
    return env


def _run_child(argv: list[str], cwd: Path) -> tuple[int, dict[str, Any] | None, str]:
    prefix: list[str] = []
    policy = sandbox.active()
    if policy:
        for root in policy.roots:
            prefix += ["--allow", str(root)]
    cmd = [sys.executable, "-m", "mysuite", *prefix, *argv]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=_child_env())
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        doc = None
    return proc.returncode, doc, proc.stderr


def _usable(paths: list[str], step: Step) -> list[Path]:
    wanted = {f".{e}" for e in step.only} if step.only else set(SOURCE_EXTENSIONS)
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        path = Path(p)
        if path.suffix.lower() in wanted and path.is_file():
            out.append(path)
    return out


def run(pipeline: Pipeline, *, dry_run: bool = False, config: Path | None = None) -> list[StepResult]:
    commands = schema.build()["commands"]
    originals = resolve_inputs(pipeline) if pipeline.inputs else []
    outputs_by: dict[str, list[str]] = {"inputs": [str(p) for p in originals]}
    previous_key = "inputs"
    results: list[StepResult] = []
    failed = False
    for step in pipeline.steps:
        key = step.source if step.source != "previous" else previous_key
        candidates = outputs_by.get(key, [])
        if failed and not pipeline.continue_on_error:
            results.append(StepResult(step, "skipped", note="an earlier step failed"))
            continue
        inputs = _usable(candidates, step) if key != "inputs" else [Path(c) for c in candidates]
        if key != "inputs" and step.index > 1 and not inputs and dry_run:
            results.append(StepResult(step, "planned", note=f"will process the outputs of step {key} (not known until it runs)"))
            outputs_by[f"step{step.index}"] = []
            previous_key = f"step{step.index}"
            if step.id:
                outputs_by[step.id] = []
            continue
        if not inputs:
            results.append(StepResult(step, "failed", note=f"no usable input files from {key!r}"))
            failed = True
            continue
        argv = build_argv(step, inputs, overwrite=pipeline.overwrite, dry_run=dry_run, commands=commands)
        if config:
            argv[argv.index("--"):argv.index("--")] = ["--config", str(config)]
        code, doc, err = _run_child(argv, pipeline.base)
        outs = [i["output"] for i in (doc or {}).get("items", []) if i.get("status") in ("written", "skipped_existing", "planned") and i.get("output")]
        status = "ok" if code == 0 else "failed"
        if dry_run and code == 0:
            status = "planned"
        res = StepResult(step, status, code, [str(p) for p in inputs], outs, doc,
                         note=None if doc else (err.strip().splitlines()[-1] if err.strip() else "no JSON result"))
        results.append(res)
        outputs_by[f"step{step.index}"] = outs
        previous_key = f"step{step.index}"
        if step.id:
            outputs_by[step.id] = outs
        if code != 0:
            failed = True
    return results
