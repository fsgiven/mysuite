from __future__ import annotations

import json
import random
import tempfile
import shutil
from dataclasses import dataclass, field
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
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


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


C2PA_TEST_CERT = "signed with c2patool's built-in test certificate: it proves the record is intact, not who you are (verifiers show it as untrusted)"
C2PA_MISSING = "not added: c2patool is not installed"


def c2pa_tool(tools: ToolPaths) -> str | None:
    """c2patool's path when installed. C2PA is optional: the IPTC credit is always written without it."""
    return shutil.which(tools.c2patool)


def credit_args(*, author: str, copyright_notice: str | None = None, credit_line: str | None = None, source: str | None = None,
                usage_terms: str | None = None) -> list[str]:
    """exiftool arguments for the IPTC Core credit, in the three places picture desks and DAMs read it: EXIF, IPTC IIM
    and XMP (Dublin Core / Photoshop / xmpRights). Values replace earlier ones; nothing else in the file changes."""
    args = [f"-EXIF:Artist={author}", f"-IPTC:By-line={author}", f"-XMP-dc:Creator={author}"]
    if copyright_notice:
        args += [f"-EXIF:Copyright={copyright_notice}", f"-IPTC:CopyrightNotice={copyright_notice}",
                 f"-XMP-dc:Rights={copyright_notice}", "-XMP-xmpRights:Marked=True"]
    if credit_line:
        args += [f"-IPTC:Credit={credit_line}", f"-XMP-photoshop:Credit={credit_line}"]
    if source:
        args += [f"-IPTC:Source={source}", f"-XMP-photoshop:Source={source}"]
    if usage_terms:
        args += [f"-XMP-xmpRights:UsageTerms={usage_terms}"]
    return args


def _exiftool_write(tools: ToolPaths, path: Path, args: list[str]) -> None:
    """Writes tags in place on a working copy. -m: a format without IPTC IIM (PNG, WebP) still gets EXIF and XMP."""
    run([tools.exiftool, "-q", "-q", "-m", "-overwrite_original", "-charset", "iptc=UTF8", "-codedcharacterset=UTF8",
         *args, str(path)])


def c2pa_sign(src: Path, dst: Path, *, tools: ToolPaths, tool: str, author: str, copyright_notice: str | None,
              generator: str = "mysuite", no_ai: bool = False) -> None:
    manifest = _build_manifest(author=author, copyright_notice=copyright_notice, generator=generator, no_ai=no_ai)
    manifest_path = _tmp_sibling(src, ".json")
    manifest_path.write_text(json.dumps(manifest))
    try:
        run([tool, str(src), "-m", str(manifest_path), "-o", str(dst)])
    finally:
        manifest_path.unlink(missing_ok=True)


def credit_file(
    input_path: Path,
    *,
    author: str,
    copyright_notice: str | None = None,
    credit_line: str | None = None,
    source: str | None = None,
    usage_terms: str | None = None,
    generator: str = "mysuite",
    tools: ToolPaths,
    overwrite: bool = False,
    no_ai: bool = False,
    c2pa: bool = True,
) -> MetadataOutcome:
    """Writes the credit into a copy (name_credited.ext):
    1. always, with exiftool: IPTC Core author (By-line, dc:creator, Artist), copyright notice (dc:rights), credit
       line, source and rights usage terms (credit_args); with no_ai also the PLUS "no data mining" declaration;
    2. then, only when c2patool is installed (and c2pa=True), a C2PA manifest with the same author/copyright, signed
       LAST so the record covers the finished file. c2patool's built-in test certificate is used: the record reads
       back and shows tampering, but verifiers show the signer as untrusted (a real certificate is your own step).
    The result says which happened (extra["c2pa"])."""
    output_path = output_path_for(input_path, "credited")

    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")

    args = credit_args(author=author, copyright_notice=copyright_notice, credit_line=credit_line, source=source,
                       usage_terms=usage_terms)
    if no_ai:      # the same "no AI" declaration as `metadata declare`, without replacing your own usage terms
        args += [a for a in declare_args(policy="prohibited", owner=author)
                 if not (usage_terms and a.startswith("-XMP-xmpRights:UsageTerms="))]
    tool = c2pa_tool(tools) if c2pa else None
    state = {"c2pa": C2PA_TEST_CERT if tool else (C2PA_MISSING if c2pa else "not added (switched off)")}

    def write(tmp_path: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="mysuite-credit-") as d:
            stage = Path(d) / f"stage{input_path.suffix}"
            shutil.copyfile(input_path, stage)
            _exiftool_write(tools, stage, args)
            if tool:
                c2pa_sign(stage, tmp_path, tools=tools, tool=tool, author=author, copyright_notice=copyright_notice,
                          generator=generator, no_ai=no_ai)
            else:
                shutil.copyfile(stage, tmp_path)

    # c2patool infers the output format from the extension, so the temp path keeps the real one.
    atomic_write_via(output_path, write, preserve_extension=True)
    notes = [] if tool else ([f"C2PA {C2PA_MISSING} (the IPTC credit was written)"] if c2pa else [])
    return MetadataOutcome(input_path, output_path, "written", notes=notes, extra=state)


def declare_args(*, policy: str = "prohibited", owner: str | None = None, terms_url: str | None = None) -> list[str]:
    if policy not in DATA_MINING:
        raise ValueError(f"policy must be one of {', '.join(DATA_MINING)}")
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
    return args


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
    args = declare_args(policy=policy, owner=owner, terms_url=terms_url)
    output_path = output_path_for(input_path, "declared")
    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")
    def write(tmp_path: Path) -> None:
        shutil.copyfile(input_path, tmp_path)
        run([tools.exiftool, "-q", "-overwrite_original", "-m", *args, str(tmp_path)])

    atomic_write_via(output_path, write, preserve_extension=True)
    notes = [GPS_KEPT] if has_gps(output_path, tools) else []
    return MetadataOutcome(input_path, output_path, "written", notes=notes)


GPS_KEPT = ("the GPS location is still in this file (declare only adds the notice); "
            "use `metadata protect` or `metadata strip --keep-credit` to remove it")


def has_gps(path: Path, tools: ToolPaths) -> bool:
    out = run([tools.exiftool, "-q", "-q", "-s3", "-GPSLatitude", "-GPSLongitude", "-GPSPosition", str(path)]).stdout
    return bool(out.strip())


# ------------------------------------------------------------------------------------- several steps, one file
# "protect" is the Protect & credit flow: private data out (credit kept), your credit in, the no-AI notice, and the
# invisible mark when a key is given. The mark goes before the metadata steps (it copies the metadata anyway) and the
# C2PA record is always signed last, so it covers the finished file.
PROTECT_STEPS = ("strip-private", "credit", "declare")
STEP_NAMES = {"strip": "stripped", "strip-private": "stripped", "credit": "credited", "declare": "declared",
              "mark": "marked", "randomize": "randomized"}


@dataclass
class ChainOptions:
    author: str | None = None
    copyright_notice: str | None = None
    credit_line: str | None = None
    source: str | None = None
    usage_terms: str | None = None
    owner: str | None = None
    terms_url: str | None = None
    declare_policy: str = "prohibited"
    no_ai: bool = False
    mark_key: str | None = None
    mark_id: str | None = None
    c2pa: bool = True
    generator: str = "mysuite"


def expand_steps(steps: list[str], *, mark: bool = False) -> list[str]:
    """'protect' -> strip-private, (mark,) credit, declare; duplicates dropped, order kept."""
    out: list[str] = []
    for step in steps:
        for s in ((PROTECT_STEPS[0], *(("mark",) if mark else ()), *PROTECT_STEPS[1:]) if step == "protect" else (step,)):
            if s not in out:
                out.append(s)
    return out


def chain_output_path(input_path: Path, steps: list[str], requested: list[str]) -> Path:
    if requested == ["protect"]:
        return output_path_for(input_path, "protected")
    return output_path_for(input_path, "_".join(dict.fromkeys(STEP_NAMES[s] for s in steps)))


def run_chain(input_path: Path, steps: list[str], *, output_path: Path, tools: ToolPaths, opts: ChainOptions,
              overwrite: bool = False, rng: random.Random | None = None) -> MetadataOutcome:
    """Runs the steps on working copies in a temporary folder and writes ONE file, output_path; nothing else is left
    behind, even when a step fails."""
    if not overwrite and output_path.exists():
        return MetadataOutcome(input_path, output_path, "skipped_existing")
    if input_path.suffix.lower() == ".pdf":
        raise ValueError("metadata apply/protect work on pictures; for a PDF use `metadata strip` or `pdf strip`")
    notes: list[str] = []
    tool = c2pa_tool(tools) if (opts.c2pa and "credit" in steps) else None
    extra: dict = {"steps": steps}
    if "credit" in steps:
        extra["c2pa"] = C2PA_TEST_CERT if tool else (C2PA_MISSING if opts.c2pa else "not added (switched off)")
        if opts.c2pa and not tool:
            notes.append(f"C2PA {C2PA_MISSING} (the IPTC credit was written)")

    def write(tmp_path: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="mysuite-protect-") as d:
            current = input_path
            for n, step in enumerate(steps):
                nxt = Path(d) / f"{n}{input_path.suffix}"
                _run_step(step, current, nxt, tools=tools, opts=opts, notes=notes, rng=rng)
                current = nxt
            if tool:
                c2pa_sign(current, tmp_path, tools=tools, tool=tool, author=opts.author or "", generator=opts.generator,
                          copyright_notice=opts.copyright_notice, no_ai=opts.no_ai or "declare" in steps)
            else:
                shutil.copyfile(current, tmp_path)

    atomic_write_via(output_path, write, preserve_extension=True)
    if "declare" in steps and not any(s.startswith("strip") or s == "randomize" for s in steps) and has_gps(output_path, tools):
        notes.append(GPS_KEPT)
    return MetadataOutcome(input_path, output_path, "written", notes=notes, extra=extra)


def _run_step(step: str, src: Path, dst: Path, *, tools: ToolPaths, opts: ChainOptions, notes: list[str],
              rng: random.Random | None) -> None:
    if step in ("strip", "strip-private"):
        _strip_into(src, dst, tools, keep_credit=step == "strip-private")
    elif step == "randomize":
        _, decoy = build_decoy_tags(rng)
        _strip_into(src, dst, tools)
        run([tools.exiftool, "-overwrite_original", *[f"-EXIF:{k}={v}" for k, v in decoy.items()], str(dst)])
    elif step == "credit":
        if not opts.author:
            raise ValueError("the credit step needs an author")
        args = credit_args(author=opts.author, copyright_notice=opts.copyright_notice, credit_line=opts.credit_line,
                           source=opts.source, usage_terms=opts.usage_terms)
        if opts.no_ai:
            args += [a for a in declare_args(policy="prohibited", owner=opts.author)
                     if not (opts.usage_terms and a.startswith("-XMP-xmpRights:UsageTerms="))]
        shutil.copyfile(src, dst)
        _exiftool_write(tools, dst, args)
    elif step == "declare":
        args = declare_args(policy=opts.declare_policy, owner=opts.owner or opts.author, terms_url=opts.terms_url)
        if opts.usage_terms:          # your own usage terms win over the generic no-AI sentence
            args = [a for a in args if not a.startswith("-XMP-xmpRights:UsageTerms=")]
        shutil.copyfile(src, dst)
        _exiftool_write(tools, dst, args)
    elif step == "mark":
        from mysuite.mark import core as mark_core

        if not opts.mark_key:
            raise ValueError("the mark step needs a key (--key or MYSUITE_MARK_KEY)")
        try:
            result = mark_core.embed_file(src, key=opts.mark_key, ident=opts.mark_id, out=dst, overwrite=True,
                                          fmt=mark_core.target_format(src, None), exiftool=tools.exiftool)
        except mark_core.MarkError as exc:
            raise ValueError(f"mark: {exc}") from exc
        notes.extend(result.notes or [])
    else:
        raise ValueError(f"unknown step {step!r}")
