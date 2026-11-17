"""One description of "hidden data", shared by `metadata strip`, `metadata apply` and `pdf strip`, so the help, the
dashboard and the code say the same thing about what goes and what stays."""
from __future__ import annotations

from pathlib import Path

# What "strip" removes from pictures (all of it, unless --keep-credit keeps the credit fields below).
REMOVED = "GPS location, camera make/model and serial numbers, lens, software, dates, comments, author and copyright"
# What it keeps.
KEPT = "the colour profile (ICC, so colours look the same; --drop-icc removes it) and the rotation flag"
# The credit fields `--keep-credit` keeps (and `metadata credit` writes): EXIF, IPTC IIM and XMP (IPTC Core / Dublin Core).
CREDIT_TAGS = (
    "EXIF:Artist", "EXIF:Copyright",
    "IPTC:By-line", "IPTC:By-lineTitle", "IPTC:Credit", "IPTC:Source", "IPTC:CopyrightNotice",
    "XMP-dc:Creator", "XMP-dc:Rights",
    "XMP-photoshop:Credit", "XMP-photoshop:Source", "XMP-photoshop:AuthorsPosition",
    "XMP-xmpRights:all", "XMP-plus:all", "XMP-iptcCore:CreatorContactInfo",
)
PDF_REMOVED = "title, author, subject, keywords, producer, dates, the XMP packet and comment authors"

# Suffixes mysuite gives its own outputs. A folder run skips these files, so running a command twice on a folder does
# not process last run's results (photo_marked_stripped.jpg). A file named explicitly is always processed.
OWN_SUFFIXES = ("_stripped", "_credited", "_declared", "_marked", "_protected", "_randomized", "_shielded")


def is_own_output(path: Path) -> bool:
    return Path(path).stem.endswith(OWN_SUFFIXES)
