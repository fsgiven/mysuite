from __future__ import annotations

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

DEFAULT_BUNDLE_NAMING_TEMPLATE = "{name}{ext}"

# Variants used when the "date stamp" option is on and the naming/bundle template
# is still the tool's own default — see resolve_naming_templates().
DATE_STAMPED_NAMING_TEMPLATE = "{name}_{size}_{date}{ext}"
DATE_STAMPED_BUNDLE_NAMING_TEMPLATE = "{name}_{date}{ext}"

# Used when --variant is set and path_template is still the tool's own default —
# see resolve_path_template_for_variant(). Nests output under the SAME name
# folder as the primary export, one level down per variant.
VARIANT_PATH_TEMPLATE = "{variant}/{format}/{colorspace}/{naming}"


def today_stamp() -> str:
    return date.today().strftime("%Y%m%d")


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


def render_filename(
    naming_template: str,
    *,
    name: str,
    size: Size,
    format: str,
    colorspace: str,
    variant: str = "",
) -> str:
    ext = FORMAT_EXTENSIONS[format]
    return naming_template.format(
        name=name,
        size=size.label,
        size_px=size.pixels,
        width=size.label,
        height=size.label,
        format=format,
        colorspace=colorspace,
        ext=ext,
        date=today_stamp(),
        variant=variant,
    )


def render_path(
    path_template: str,
    naming_template: str,
    *,
    out_dir: Path,
    name: str,
    size: Size,
    format: str,
    colorspace: str,
    variant: str = "",
) -> Path:
    filename = render_filename(
        naming_template, name=name, size=size, format=format, colorspace=colorspace, variant=variant
    )
    ext = FORMAT_EXTENSIONS[format]
    relative = path_template.format(
        name=name,
        size=size.label,
        size_px=size.pixels,
        width=size.label,
        height=size.label,
        format=format,
        colorspace=colorspace,
        ext=ext,
        naming=filename,
        date=today_stamp(),
        variant=variant,
    )
    return out_dir / name / Path(relative)


def render_bundle_filename(
    bundle_naming_template: str,
    *,
    name: str,
    format: str,
    colorspace: str,
    variant: str = "",
) -> str:
    ext = FORMAT_EXTENSIONS[format]
    return bundle_naming_template.format(
        name=name,
        format=format,
        colorspace=colorspace,
        ext=ext,
        date=today_stamp(),
        variant=variant,
    )


def render_bundle_path(
    path_template: str,
    bundle_naming_template: str,
    *,
    out_dir: Path,
    name: str,
    format: str,
    colorspace: str,
    variant: str = "",
) -> Path:
    filename = render_bundle_filename(
        bundle_naming_template, name=name, format=format, colorspace=colorspace, variant=variant
    )
    ext = FORMAT_EXTENSIONS[format]
    # Bundle paths have no meaningful {size}/{size_px} — a bundle covers several
    # sizes at once — so those tokens resolve to empty if a custom path_template
    # references them.
    relative = path_template.format(
        name=name,
        size="",
        size_px="",
        width="",
        height="",
        format=format,
        colorspace=colorspace,
        ext=ext,
        naming=filename,
        date=today_stamp(),
        variant=variant,
    )
    return out_dir / name / Path(relative)
