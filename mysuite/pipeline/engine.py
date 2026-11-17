"""Declarative multi-tool runs: `mysuite pipeline run release.toml`.

A pipeline is a list of steps; each step names a mysuite command and its options (option names are the
long CLI flags with underscores: `cmyk_mode = "clean"` is `--cmyk-mode clean`). By default a step takes the
previous step's outputs as inputs. Every step runs as its own `mysuite … --json` process, so a pipeline
behaves exactly like the same commands typed by hand - including the sandbox - and nothing new has to be
trusted. Options are validated against the live CLI before anything runs.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mysuite import profiles as profiles_mod
from mysuite import sandbox, schema
from mysuite.convert._parsing import SOURCE_EXTENSIONS

MAX_STEPS = 20
RESERVED = {"tool", "action", "id", "from", "inputs", "only", "text", "against"}
NOT_OPTIONS = {"json", "help", "config", "dry_run"}          # set by the pipeline itself, never by a step
SINGLE = {"export", "convert", "cutout", "watermark", "compress", "inspect", "transform", "relight", "ocr", "dupes",
          "print", "rename", "sheet", "profile", "diff"}
METADATA_ACTIONS = {"strip", "randomize", "credit", "declare", "apply"}
SVG_ACTIONS = {"import", "optimise"}
PDF_ACTIONS = {"info", "merge", "split", "extract", "rotate", "resize", "strip", "number", "stamp", "images", "render", "from-images", "compress"}
ACTIONS: dict[str, set[str]] = {"metadata": METADATA_ACTIONS, "pdf": PDF_ACTIONS, "svg": SVG_ACTIONS,
                                "mark": {"embed", "detect"}, "qr": {"make", "read"}}
FIXED = {"enhance": ["enhance", "run"], "variants": ["variants", "make"], "kit": ["kit", "make"]}
TOOLS = SINGLE | set(ACTIONS) | set(FIXED)
TOP_LEVEL = {"name", "inputs", "overwrite", "continue_on_error", "keep_intermediate", "step", "steps", "profile"}


class PipelineError(ValueError):
    pass


def did_you_mean(word: str, choices: Any) -> str:
    """" - did you mean 'formats'?" for the closest choice, or "" when nothing is close."""
    close = difflib.get_close_matches(str(word), sorted(str(c) for c in choices), n=1, cutoff=0.6)
    return f" - did you mean {close[0]!r}?" if close else ""


@dataclass
class Step:
    index: int
    tool: str
    command: list[str]              # e.g. ["metadata", "strip"]
    options: dict[str, Any]
    source: str = "previous"        # previous | inputs | none (qr make) | <step id>
    only: list[str] | None = None
    id: str | None = None
    text: str | None = None         # qr make: what to encode (the command's argument, not a file)
    against: str | None = None      # diff: compare each run's single input with this file

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
    profile: str | None = None
    keep_intermediate: bool = False


# ------------------------------------------------------------------ loading
def load(path: Path) -> Pipeline:
    try:
        raw = path.read_bytes()
        data = json.loads(raw) if path.suffix.lower() == ".json" else tomllib.loads(raw.decode("utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise PipelineError(f"can't read pipeline file {path}: {exc}") from exc
    return parse(data, base=path.resolve().parent, default_name=path.stem)


def parse(data: dict[str, Any], *, base: Path, default_name: str = "pipeline") -> Pipeline:
    unknown = sorted(set(data) - TOP_LEVEL)
    if unknown:
        raise PipelineError(f"unknown top-level key(s): {', '.join(unknown)}{did_you_mean(unknown[0], TOP_LEVEL)} "
                            f"(allowed: {', '.join(sorted(TOP_LEVEL))})")
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
        if step.source not in ("previous", "inputs", "none") and step.source not in ids:
            raise PipelineError(f"step {i}: from = {step.source!r} is not an earlier step id")
        if i == 1 and step.source == "previous":
            step.source = "inputs"
        if step.source == "inputs" and not inputs:
            raise PipelineError(f"step {i} needs input files: set `inputs = [...]` at the top")
        steps.append(step)
    return Pipeline(
        name=str(data.get("name", default_name)), base=base, inputs=inputs, steps=steps,
        overwrite=bool(data.get("overwrite", False)), continue_on_error=bool(data.get("continue_on_error", False)),
        profile=data.get("profile"), keep_intermediate=bool(data.get("keep_intermediate", False)),
    )


def _command_for(index: int, tool: str, raw: dict[str, Any]) -> list[str]:
    if tool in ACTIONS:
        action = raw.get("action")
        if action not in ACTIONS[tool]:
            hint = did_you_mean(action, ACTIONS[tool]) if action else ""
            raise PipelineError(f"step {index}: {tool} needs action = one of {', '.join(sorted(ACTIONS[tool]))}"
                                + (f" (got {action!r}{hint})" if action else ""))
        return [tool, action]
    if tool in FIXED:
        return list(FIXED[tool])
    if tool in SINGLE:
        return [tool]
    raise PipelineError(f"step {index}: unknown tool {tool!r}{did_you_mean(tool, TOOLS)} - expected one of {', '.join(sorted(TOOLS))}")


def _check_choices(index: int, tool: str, key: str, value: Any, opt: dict[str, Any]) -> None:
    choices = opt.get("choices")
    if not choices or opt["type"] == "boolean":
        return
    for v in value if isinstance(value, (list, tuple)) else [value]:
        if str(v) not in [str(c) for c in choices]:
            raise PipelineError(f"step {index} ({tool}): {key} = {v!r} is not one of {', '.join(map(str, choices))}"
                                f"{did_you_mean(v, choices)}")


def _parse_step(index: int, raw: dict[str, Any], commands: dict[str, Any]) -> Step:
    if not isinstance(raw, dict) or "tool" not in raw:
        raise PipelineError(f"step {index}: each step needs a `tool`")
    tool = str(raw["tool"])
    command = _command_for(index, tool, raw)
    spec = commands[" ".join(command)]
    valid = {}
    for opt in spec["options"]:
        for flag in opt["flags"]:
            if flag.startswith("--") and not flag.startswith("--no-"):
                valid[flag[2:].replace("-", "_")] = opt
    usable = sorted(k for k in valid if k not in NOT_OPTIONS)
    options = {k: v for k, v in raw.items() if k not in RESERVED}
    for key, value in options.items():
        if key in NOT_OPTIONS:
            raise PipelineError(f"step {index} ({tool}): {key!r} is a reserved option: the pipeline sets it "
                                f"(use `pipeline run --dry-run` for a dry run)")
        if key not in valid:
            raise PipelineError(f"step {index} ({tool}): unknown option {key!r}{did_you_mean(key, usable) or '.'} "
                                f"Valid: {', '.join(usable)}")
        _check_choices(index, tool, key, value, valid[key])
    only = raw.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(x, str) for x in only)):
        raise PipelineError(f"step {index}: `only` must be a list of file extensions")
    source = str(raw.get("from", "previous"))
    text = None
    if command == ["qr", "make"]:
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            raise PipelineError(f"step {index}: qr make needs text = \"what to encode\"")
        source = "none"
    against = raw.get("against")
    if against is not None and (command != ["diff"] or not isinstance(against, str)):
        raise PipelineError(f"step {index}: `against` (a file path) only works with tool = \"diff\"")
    return Step(index, tool, command, options, source=source,
                only=[x.lower().lstrip(".") for x in only] if only else None, id=raw.get("id"), text=text, against=against)


# ----------------------------------------------------------------- building argv
def _flag_for(key: str, spec: dict[str, Any]) -> tuple[str, bool]:
    target = "--" + key.replace("_", "-")
    for opt in spec["options"]:
        if target in opt["flags"]:
            return target, bool(opt["repeatable"])
    return target, False


def has_dry_run(step: Step, commands: dict[str, Any]) -> bool:
    """True when the step's command can plan without writing (it has a real `--dry-run`)."""
    spec = commands[" ".join(step.command)]
    return any("--dry-run" in o["flags"] for o in spec["options"])


def is_read_only(step: Step, commands: dict[str, Any]) -> bool:
    """True when the step can't write a file with the options it has (inspect, dupes, contrast, pdf info…)."""
    spec = commands[" ".join(step.command)]
    if spec.get("writes_files", True):
        return False
    writers = {f[2:].replace("-", "_") for f in spec.get("writes_files_with", [])}
    return not any(step.options.get(k) for k in writers)


def not_previewed_note(step: Step) -> str:
    return (f"not run in this dry run: `{' '.join(step.command)}` has no --dry-run of its own, so previewing it "
            "would write files. A real run will run it.")


def _where(source: str) -> str:
    if source == "inputs":
        return "the pipeline inputs"
    return f"step {source[4:]}" if source.startswith("step") and source[4:].isdigit() else f"step {source!r}"


def symbolic_note(source: str) -> str:
    return f"planned: runs on the outputs of {_where(source)}, whose file names are not known until it runs"


def build_argv(step: Step, inputs: list[Path | str], *, overwrite: bool, dry_run: bool, commands: dict[str, Any] | None = None) -> list[str]:
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


def step_inputs(step: Step, files: list[Path], base: Path | None = None) -> list[Path | str]:
    """What goes after `--`: the files, except for qr make (its text) and diff against a reference."""
    if step.text is not None:
        return [step.text]
    if step.against is not None:
        ref = Path(os.path.expanduser(step.against))
        ref = ref if ref.is_absolute() or base is None else base / ref
        return [ref, *files]
    return list(files)


def preview_argv(step: Step, commands: dict[str, Any] | None = None) -> list[str]:
    """The command a step will run, with its inputs shown symbolically (for `pipeline validate`)."""
    placeholder: list[Path | str] = [{"inputs": "<inputs>", "previous": "<outputs of the previous step>"}.get(
        step.source, f"<outputs of {step.source}>")] if step.source != "none" else []
    argv = build_argv(step, step_inputs(step, placeholder), overwrite=False, dry_run=False, commands=commands)
    return ["mysuite", *argv]


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
    status: str                      # ok | failed | skipped | planned (skipped in a dry run = not previewable)
    exit_code: int | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    doc: dict[str, Any] | None = None
    note: str | None = None
    removed: list[str] = field(default_factory=list)   # this step's outputs deleted as intermediate files


def _child_env() -> dict[str, str]:
    """Children must run the same mysuite as the parent (not another copy that happens to be installed)."""
    env = dict(os.environ)
    package_parent = str(Path(__file__).resolve().parent.parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH", "")]))
    return env


def _run_child(argv: list[str], cwd: Path, profile: str | None = None) -> tuple[int, dict[str, Any] | None, str]:
    prefix: list[str] = []
    profile = profile or profiles_mod.active_name()
    if profile:
        prefix += ["--profile", profile]
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


def _outputs(doc: dict[str, Any] | None) -> list[str]:
    """Files a step hands on. `not_smaller` wrote nothing, so its input goes on unchanged."""
    outs = []
    for item in (doc or {}).get("items", []):
        status = item.get("status")
        if status in ("written", "skipped_existing", "planned", "done") and item.get("output"):
            outs.append(item["output"])
        elif status == "not_smaller" and item.get("input"):
            outs.append(item["input"])
    return outs


OVERWRITE_KEEPS = ("intermediate files were kept: with overwrite = true a step may have replaced a file you already "
                   "had, so nothing is deleted")


def _same(a: str) -> str:
    return os.path.realpath(a)


def _cleanup(results: list[StepResult]) -> None:
    """Delete files this run wrote that a later step replaced with its own output.

    A file is removed only if (1) this run wrote it (status written: never a file that already existed),
    (2) a later step took it as input, and (3) every step that took it wrote an output for it that exists
    (so a `not_smaller` compress, a contact sheet or a read-only step keeps it). Nothing is removed when a step
    failed. The original inputs are never written by the run, so they are never removed.
    """
    written: dict[str, StepResult] = {}
    for res in results:
        for item in (res.doc or {}).get("items", []):
            if item.get("status") in ("written", "done") and item.get("output"):
                written[_same(item["output"])] = res
    replaced: set[str] = set()
    kept: set[str] = set()
    for res in results:
        if res.exit_code is None:
            continue
        made_from = {_same(i["input"]) for i in (res.doc or {}).get("items", [])
                     if i.get("input") and i.get("output") and _same(i["output"]) != _same(i["input"])
                     and i.get("status") in ("written", "skipped_existing", "done") and Path(i["output"]).exists()}
        for f in map(_same, res.inputs):
            if f in written and written[f] is not res:
                (replaced if f in made_from else kept).add(f)
    for f in sorted(replaced - kept):
        try:
            Path(f).unlink()
        except OSError:
            continue
        owner = written[f]
        owner.removed.append(next((o for o in owner.outputs if _same(o) == f), f))


def run(pipeline: Pipeline, *, dry_run: bool = False, config: Path | None = None,
        keep_intermediate: bool | None = None) -> list[StepResult]:
    commands = schema.build()["commands"]
    originals = resolve_inputs(pipeline) if pipeline.inputs else []
    outputs_by: dict[str, list[str]] = {"inputs": [str(p) for p in originals]}
    previous_key = "inputs"
    results: list[StepResult] = []
    failed = False

    def hand_on(step: Step, outs: list[str]) -> None:
        nonlocal previous_key
        outputs_by[f"step{step.index}"] = outs
        previous_key = f"step{step.index}"
        if step.id:
            outputs_by[step.id] = outs

    for step in pipeline.steps:
        key = step.source if step.source != "previous" else previous_key
        if failed and not pipeline.continue_on_error:
            results.append(StepResult(step, "skipped", note="not run: an earlier step failed"))
            continue
        if step.source == "none":
            files: list[Path] = []
        else:
            candidates = outputs_by.get(key, [])
            files = _usable(candidates, step) if key != "inputs" else [Path(c) for c in candidates]
            if key != "inputs" and step.index > 1 and not files and dry_run:
                results.append(StepResult(step, "planned", note=symbolic_note(key)))
                hand_on(step, [])
                continue
            if not files:
                results.append(StepResult(step, "failed", note=f"no usable input files from {_where(key)}"))
                failed = True
                continue
        inputs = step_inputs(step, files, pipeline.base)
        if step.command == ["diff"] and len(inputs) != 2:
            results.append(StepResult(step, "failed", None, [str(p) for p in inputs], note=(
                f"diff compares exactly two files; got {len(inputs)} "
                "(use `against = \"ref.png\"` to compare one file with a reference)")))
            failed = True
            continue
        read_only = is_read_only(step, commands)
        if dry_run and not read_only and not has_dry_run(step, commands):
            # A dry run must never write. Without --dry-run the child would do the real work, so don't start it.
            results.append(StepResult(step, "skipped", None, [str(p) for p in inputs], [], None, note=not_previewed_note(step)))
            hand_on(step, [])
            continue
        argv = build_argv(step, inputs, overwrite=pipeline.overwrite, dry_run=dry_run, commands=commands)
        if config:
            argv[argv.index("--"):argv.index("--")] = ["--config", str(config)]
        code, doc, err = _run_child(argv, pipeline.base, pipeline.profile)
        outs = _outputs(doc)
        status = "ok" if code == 0 else "failed"
        if dry_run and code == 0:
            status = "planned"
        res = StepResult(step, status, code, [str(p) for p in inputs], outs, doc,
                         note=None if doc else (err.strip().splitlines()[-1] if err.strip() else "no JSON result"))
        results.append(res)
        hand_on(step, outs)
        if code != 0:
            failed = True
    keep = pipeline.keep_intermediate if keep_intermediate is None else keep_intermediate
    if not dry_run and not keep and not any(r.status == "failed" for r in results):
        if pipeline.overwrite:
            # A "written" file may have replaced one the user already had; the run can't tell, so keep everything.
            if any(r.step.index > 1 and r.exit_code == 0 for r in results) and results[-1].note is None:
                results[-1].note = OVERWRITE_KEEPS
        else:
            _cleanup(results)
    return results
