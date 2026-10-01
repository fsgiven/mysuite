from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mysuite.export.units import Size


@dataclass(frozen=True)
class ExportJob:
    size: Size
    format: str
    colorspace: str
    output_path: Path


@dataclass(frozen=True)
class BundleJob:
    """A single output file that packs multiple sizes together (ico, icns) —
    as opposed to ExportJob, which is one file per size."""

    sizes: tuple[Size, ...]
    format: str
    colorspace: str
    output_path: Path


@dataclass(frozen=True)
class SkipReason:
    format: str
    colorspace: str
    reason: str


@dataclass
class ExportPlan:
    jobs: list[ExportJob]
    skips: list[SkipReason]
    bundle_jobs: list[BundleJob] = field(default_factory=list)
