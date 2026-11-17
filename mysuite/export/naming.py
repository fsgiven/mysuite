from __future__ import annotations

import re
import string
from datetime import date
from pathlib import Path

from mysuite.export.units import Size

FORMAT_EXTENSIONS = {
    "png": ".png",
    "pdf": ".pdf",
    "eps": ".eps",
    "svg": ".svg",
    "jpeg": ".jpg",
    "webp": ".webp",
    "tiff": ".tiff",
    "ico": ".ico",
    "icns": ".icns",
}

DEFAULT_NAMING_TEMPLATE = "{name}_{variant}_{size}{ext}"
DEFAULT_PATH_TEMPLATE = "{format}/{colorspace}/{naming}"
DEFAULT_BUNDLE_NAMING_TEMPLATE = "{name}_{variant}{ext}"

# Variants used when the "date stamp" option is on and the naming/bundle template
# is still the tool's own default — see resolve_naming_templates().
DATE_STAMPED_NAMING_TEMPLATE = "{name}_{variant}_{size}_{date}{ext}"
DATE_STAMPED_BUNDLE_NAMING_TEMPLATE = "{name}_{variant}_{date}{ext}"

# Used when --variant is set and path_template is still the tool's own default —
# see resolve_path_template_for_variant(). Nests output under the SAME name
# folder as the primary export, one level down per variant.
VARIANT_PATH_TEMPLATE = "{variant}/{format}/{colorspace}/{naming}"

# Every token a file-name template may use; {naming} (the rendered file name) only in the path template.
# Write a token in CAPITALS ({FORMAT}, {COLORSPACE}, {VARIANT}, ...) to get its value in capitals.
NAME_TOKENS = ("name", "variant", "size", "size_px", "width", "height", "format", "colorspace", "ext", "date")
PATH_TOKENS = (*NAME_TOKENS, "naming")

_EMPTY = "\x00"          # marks a token that came out empty, so its separator can go with it
_SEPARATORS = "_- "


class NamingError(ValueError):
    """A naming/path template that can't be filled: unknown token, broken brace, a value that isn't known."""


def today_stamp() -> str:
    return date.today().strftime("%Y%m%d")


def _collapse(text: str) -> str:
    """Drop the separator next to every empty token, then empty folder segments: with no variant,
    `{name}_{variant}_{size}{ext}` is `logo_512.png`, not `logo__512.png`; `{variant}/{format}` is `png`."""
    sep = re.escape(_SEPARATORS)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(rf"[{sep}]{_EMPTY}(?=[{sep}./{_EMPTY}]|$)", "", text)
        text = re.sub(rf"(^|/){_EMPTY}[{sep}]", r"\1", text)
    text = text.replace(_EMPTY, "")
    return "/".join(part for part in text.split("/") if part)


def fill(template: str, values: dict, *, what: str = "naming template") -> str:
    """Fill a template. `values[token]` None means "not known here" (e.g. {width} of an icon bundle)."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise NamingError(
            f"{what} {template!r}: {exc} - tokens look like {{name}}; write {{{{ or }}}} for a literal brace"
        ) from None
    out: list[str] = []
    for literal, token, spec, conversion in parsed:
        out.append(literal)
        if token is None:
            continue
        key = token.lower() if token.isupper() else token
        if key not in values or conversion:
            known = ", ".join("{" + k + "}" for k in values)
            raise NamingError(f"{what} {template!r}: unknown token {{{token}}} - use one of {known} "
                              "(in CAPITALS for a capitalised value, e.g. {FORMAT})")
        value = values[key]
        if value is None:
            raise NamingError(f"{what} {template!r}: {{{token}}} isn't known for this file")
        try:
            text = format(value, spec) if spec else str(value)
        except ValueError as exc:
            raise NamingError(f"{what} {template!r}: {{{token}:{spec}}}: {exc}") from None
        if token.isupper():
            text = text.upper()
        out.append(text if text != "" else _EMPTY)
    return _collapse("".join(out))


def check_template(template: str, *, path: bool = False) -> None:
    """Fail early (before anything is planned) on an unknown token or a broken brace."""
    tokens = PATH_TOKENS if path else NAME_TOKENS
    fill(template, {t: "x" for t in tokens}, what="path template" if path else "naming template")


def resolve_naming_templates(
    naming_template: str,
    bundle_naming_template: str,
    *,
    date_stamp: bool,
    default_naming_template: str,
) -> tuple[str, str]:
    """If date_stamp is on and a template is still exactly the tool's built-in
    default, swaps in the date-stamped built-in variant. A template the user has
    already customized (in config, a preset, or the CLI/TUI) is left as-is —
    {date} is available as a token there too, so a custom template can place it
    wherever it likes rather than having one silently appended."""
    if not date_stamp:
        return naming_template, bundle_naming_template
    resolved_naming = (
        DATE_STAMPED_NAMING_TEMPLATE if naming_template == default_naming_template else naming_template
    )
    resolved_bundle = (
        DATE_STAMPED_BUNDLE_NAMING_TEMPLATE
        if bundle_naming_template == DEFAULT_BUNDLE_NAMING_TEMPLATE
        else bundle_naming_template
    )
    return resolved_naming, resolved_bundle


def resolve_path_template_for_variant(
    path_template: str, *, variant: str | None, default_path_template: str
) -> str:
    """If a --variant is given and path_template is still the tool's own
    default, nests output one level deeper under {variant} so e.g. a "negative"
    variant of "logo" lands in exports/logo/negative/... alongside the primary
    exports/logo/... export instead of getting its own top-level name folder.
    A path_template already customized is left as-is — {variant} is available
    as a token there too."""
    if not variant:
        return path_template
    return VARIANT_PATH_TEMPLATE if path_template == default_path_template else path_template


def _values(*, name: str, size: Size | None, format: str, colorspace: str, variant: str,
            width: int | None, height: int | None) -> dict:
    artwork = size is None or size.is_artwork
    return {
        "name": name,
        "variant": variant,
        "size": "" if artwork else size.label,
        "size_px": "" if artwork else size.pixels,
        "width": width,
        "height": height,
        "format": format,
        "colorspace": colorspace,
        "ext": FORMAT_EXTENSIONS[format],
        "date": today_stamp(),
    }


def render_filename(
    naming_template: str,
    *,
    name: str,
    size: Size | None,
    format: str,
    colorspace: str,
    variant: str = "",
    width: int | None = None,
    height: int | None = None,
) -> str:
    values = _values(name=name, size=size, format=format, colorspace=colorspace, variant=variant, width=width, height=height)
    filename = fill(naming_template, values)
    if not filename:
        raise NamingError(f"naming template {naming_template!r} gives an empty file name")
    return filename


def render_path(
    path_template: str,
    naming_template: str,
    *,
    out_dir: Path,
    name: str,
    size: Size | None,
    format: str,
    colorspace: str,
    variant: str = "",
    width: int | None = None,
    height: int | None = None,
    name_folder: bool = True,
) -> Path:
    filename = render_filename(
        naming_template, name=name, size=size, format=format, colorspace=colorspace, variant=variant,
        width=width, height=height,
    )
    values = _values(name=name, size=size, format=format, colorspace=colorspace, variant=variant, width=width, height=height)
    return _join(out_dir, name, name_folder, path_template, fill(path_template, {**values, "naming": filename}, what="path template"))


def render_bundle_filename(
    bundle_naming_template: str,
    *,
    name: str,
    format: str,
    colorspace: str,
    variant: str = "",
) -> str:
    values = _values(name=name, size=None, format=format, colorspace=colorspace, variant=variant, width=None, height=None)
    return fill(bundle_naming_template, values, what="bundle naming template")


def render_bundle_path(
    path_template: str,
    bundle_naming_template: str,
    *,
    out_dir: Path,
    name: str,
    format: str,
    colorspace: str,
    variant: str = "",
    name_folder: bool = True,
) -> Path:
    filename = render_bundle_filename(
        bundle_naming_template, name=name, format=format, colorspace=colorspace, variant=variant
    )
    # Bundle paths have no meaningful {size}/{size_px}/{width}/{height} — a bundle covers
    # several sizes at once — so those tokens resolve to empty.
    values = _values(name=name, size=None, format=format, colorspace=colorspace, variant=variant, width="", height="")
    return _join(out_dir, name, name_folder, path_template, fill(path_template, {**values, "naming": filename}, what="path template"))


def _join(out_dir: Path, name: str, name_folder: bool, path_template: str, relative: str) -> Path:
    if path_template.lstrip().startswith(("/", "\\")):
        return Path(path_template)        # absolute: the planner's confinement check refuses it with a clear message
    if not relative:
        raise NamingError(f"path template {path_template!r} gives an empty path")
    return (out_dir / name if name_folder else out_dir) / Path(relative)
