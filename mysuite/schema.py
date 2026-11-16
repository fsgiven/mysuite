"""Self-description of the CLI for agents: `mysuite schema [COMMAND…] [--markdown]`.

Built from the live Typer/Click command tree, so it cannot drift from the real options.
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Any

from mysuite.utils.jsonout import SCHEMA_VERSION

RESULT_SCHEMA: dict[str, Any] = {
    "description": "With --json every command prints exactly one document like this on stdout "
    "(human text goes to stderr).",
    "type": "object",
    "required": ["schema_version", "command", "ok", "exit_code", "items", "warnings", "errors"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "command": {"type": "string"},
        "ok": {"type": "boolean"},
        "exit_code": {"enum": [0, 1, 3, 4]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["planned", "written", "skipped_existing", "failed"]},
                    "input": {"type": "string"},
                    "output": {"type": "string"},
                    "error": {"type": "string"},
                },
                "description": "Per file (export: per output file). Extra fields depend on the command.",
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "errors": {"type": "array", "items": {"type": "string"}},
        "missing_tools": {"type": "array", "description": "Present with exit code 4: [{tool, install_hint}]."},
        "cmyk": {"type": "object", "description": "export only, when CMYK was produced: mode, profile, colours."},
    },
}

EXIT_CODES = {
    "0": "ok",
    "1": "at least one item failed (or an unexpected error)",
    "2": "bad usage (unknown option, missing argument) - not JSON, comes from the argument parser",
    "3": "refused by the sandbox/policy - nothing was written",
    "4": "a required external tool is missing - see missing_tools for install hints",
}


# Click's ParamType.name for the plain Python annotations Typer maps (Click 8.2+ spelling).
_ANNOTATION_NAMES = {str: "str", int: "int", float: "float", bool: "boolean"}


def _type_and_choices(p: Any) -> tuple[str, list[Any] | None]:
    """The parameter's type name and choices, the same under every Typer version.

    Typer <0.29 (and plain Click) attach a Click ParamType as `p.type`. Typer 0.29+ bundles its own
    Click and drops `.type`; it keeps `p.type_descriptor` (the annotation plus the Option/Argument
    info) instead, so the names Click would have given are rebuilt from that.
    """
    ptype = getattr(p, "type", None)
    if ptype is not None:
        kind = getattr(ptype, "name", "string")
        choices = getattr(ptype, "choices", None)
        return kind, (list(choices or []) if choices is not None or kind == "choice" else None)
    desc = getattr(p, "type_descriptor", None)
    if desc is None:
        return "string", None
    if desc.choices is not None:
        return "choice", list(desc.choices)
    annotation = desc.annotation
    if desc.is_list:  # list[X] -> X: repeatability is reported separately
        annotation = getattr(annotation, "__args__", (str,))[0]
    # `is_path` misses list[Path], and Typer spells a directory "dir" where Click says "directory".
    if desc.is_path or (isinstance(annotation, type) and issubclass(annotation, PurePath)):
        info = desc.parameter_info
        if info.file_okay and not info.dir_okay:
            return "file", None
        if info.dir_okay and not info.file_okay:
            return "directory", None
        return "path", None
    if desc.is_ranged:
        return f"{desc.ranged_type_name} range", None
    if annotation in _ANNOTATION_NAMES:
        return _ANNOTATION_NAMES[annotation], None
    return getattr(annotation, "__name__", "string").lower(), None


def _param(p: Any) -> dict[str, Any]:
    kind, choices = _type_and_choices(p)
    entry: dict[str, Any] = {"name": p.name, "type": kind}
    if getattr(p, "opts", None) and p.param_type_name == "option":
        entry["flags"] = list(p.opts) + list(getattr(p, "secondary_opts", []) or [])
    if p.param_type_name == "option" and getattr(p, "is_flag", False):
        entry["type"] = "boolean"
    if choices is not None:
        entry["choices"] = choices
    entry["required"] = bool(p.required)
    entry["repeatable"] = bool(getattr(p, "multiple", False)) or getattr(p, "nargs", 1) == -1
    default = p.default
    if default is not None and not callable(default) and not isinstance(default, (tuple, list)) or default == ():
        entry["default"] = default if isinstance(default, (str, int, float, bool)) else None
    help_text = getattr(p, "help", None)
    if help_text:
        entry["help"] = " ".join(help_text.split())
    return entry


def _walk(cmd: Any, prefix: str, out: dict[str, Any]) -> None:
    children = getattr(cmd, "commands", None)
    if children:
        for name, sub in sorted(children.items()):
            _walk(sub, f"{prefix} {name}".strip(), out)
        return
    params = [_param(p) for p in cmd.params]
    out[prefix] = {
        "help": " ".join((cmd.help or "").split()),
        "arguments": [p for p in params if "flags" not in p],
        "options": [p for p in params if "flags" in p],
    }


def build(only: list[str] | None = None) -> dict[str, Any]:
    import typer

    from mysuite.cli import app

    root = typer.main.get_command(app)
    commands: dict[str, Any] = {}
    _walk(root, "", commands)
    commands.pop("", None)
    if only:
        wanted = " ".join(only)
        commands = {k: v for k, v in commands.items() if k == wanted or k.startswith(wanted + " ")}
    global_opts = [_param(p) for p in root.params if p.param_type_name == "option" and p.name not in ("help", "install_completion", "show_completion")]
    return {
        "schema_version": SCHEMA_VERSION,
        "global_options": [g for g in global_opts if "flags" in g],
        "exit_codes": EXIT_CODES,
        "result": RESULT_SCHEMA,
        "commands": commands,
    }


def markdown(data: dict[str, Any] | None = None) -> str:
    data = data or build()
    lines = ["# mysuite command reference", "",
             "<!-- generated by `mysuite schema --markdown`; do not edit by hand -->", "",
             "## Global options", ""]
    for g in data["global_options"]:
        lines.append(f"- `{' / '.join(g['flags'])}` - {g.get('help', '')}")
    lines += ["", "## Exit codes", ""] + [f"- `{k}` - {v}" for k, v in data["exit_codes"].items()]
    lines += ["", "## Result document (`--json`)", "", "```json",
              __import__("json").dumps(data["result"], indent=2), "```", ""]
    for name, cmd in data["commands"].items():
        lines += [f"## `mysuite {name}`", "", cmd["help"], ""]
        for a in cmd["arguments"]:
            lines.append(f"- argument `{a['name']}` ({a['type']}{', required' if a['required'] else ''}"
                         f"{', repeatable' if a['repeatable'] else ''}) {a.get('help', '')}")
        for o in cmd["options"]:
            default = f", default `{o['default']}`" if o.get("default") not in (None, "", False) else ""
            lines.append(f"- `{' / '.join(o['flags'])}` ({o['type']}{default}{', repeatable' if o['repeatable'] else ''}) "
                         f"{o.get('help', '')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
