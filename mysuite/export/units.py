from __future__ import annotations

import re
from dataclasses import dataclass

UNITS_PER_INCH = {
    "in": 1.0,
    "cm": 1 / 2.54,
    "mm": 1 / 25.4,
}

VALID_UNITS = {"px", "mm", "cm", "in"}

_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)(px|mm|cm|in)?$", re.IGNORECASE)


class InvalidSizeError(ValueError):
    pass


class InvalidPaddingError(ValueError):
    pass


@dataclass(frozen=True)
class Size:
    label: str
    """Original user-provided spec (e.g. "512", "5cm") — used for filenames and
    passed straight through to rsvg-convert's --width/--height, which understands
    the same unit suffixes natively."""

    pixels: int
    """Resolved pixel-equivalent (via dpi for physical units) — used for sorting,
    grouping, and the {size_px} naming token."""


def parse_size(raw: str | int | float, *, dpi: float, default_unit: str = "px") -> Size:
    """Parses one size spec. If raw has no unit suffix, default_unit applies —
    and for a non-px default_unit, the resolved unit is appended to the label
    (e.g. "50" with default_unit="mm" labels as "50mm") so filenames stay
    unambiguous about what unit was actually used."""
    if default_unit not in VALID_UNITS:
        raise InvalidSizeError(f"invalid default unit {default_unit!r} — expected one of px, mm, cm, in")

    stripped = str(raw).strip()
    match = _SIZE_RE.match(stripped)
    if not match:
        raise InvalidSizeError(
            f"invalid size {raw!r} — expected a number, optionally suffixed with "
            f"px, mm, cm, or in (e.g. 512, 5cm, 2in)"
        )
    value_str, explicit_unit = match.groups()
    value = float(value_str)

    if explicit_unit is None:
        unit = default_unit
        label = f"{value_str}{unit}" if unit != "px" else value_str
    else:
        unit = explicit_unit.lower()
        label = stripped

    if unit == "px":
        if value != int(value):
            raise InvalidSizeError(f"pixel sizes must be whole numbers: {raw!r}")
        pixels = int(value)
    else:
        pixels = round(value * UNITS_PER_INCH[unit] * dpi)

    if pixels <= 0:
        raise InvalidSizeError(f"size must be positive: {raw!r}")

    return Size(label=label, pixels=pixels)


_PADDING_RE = re.compile(r"^(\d+(?:\.\d+)?)(%|px)?$", re.IGNORECASE)


def parse_padding(value: str, *, reference_px: int) -> int:
    """Parses a padding/margin spec ("10%" or "20"/"20px") into whole pixels, "%"
    relative to reference_px (the size the margin is being added to)."""
    stripped = value.strip()
    match = _PADDING_RE.match(stripped)
    if not match:
        raise InvalidPaddingError(
            f"invalid padding {value!r} — expected e.g. 20, 20px, or 10%"
        )
    value_str, unit = match.groups()
    amount = float(value_str)
    if (unit or "").lower() == "%":
        pixels = round(reference_px * amount / 100)
    else:
        pixels = round(amount)
    if pixels < 0:
        raise InvalidPaddingError(f"padding must not be negative: {value!r}")
    return pixels


@dataclass(frozen=True)
class Margin:
    """Resolved margin in whole pixels, one value per side — a safe-space border
    around the artwork. With no background color it stays transparent rather
    than forcing a colored box."""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0

    @property
    def is_zero(self) -> bool:
        return self.top == 0 and self.right == 0 and self.bottom == 0 and self.left == 0


def resolve_margin(
    top: str | None, right: str | None, bottom: str | None, left: str | None, *, reference_px: int
) -> Margin:
    """Resolves 4 already-defaulted side specs (each None means "no margin on
    this side") into pixels. Callers are responsible for falling a side back to
    a uniform --padding/--margin baseline before calling this, if desired."""
    return Margin(
        top=parse_padding(top, reference_px=reference_px) if top else 0,
        right=parse_padding(right, reference_px=reference_px) if right else 0,
        bottom=parse_padding(bottom, reference_px=reference_px) if bottom else 0,
        left=parse_padding(left, reference_px=reference_px) if left else 0,
    )
