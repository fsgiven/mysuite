from __future__ import annotations

import json
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mysuite.config import ToolPaths
from mysuite.metadata._parsing import output_path_for
from mysuite.metadata.camera_profiles import build_decoy_tags
from mysuite.metadata.hidden import CREDIT_TAGS
from mysuite.utils.subprocess_utils import atomic_write_via, run


@dataclass
class MetadataOutcome:
    input_path: Path
    output_path: Path
    status: str  # "written" | "skipped_existing"


def _tmp_sibling(input_path: Path, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"mysuite-metadata-{input_path.stem}-", delete=False
    )
    handle.close()
    return Path(handle.name)


_TIFF_SUFFIXES = {".tif", ".tiff"}


def _strip_into(input_path: Path, tmp_path: Path, tools: ToolPaths, *, keep_icc: bool = True, keep_credit: bool = False) -> None:
    """Writes a copy of input_path without hidden data (hidden.REMOVED) to tmp_path.

    exiftool -all= removes everything except what's structurally required; then some tags are copied back from the
    original:
    - Orientation: a 1-8 rotation flag with no identifying content; dropping it leaves phone photos sideways.
    - ICC_Profile (unless keep_icc=False): the colour profile. Without it a Display P3 or Adobe RGB photo is read as
      sRGB and looks dull or wrong. It describes colours, not the person or the camera.
    - the credit fields (hidden.CREDIT_TAGS) with keep_credit=True: author, copyright, credit line, source.
    exiftool can't delete a TIFF's IFD0, so Artist/Copyright/Software/ImageDescription would survive; TIFFs get a
    magick -strip pass first (with -auto-orient so the rotation is baked into the pixels instead, since magick -strip
    drops the flag too)."""
    source = input_path
    mid: Path | None = None
    if input_path.suffix.lower() in _TIFF_SUFFIXES:
        mid = _tmp_sibling(input_path, input_path.suffix)
        run([tools.magick, str(input_path), "-auto-orient", "-strip", str(mid)])
        source = mid
    copy_back = (["-ICC_Profile"] if keep_icc else []) + ([f"-{t}" for t in CREDIT_TAGS] if keep_credit else [])
    try:
        run([tools.exiftool, "-charset", "iptc=UTF8", "-all=", "-tagsfromfile", "@", "-Orientation#",
             *(["-tagsfromfile", str(input_path), *copy_back] if copy_back else []),
             *(["-IPTC:CodedCharacterSet=UTF8"] if keep_credit else []),
             str(source), "-o", str(tmp_path)])
    finally:
        if mid is not None:
            mid.unlink(missing_ok=True)


def strip_file(input_path: Path, *, tools: ToolPaths, overwrite: bool = False, keep_icc: bool = True,
               keep_credit: bool = False) -> MetadataOutcome:
    """Removes hidden data (hidden.REMOVED) via exiftool, writing a new file beside the source (name_stripped.ext) —
    never mutates the source. The colour profile is kept unless keep_icc=False; keep_credit=True keeps the author,
    copyright and credit fields. A PDF goes through the PDF engine (mysuite.pdf.ops.strip_metadata): exiftool only
    appends an update to a PDF, so the old title and author would still be inside the file.
    Because the output is a brand-new file, macOS extended attributes on the source (quarantine flag, download-URL
    "where from") don't carry over. This does not necessarily remove a C2PA provenance manifest (a JUMBF segment
    exiftool's model doesn't fully own), so a credited-then-stripped file may still carry a C2PA claim."""
    output_path = output_path_for(input_path, "stripped")

    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")

    if input_path.suffix.lower() == ".pdf":
        from mysuite.pdf import ops

        ops.strip_metadata(input_path, output_path, overwrite=True)
        return MetadataOutcome(input_path, output_path, "written")

    atomic_write_via(output_path, lambda tmp: _strip_into(input_path, tmp, tools, keep_icc=keep_icc, keep_credit=keep_credit),
                     preserve_extension=True)
    return MetadataOutcome(input_path, output_path, "written")


def randomize_file(
    input_path: Path,
    *,
    tools: ToolPaths,
    overwrite: bool = False,
    rng: random.Random | None = None,
) -> MetadataOutcome:
    """Strips everything (same as strip_file), then writes one internally
    consistent decoy camera identity — a single real device profile's make,
    model, lens and firmware plus plausible per-photo exposure values and a
    recent capture time (see camera_profiles.build_decoy_tags). Writes a new
    file beside the source (name_randomized.ext); never mutates the source.

    This hides the real capture device in the metadata. It does not defeat
    image forensics: sensor-noise fingerprints, thumbnails and maker-note
    structure a real camera would add are not reproduced."""
    output_path = output_path_for(input_path, "randomized")

    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")

    _, tags = build_decoy_tags(rng)

    def write(tmp_path: Path) -> None:
        _strip_into(input_path, tmp_path, tools)
        args = [f"-EXIF:{name}={value}" for name, value in tags.items()]
        run([tools.exiftool, "-overwrite_original", *args, str(tmp_path)])

    # exiftool picks the writable format from the file extension on some
    # types, so keep the real one on the temp path (see atomic_write_via).
    atomic_write_via(output_path, write, preserve_extension=True)
    return MetadataOutcome(input_path, output_path, "written")


NO_AI_ENTRIES = ("c2pa.ai_generative_training", "c2pa.ai_training", "c2pa.ai_inference", "c2pa.data_mining")

# PLUS data-mining vocabulary, spelled the way exiftool writes it (value -> DMI-... code)
DATA_MINING = {
    "prohibited": "Prohibited",
    "prohibited-ai-training": "Prohibited for AI/ML training",
    "prohibited-genai-training": "Prohibited for Generative AI/ML training",
}


def _build_manifest(*, author: str, copyright_notice: str | None, generator: str, no_ai: bool = False) -> dict:
    creative_work: dict = {
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "author": [{"@type": "Person", "name": author}],
    }
    if copyright_notice:
        creative_work["copyrightNotice"] = copyright_notice
    assertions: list[dict] = [{"label": "stds.schema-org.CreativeWork", "data": creative_work}]
    if no_ai:
        assertions.append({"label": "c2pa.training-mining", "data": {"entries": {e: {"use": "notAllowed"} for e in NO_AI_ENTRIES}}})
    return {"claim_generator": generator, "assertions": assertions}


def credit_file(
    input_path: Path,
    *,
    author: str,
    copyright_notice: str | None = None,
    generator: str = "mysuite",
    tools: ToolPaths,
    overwrite: bool = False,
    no_ai: bool = False,
) -> MetadataOutcome:
    """Embeds a signed C2PA provenance manifest (author/copyright/generator)
    via c2patool, writing a new file beside the source (name_credited.ext).
    Uses c2patool's built-in test certificate — real enough to embed and read
    back a provenance record for personal/internal verification, but NOT
    third-party-trusted (that needs a certificate from an accredited CA,
    which is the caller's own separate step, not something this obtains).

    Note: c2patool 0.27.15's own read-back report always shows its own tool
    identity under claim_generator_info, regardless of what generator names
    here — that field isn't independently observable via `c2patool <file>`
    on this version. author/copyright_notice (the CreativeWork assertion) do
    reliably read back correctly — that's the part that matters for
    correct crediting."""
    output_path = output_path_for(input_path, "credited")

    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")

    manifest = _build_manifest(author=author, copyright_notice=copyright_notice, generator=generator, no_ai=no_ai)
    manifest_path = _tmp_sibling(input_path, ".json")
    manifest_path.write_text(json.dumps(manifest))

    try:
        def write(tmp_path: Path) -> None:
            run([tools.c2patool, str(input_path), "-m", str(manifest_path), "-o", str(tmp_path)])

        # c2patool infers the output format from the file extension, so the
        # temp path must still end in the real extension (see
        # atomic_write_via's preserve_extension docstring).
        atomic_write_via(output_path, write, preserve_extension=True)
    finally:
        manifest_path.unlink(missing_ok=True)

    return MetadataOutcome(input_path, output_path, "written")


def declare_file(
    input_path: Path,
    *,
    policy: str = "prohibited",
    owner: str | None = None,
    terms_url: str | None = None,
    tools: ToolPaths,
    overwrite: bool = False,
) -> MetadataOutcome:
    """Writes a machine-readable "no AI use" declaration into a COPY (name_declared.ext): the PLUS DataMining property,
    XMP Rights (Marked, UsageTerms, WebStatement) and optionally the owner. Honoured only by crawlers and tools that
    choose to read it - it is a legal/consent signal, not a technical block."""
    if policy not in DATA_MINING:
        raise ValueError(f"policy must be one of {', '.join(DATA_MINING)}")
    output_path = output_path_for(input_path, "declared")
    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")
    terms = "No use for AI/ML training, generative AI, or data mining without the owner's written permission."
    args = [
        f"-XMP-plus:DataMining={DATA_MINING[policy]}",
        "-XMP-xmpRights:Marked=True",
        f"-XMP-xmpRights:UsageTerms={terms}",
    ]
    if terms_url:
        args.append(f"-XMP-xmpRights:WebStatement={terms_url}")
    if owner:
        args += [f"-XMP-plus:CopyrightOwnerName={owner}", f"-XMP-xmpRights:Owner={owner}"]

    def write(tmp_path: Path) -> None:
        import shutil

        shutil.copyfile(input_path, tmp_path)
        run([tools.exiftool, "-q", "-overwrite_original", "-m", *args, str(tmp_path)])

    atomic_write_via(output_path, write, preserve_extension=True)
    return MetadataOutcome(input_path, output_path, "written")
