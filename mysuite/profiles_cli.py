from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import tomlkit
import typer
from rich.markup import escape
from rich.table import Table

from mysuite.config import MysuiteConfigError, _find_config_path, load_config
from mysuite.profiles import Profile, ProfileError, parse_profile
from mysuite.utils import jsonout
from mysuite.utils.console import console, log_error, log_skip, log_step

app = typer.Typer(help="Company profiles: one named bundle of tokens, brand, variants, export defaults, metadata policy and allowed folders.", no_args_is_help=True)
CONFIG = typer.Option(None, "--config", "-c", help="Explicit path to mysuite.toml (default: ./mysuite.toml or ~/.config/mysuite/mysuite.toml).")


def _config(config_path: Optional[Path]):
    try:
        return load_config(config_path)
    except MysuiteConfigError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc


def _write_path(config_path: Optional[Path]) -> Path:
    if config_path:
        return config_path
    found = _find_config_path(None)
    return found or Path.cwd() / "mysuite.toml"


def _dump(profile: Profile) -> dict:
    out: dict = {}
    for key in ("description", "tokens", "brand", "theme", "variants", "compress_preset", "enhance_preset"):
        if getattr(profile, key):
            out[key] = getattr(profile, key)
    if profile.negative_map:
        out["negative_map"] = profile.negative_map
    if profile.allow:
        out["allow"] = profile.allow
    if profile.export:
        out["export"] = profile.export
    if profile.metadata:
        out["metadata"] = profile.metadata
    return out


@app.command("list", help="List the profiles in mysuite.toml.")
@jsonout.with_json("profiles-list")
def list_profiles(config_path: Optional[Path] = CONFIG) -> None:
    config = _config(config_path)
    table = Table(title="profiles")
    for col in ("name", "brand", "tokens", "variants", "metadata policy", "description"):
        table.add_column(col)
    for name in sorted(config.profiles):
        try:
            p = parse_profile(name, config.profiles[name])
        except ProfileError as exc:
            jsonout.add_item(name=name, status="invalid", error=str(exc))
            table.add_row(escape(name), "[#F87171]invalid[/]", "", "", "", escape(str(exc)))
            continue
        jsonout.add_item(name=name, status="ok", **_dump(p))
        table.add_row(escape(name), p.brand or "", escape(p.tokens or ""), p.variants or "", ",".join(p.policy), escape(p.description or ""))
    console.print(table)
    if not config.profiles:
        console.print("[dim]no profiles yet: create one with `mysuite profiles save NAME --tokens … --brand …`[/dim]")


@app.command("show", help="One profile, in full.")
@jsonout.with_json("profiles-show")
def show(name: str = typer.Argument(..., help="Profile name."), config_path: Optional[Path] = CONFIG) -> None:
    config = _config(config_path)
    if name not in config.profiles:
        log_error(f"unknown profile {escape(name)} (defined: {escape(', '.join(sorted(config.profiles)) or 'none')})")
        raise typer.Exit(1)
    try:
        p = parse_profile(name, config.profiles[name])
    except ProfileError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    data = _dump(p)
    jsonout.add_item(name=name, status="ok", **data)
    console.print(f"[bold]{escape(name)}[/bold]")
    for key, value in data.items():
        console.print(f"  {key}: {escape(str(value))}")


@app.command("save", help="Create or replace a profile in mysuite.toml. Only the options you give are stored.")
@jsonout.with_json("profiles-save")
def save(
    name: str = typer.Argument(..., help="Profile name, e.g. acme."),
    description: Optional[str] = typer.Option(None, "--description"),
    tokens: Optional[str] = typer.Option(None, "--tokens", help="Token source (file/folder, git+https://…@REF#path=…, figma:KEY)."),
    brand: Optional[str] = typer.Option(None, "--brand"),
    theme: Optional[str] = typer.Option(None, "--theme", help="light or dark."),
    variants: Optional[str] = typer.Option(None, "--variants", help="Default logo variants, e.g. default,negative."),
    negative_map: Optional[List[str]] = typer.Option(None, "--negative-map", help="LOGOCOLOUR=token:NAME (repeatable)."),
    allow: Optional[List[str]] = typer.Option(None, "--allow", help="Sandbox folder(s) this profile may read/write (repeatable)."),
    formats: Optional[str] = typer.Option(None, "--formats", help="Export formats, comma-separated."),
    sizes: Optional[str] = typer.Option(None, "--sizes", help="Export sizes, comma-separated."),
    colour_profiles: Optional[str] = typer.Option(None, "--colour-profiles", help="Export colour profiles: rgb,cmyk."),
    cmyk_mode: Optional[str] = typer.Option(None, "--cmyk-mode", help="exact, clean or clean:N."),
    cmyk_profile: Optional[str] = typer.Option(None, "--cmyk-profile", help="CMYK ICC profile path."),
    out_dir: Optional[str] = typer.Option(None, "--out-dir", help="Export output folder."),
    background: Optional[str] = typer.Option(None, "--background"),
    policy: Optional[str] = typer.Option(None, "--policy", help="Metadata policy steps, e.g. strip,credit."),
    author: Optional[str] = typer.Option(None, "--author", help="Metadata credit: author."),
    copyright_notice: Optional[str] = typer.Option(None, "--copyright", help="Metadata credit: copyright line."),
    no_ai: Optional[bool] = typer.Option(None, "--no-ai/--allow-ai", help="Metadata: record a 'no AI training/mining' declaration."),
    terms_url: Optional[str] = typer.Option(None, "--terms-url", help="Metadata declare: link to your AI-use terms."),
    compress_preset: Optional[str] = typer.Option(None, "--compress-preset"),
    enhance_preset: Optional[str] = typer.Option(None, "--enhance-preset"),
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite", help="Replace a profile of the same name."),
    config_path: Optional[Path] = CONFIG,
) -> None:
    csv = lambda s: [v.strip() for v in s.split(",") if v.strip()] if s else None
    export = {k: v for k, v in {
        "formats": csv(formats), "sizes": [int(s) if s.isdigit() else s for s in csv(sizes) or []] or None,
        "profiles": csv(colour_profiles), "cmyk_mode": cmyk_mode, "cmyk_profile": cmyk_profile,
        "out_dir": out_dir, "background": background,
    }.items() if v is not None}
    meta = {k: v for k, v in {"policy": csv(policy), "author": author, "copyright": copyright_notice, "no_ai": no_ai, "terms_url": terms_url}.items() if v is not None}
    data = {k: v for k, v in {
        "description": description, "tokens": tokens, "brand": brand, "theme": theme, "variants": variants,
        "negative_map": negative_map or None, "allow": allow or None, "compress_preset": compress_preset,
        "enhance_preset": enhance_preset, "export": export or None, "metadata": meta or None,
    }.items() if v is not None}
    try:
        parse_profile(name, data)
    except ProfileError as exc:
        log_error(escape(str(exc)))
        raise typer.Exit(1) from exc
    from mysuite import sandbox

    path = _write_path(config_path)
    sandbox.check_write(path.resolve())
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    if "profiles" not in doc:
        doc["profiles"] = tomlkit.table(is_super_table=True)
    if name in doc["profiles"] and not overwrite:
        log_error(f"profile {escape(name)} already exists in {escape(str(path))} - pass --overwrite to replace it")
        raise typer.Exit(1)
    table = tomlkit.table()
    for key, value in data.items():
        if isinstance(value, dict):
            sub = tomlkit.table()
            for k, v in value.items():
                sub[k] = v
            table[key] = sub
        else:
            table[key] = value
    doc["profiles"][name] = table
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    jsonout.add_item(name=name, output=path, status="written", **data)
    log_step(f"profile {escape(name)} saved to {escape(str(path))}")


@app.command("delete", help="Remove a profile from mysuite.toml.")
@jsonout.with_json("profiles-delete")
def delete(name: str = typer.Argument(...), config_path: Optional[Path] = CONFIG) -> None:
    from mysuite import sandbox

    path = _find_config_path(config_path) if config_path is None else config_path
    if path is None or not path.exists():
        log_error("no mysuite.toml found")
        raise typer.Exit(1)
    sandbox.check_write(path.resolve())
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    if "profiles" not in doc or name not in doc["profiles"]:
        log_error(f"unknown profile {escape(name)}")
        raise typer.Exit(1)
    del doc["profiles"][name]
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    jsonout.add_item(name=name, status="deleted")
    log_step(f"profile {escape(name)} removed")


@app.command("check", help="Check a profile works: valid keys, the token source loads, the brand exists, folders exist. Git and Figma sources are only fetched with --online.")
@jsonout.with_json("profiles-check")
def check(
    name: str = typer.Argument(...),
    online: bool = typer.Option(False, "--online", help="Also fetch git/figma token sources (network)."),
    config_path: Optional[Path] = CONFIG,
) -> None:
    from mysuite.tokens.model import TokenError
    from mysuite.tokens.sources import load_tokens

    config = _config(config_path)
    problems = 0

    def report(what: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        problems += 0 if ok else 1
        jsonout.add_item(check=what, status="ok" if ok else "problem", detail=detail)
        (log_step if ok else log_error)(escape(f"{what}{': ' + detail if detail else ''}"))

    if name not in config.profiles:
        log_error(f"unknown profile {escape(name)} (defined: {escape(', '.join(sorted(config.profiles)) or 'none')})")
        raise typer.Exit(1)
    try:
        p = parse_profile(name, config.profiles[name])
        report("profile is valid", True)
    except ProfileError as exc:
        report("profile is valid", False, str(exc))
        raise typer.Exit(1) from exc
    if p.tokens:
        remote = p.tokens.startswith(("git+", "figma:"))
        if remote and not online:
            log_skip("token source is remote: not fetched (use --online)")
            jsonout.add_warning("token source is remote: not fetched")
        else:
            try:
                loaded = load_tokens(p.tokens)
                report("token source loads", True, f"{len(loaded.tokens)} tokens")
                try:
                    loaded.tokens.resolve_brand(p.brand)
                    report("brand is available", True, p.brand or "(single or none)")
                except TokenError as exc:
                    report("brand is available", False, str(exc))
            except TokenError as exc:
                report("token source loads", False, str(exc))
    for folder in p.allow:
        exists = Path(folder).expanduser().exists()
        report(f"allowed folder exists: {folder}", exists)
    if p.policy:
        from mysuite.doctor import check_tools

        found = check_tools(config.tools)
        needs = {"strip": ("exiftool", "magick"), "randomize": ("exiftool", "magick"), "credit": ("c2patool",)}
        for step in p.policy:
            missing = [t for t in needs[step] if not found.get(t)]
            report(f"metadata step '{step}' has its tools", not missing, ", ".join(missing))
    if problems:
        raise typer.Exit(1)
