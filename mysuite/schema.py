"""Self-description of the CLI for agents: `mysuite schema [COMMAND…] [--markdown]`.

Built from the live Typer/Click command tree, so it cannot drift from the real options.
"""
from __future__ import annotations

import enum
from pathlib import PurePath
from typing import Any, Literal, get_args, get_origin

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
        "exit_code": {"enum": [0, 1, 2, 3, 4]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "status": {"enum": []},  # filled from STATUSES below
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

# Every `items[].status` a command can report. A test scans the source, so a new one can't go unlisted.
STATUSES: dict[str, str] = {
    "planned": "dry run: this would be written (nothing was)",
    "written": "the output file was written",
    "skipped_existing": "the output already exists and --overwrite was not given; nothing was written",
    "skipped": "not done: a pipeline step after a failure, a dry-run step that can't preview, or a kit file that doesn't apply",
    "failed": "this item failed; see `error`",
    "missing": "doctor: this tool is not installed (see `required`, `unlocks` and `install_hint`)",
    "ok": "a read-only result (inspect, contrast, diff, ocr, a pipeline step that ran)",
    "warning": "inspect: the file was read, but something is wrong with it; see the item's `warnings`",
    "done": "enhance: the picture was processed and written",
    "group": "dupes: one group of identical or look-alike files",
    "not_smaller": "pdf compress: the result would not be smaller, so nothing was written (no `output`)",
    "marked": "mark detect: the mark was found",
    "not_found": "mark detect: the mark was not found",
    "installed": "install: the component was installed",
    "already_installed": "install: nothing to do",
    "removed": "install --remove: the component was deleted",
    "not_installed": "install --remove: there was nothing to delete",
    "invalid": "profiles list: this profile has an error",
    "problem": "profiles check: this check failed",
    "deleted": "profiles delete: the profile was removed",
    "not_generated": "testcases: this sample needs a tool that is missing, so it was not made",
}
RESULT_SCHEMA["properties"]["items"]["items"]["properties"]["status"]["enum"] = list(STATUSES)

# Commands that never write a file, and options that make an otherwise read-only command write one.
READ_ONLY = {
    "contrast", "doctor", "dupes", "enhance presets", "inspect", "kit list", "mark detect", "mcp", "ocr", "pdf info",
    "pipeline validate", "preset list", "profiles check", "profiles list", "profiles show", "qr read", "schema",
    "testcases list", "tokens check", "tokens list", "tokens show", "tui", "variants list", "diff", "install",
    "enhance history",
}
WRITES_WITH: dict[str, list[str]] = {
    "inspect": ["--thumb", "--sheet"], "ocr": ["--save"], "diff": ["--out"], "install": ["--yes"],
    "enhance history": ["--clear"],
}

# At least one example per command; a test checks every flag and choice against the live CLI.
EXAMPLES: dict[str, list[str]] = {
    "compress": ["mysuite compress photo.jpg --codec mozjpeg --quality 80 --json",
                 "mysuite compress pics/ --codec webp --dry-run --json"],
    "contrast": ['mysuite contrast "#dd0000" white --json', 'mysuite contrast --logo logo.svg --on "#ffffff" --json',
                 'mysuite contrast token:--text-color-brand token:--bg-color --tokens tokens/ --theme dark --json'],
    "convert": ["mysuite convert pic.png --to webp,jpeg --json"],
    "cutout": ["mysuite cutout photo.jpg --json"],
    "diff": ["mysuite diff a.png b.png --json", "mysuite diff old.svg new.svg --out changes.png --json"],
    "doctor": ["mysuite doctor --json"],
    "dupes": ["mysuite dupes photos/ --recursive --json"],
    "enhance history": ["mysuite enhance history --limit 5"],
    "enhance presets": ["mysuite enhance presets"],
    "enhance run": ["mysuite enhance run photo.jpg --preset gentle --json"],
    "export": ["mysuite export logo.svg --formats png,pdf --sizes 64,512 --out dist --json",
               "mysuite export logo.svg --formats pdf --profiles cmyk --cmyk-mode clean --sizes 100 --dry-run --json",
               "mysuite export logo.svg --sizes 512,1080 --size-by height --json"],
    "inspect": ["mysuite inspect logo.svg photo.jpg --json", "mysuite inspect photo.jpg --thumb thumb.png --json"],
    "install": ["mysuite install --json", "mysuite install vision --dry-run --json"],
    "kit list": ["mysuite kit list --json"],
    "kit make": ["mysuite kit make logo.svg --kit favicon --out kits --json"],
    "mark detect": ["mysuite mark detect suspect.jpg --key SECRET --json"],
    "mark embed": ["mysuite mark embed photo.png --key SECRET --json"],
    "mcp": ["mysuite mcp --allow ~/Pictures/work", "mysuite mcp --allow ~/Pictures/work --slim"],
    "metadata apply": ["mysuite metadata apply photo.jpg --policy strip,credit --author Acme --json"],
    "metadata credit": ['mysuite metadata credit photo.jpg --author "Name" --copyright "© 2026 Name" --json'],
    "metadata declare": ["mysuite metadata declare photo.jpg --owner Acme --json"],
    "metadata randomize": ["mysuite metadata randomize photo.jpg --dry-run --json"],
    "metadata strip": ["mysuite metadata strip photo.jpg --json"],
    "ocr": ["mysuite ocr shot.png --json"],
    "pdf compress": ["mysuite pdf compress doc.pdf --level ebook --json"],
    "pdf extract": ["mysuite pdf extract doc.pdf --pages 1,3-5 --json"],
    "pdf from-images": ["mysuite pdf from-images a.jpg b.jpg --size a4 --json"],
    "pdf images": ["mysuite pdf images doc.pdf --json"],
    "pdf info": ["mysuite pdf info doc.pdf --json"],
    "pdf merge": ["mysuite pdf merge a.pdf b.pdf --json"],
    "pdf number": ['mysuite pdf number doc.pdf --format "Page {n} of {total}" --json'],
    "pdf render": ["mysuite pdf render doc.pdf --dpi 150 --json"],
    "pdf resize": ["mysuite pdf resize doc.pdf --size a4 --json"],
    "pdf rotate": ["mysuite pdf rotate doc.pdf --degrees 90 --pages 1 --json"],
    "pdf split": ["mysuite pdf split doc.pdf --every 2 --json"],
    "pdf stamp": ["mysuite pdf stamp doc.pdf --text DRAFT --json"],
    "pdf strip": ["mysuite pdf strip doc.pdf --json"],
    "pipeline run": ["mysuite pipeline run release.toml --dry-run --json", "mysuite pipeline run release.toml --json"],
    "pipeline validate": ["mysuite pipeline validate release.toml --json"],
    "preset list": ["mysuite preset list"],
    "preset save": ["mysuite preset save web --formats png,webp --sizes 64,512"],
    "print": ["mysuite print photo.jpg --size 10x15cm --dpi 300 --fit cover --json",
              "mysuite print pic.png --size 1200x630 --fit cover --json"],
    "profile": ["mysuite profile photo.tif --to srgb --json", "mysuite profile photo.tif --to cmyk --cmyk-mode clean --json"],
    "profiles check": ["mysuite profiles check acme --json"],
    "profiles delete": ["mysuite profiles delete acme --json"],
    "profiles list": ["mysuite profiles list --json"],
    "profiles save": ["mysuite profiles save acme --tokens tokens.css --brand x --formats png --sizes 64 --policy strip --json"],
    "profiles show": ["mysuite profiles show acme --json"],
    "qr make": ['mysuite qr make "https://example.com" --out qr.png --json',
                'mysuite qr make "https://example.com" --out qr.svg --error h --json'],
    "qr read": ["mysuite qr read photo.jpg --json"],
    "relight": ["mysuite relight product.png --preset golden-hour --json"],
    "rename": ['mysuite rename photos/ --pattern "trip_{n:3}{ext}" --dry-run --json'],
    "schema": ["mysuite schema export", "mysuite schema --markdown"],
    "sheet": ["mysuite sheet photos/ --out sheet.png --columns 5 --json"],
    "shield": ["mysuite shield photo.png --dry-run --json"],
    "svg import": ["mysuite svg import logo.ai --json"],
    "svg optimise": ["mysuite svg optimise logo.svg --json"],
    "testcases list": ["mysuite testcases list --json"],
    "testcases make": ["mysuite testcases make --out samples --json"],
    "tokens check": ["mysuite tokens check logo.svg --tokens tokens/ --brand bild --json"],
    "tokens list": ["mysuite tokens list tokens/ --brand bild --filter brand --json"],
    "tokens show": ["mysuite tokens show tokens/ text-color-brand --brand bild --json"],
    "transform": ["mysuite transform pic.png --crop-aspect 1:1 --resize 512 --round 50% --json"],
    "tui": ["mysuite tui"],
    "variants list": ["mysuite variants list --json"],
    "variants make": ["mysuite variants make logo.svg --variants negative,mono-white --tokens tokens/ --json"],
    "watermark": ["mysuite watermark photo.jpg --logo logo.svg --position bottom-right --json"],
}

EXIT_CODES = {
    "0": "ok",
    "1": "at least one item failed (or an unexpected error)",
    "2": "bad usage (unknown option, missing argument, a value not in `choices`) - from the CLI this is not JSON "
         "(it comes from the argument parser); MCP tools return it as JSON",
    "3": "refused by the sandbox/policy - nothing was written",
    "4": "a required external tool is missing - see missing_tools for install hints",
}


# Click's ParamType.name for the plain Python annotations Typer maps (Click 8.2+ spelling).
_ANNOTATION_NAMES = {str: "str", int: "int", float: "float", bool: "boolean"}


def _plain(values: Any) -> list[Any]:
    """Choice values as JSON-friendly plain values (Click 8.2+ may hold Enum members)."""
    return [v.value if isinstance(v, enum.Enum) else v for v in values]


def _annotation_choices(annotation: Any) -> list[Any] | None:
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return [m.value for m in annotation]
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    return None


def _type_and_choices(p: Any) -> tuple[str, list[Any] | None]:
    """The parameter's type name and choices, the same under every Typer version.

    Typer <0.29 (and plain Click) attach a Click ParamType as `p.type`; a `click.Choice` (given directly, as
    `click_type=`, or made by Typer from an Enum) has `.choices`. Typer 0.29+ bundles its own Click and drops
    `.type`; it keeps `p.type_descriptor` (the annotation plus the Option/Argument info) instead, so the names
    and choices Click would have given are rebuilt from that: `desc.choices`, a `click_type` on the info, an
    Enum or a Literal annotation.
    """
    ptype = getattr(p, "type", None)
    if ptype is not None:
        kind = getattr(ptype, "name", "string")
        choices = getattr(ptype, "choices", None)
        return kind, (_plain(choices or []) if choices is not None or kind == "choice" else None)
    desc = getattr(p, "type_descriptor", None)
    if desc is None:
        return "string", None
    if desc.choices is not None:
        return "choice", _plain(desc.choices)
    click_type = getattr(desc.parameter_info, "click_type", None)
    if getattr(click_type, "choices", None) is not None:
        return "choice", _plain(click_type.choices)
    annotation = desc.annotation
    if desc.is_list:  # list[X] -> X: repeatability is reported separately
        annotation = getattr(annotation, "__args__", (str,))[0]
    from_annotation = _annotation_choices(annotation)
    if from_annotation is not None:
        return "choice", from_annotation
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
    if isinstance(default, enum.Enum):
        default = default.value
    if isinstance(default, PurePath):
        default = str(default)
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
    options = [p for p in params if "flags" in p]
    flags = {f for o in options for f in o["flags"]}
    entry: dict[str, Any] = {
        "help": " ".join((cmd.help or "").split()),
        "arguments": [p for p in params if "flags" not in p],
        "options": options,
        "writes_files": prefix not in READ_ONLY,
        "has_dry_run": "--dry-run" in flags,
        "json": "--json" in flags,
        "examples": list(EXAMPLES.get(prefix, [])),
    }
    if prefix in WRITES_WITH:
        entry["writes_files_with"] = list(WRITES_WITH[prefix])
    out[prefix] = entry


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
        "statuses": STATUSES,
        "result": RESULT_SCHEMA,
        "commands": commands,
    }


def _one_of(p: dict[str, Any]) -> str:
    choices = p.get("choices")
    return ", one of " + ", ".join(f"`{c}`" for c in choices) if choices else ""


def _traits(cmd: dict[str, Any]) -> str:
    if cmd["writes_files"]:
        writes = "writes files"
    elif cmd.get("writes_files_with"):
        writes = "read only unless " + " / ".join(f"`{f}`" for f in cmd["writes_files_with"]) + " (then writes files)"
    else:
        writes = "read only"
    return f"*{writes} · " + ("has `--dry-run`" if cmd["has_dry_run"] else "no `--dry-run`") + \
        (" · `--json`" if cmd["json"] else "") + "*"


def markdown(data: dict[str, Any] | None = None) -> str:
    data = data or build()
    lines = ["# mysuite command reference", "",
             "<!-- generated by `mysuite schema --markdown`; do not edit by hand -->", "",
             "## Global options", ""]
    for g in data["global_options"]:
        lines.append(f"- `{' / '.join(g['flags'])}` - {g.get('help', '')}")
    lines += ["", "## Exit codes", ""] + [f"- `{k}` - {v}" for k, v in data["exit_codes"].items()]
    lines += ["", "## Item statuses (`items[].status`)", ""] + [f"- `{k}` - {v}" for k, v in data["statuses"].items()]
    lines += ["", "## Result document (`--json`)", "", "```json",
              __import__("json").dumps(data["result"], indent=2), "```", ""]
    for name, cmd in data["commands"].items():
        lines += [f"## `mysuite {name}`", "", cmd["help"], "", _traits(cmd), ""]
        for a in cmd["arguments"]:
            lines.append(f"- argument `{a['name']}` ({a['type']}{', required' if a['required'] else ''}"
                         f"{', repeatable' if a['repeatable'] else ''}{_one_of(a)}) {a.get('help', '')}".rstrip())
        for o in cmd["options"]:
            default = f", default `{o['default']}`" if o.get("default") not in (None, "", False) else ""
            lines.append(f"- `{' / '.join(o['flags'])}` ({o['type']}{default}{', repeatable' if o['repeatable'] else ''}"
                         f"{_one_of(o)}) {o.get('help', '')}".rstrip())
        if cmd["examples"]:
            lines += ["", "Examples:", "", "```bash", *cmd["examples"], "```"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
