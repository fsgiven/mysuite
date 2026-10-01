"""Rewrites the colour operators of a (Ghostscript-written, classic-xref) PDF so the exact
CMYK numbers we chose are the numbers stored, and declares the ICC profile as output intent.

Handles what logos are made of: flat fills/strokes (``r g b rg`` / ``RG``, ``v g`` / ``G``).
Anything else that is still RGB afterwards (gradients, images, transparency groups) is left
for the caller's Ghostscript CMYK pass and reported by ``count_rgb_operators``.

Structure handled: ``N G obj … endobj`` objects, optional FlateDecode content streams, direct or
indirect ``/Length``. Object streams / xref streams are not (Ghostscript's pdfwrite at
CompatibilityLevel 1.4 doesn't produce them); ``PdfStructureError`` is raised if met.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Callable

Mapper = Callable[[float, float, float], tuple[float, float, float, float]]

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_RGB_OP = re.compile(rf"(?<![\w.\-+])({_NUM})\s+({_NUM})\s+({_NUM})\s+(rg|RG)(?![\w])")
_GRAY_OP = re.compile(rf"(?<![\w.\-+])({_NUM})\s+(g|G)(?![\w])")
_OBJ_RE = re.compile(rb"(?m)^(\d+) (\d+) obj\b")


class PdfStructureError(ValueError):
    pass


def _fmt(values: tuple[float, ...]) -> str:
    return " ".join(f"{v:.4f}".rstrip("0").rstrip(".") or "0" for v in values)


@dataclass
class _Obj:
    num: int
    gen: int
    body: bytes  # everything between "N G obj" and "endobj" (exclusive)


class PdfDoc:
    def __init__(self, data: bytes):
        if b"/Type /ObjStm" in data or b"/Type/ObjStm" in data or b"/Type /XRef" in data or b"/Type/XRef" in data:
            raise PdfStructureError("PDF uses object/xref streams; expected a classic Ghostscript 1.4 file")
        starts = [(m.start(), int(m.group(1)), int(m.group(2)), m.end()) for m in _OBJ_RE.finditer(data)]
        if not starts:
            raise PdfStructureError("no PDF objects found")
        self.header = data[: starts[0][0]]
        self.objs: list[_Obj] = []
        for i, (start, num, gen, body_start) in enumerate(starts):
            end = data.find(b"endobj", body_start)
            if end < 0:
                raise PdfStructureError(f"object {num} has no endobj")
            self.objs.append(_Obj(num, gen, data[body_start:end]))
        trailer = re.search(rb"trailer\s*<<(.*?)>>\s*startxref", data, re.S)
        if not trailer:
            raise PdfStructureError("no classic trailer found")
        self.trailer = trailer.group(1)

    def get(self, num: int) -> _Obj | None:
        return next((o for o in self.objs if o.num == num), None)

    def next_num(self) -> int:
        return max(o.num for o in self.objs) + 1

    def serialize(self) -> bytes:
        out = bytearray(self.header)
        offsets: dict[int, int] = {}
        for obj in self.objs:
            offsets[obj.num] = len(out)
            out += f"{obj.num} {obj.gen} obj".encode() + obj.body + b"endobj\n"
        size = max(offsets) + 1
        xref_at = len(out)
        out += f"xref\n0 {size}\n".encode()
        out += b"0000000000 65535 f \n"
        for n in range(1, size):
            if n in offsets:
                out += f"{offsets[n]:010d} 00000 n \n".encode()
            else:
                out += b"0000000000 65535 f \n"
        trailer = re.sub(rb"/Size\s+\d+", b"/Size %d" % size, self.trailer)
        out += b"trailer\n<<" + trailer + b">>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
        return bytes(out)


_STREAM_RE = re.compile(rb"^(.*?)stream\r?\n(.*?)\r?\n?endstream", re.S)


def _is_content_stream(dict_part: bytes) -> bool:
    if re.search(rb"/Subtype\s*/(Image|Type1C|CIDFontType0C|OpenType|XML)\b", dict_part):
        return False
    if re.search(rb"/(Length1|Length2|Length3|N\s+\d|Type\s*/Metadata|Type\s*/EmbeddedFile)\b", dict_part):
        return False
    return True


def rewrite_flat_colours(data: bytes, mapper: Mapper) -> bytes:
    """Replaces rg/RG/g/G operators in content streams by k/K via `mapper` (0-1 floats)."""
    doc = PdfDoc(data)
    for obj in doc.objs:
        m = _STREAM_RE.match(obj.body.lstrip(b"\r\n"))
        if not m:
            continue
        dict_part, raw = m.group(1), m.group(2)
        if not _is_content_stream(dict_part):
            continue
        flate = b"/FlateDecode" in dict_part
        try:
            content = zlib.decompress(raw) if flate else raw
        except zlib.error:
            continue
        text = content.decode("latin-1")
        new = _RGB_OP.sub(
            lambda mm: _fmt(mapper(float(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
            + (" k" if mm.group(4) == "rg" else " K"),
            text,
        )
        new = _GRAY_OP.sub(
            lambda mm: _fmt(mapper(float(mm.group(1)), float(mm.group(1)), float(mm.group(1))))
            + (" k" if mm.group(2) == "g" else " K"),
            new,
        )
        if new == text:
            continue
        payload = zlib.compress(new.encode("latin-1"), 9) if flate else new.encode("latin-1")
        _set_stream(doc, obj, dict_part, payload)
    return doc.serialize()


def _set_stream(doc: PdfDoc, obj: _Obj, dict_part: bytes, payload: bytes) -> None:
    length_ref = re.search(rb"/Length\s+(\d+)\s+0\s+R", dict_part)
    if length_ref:
        target = doc.get(int(length_ref.group(1)))
        if target is None:
            raise PdfStructureError("indirect /Length object missing")
        target.body = b"\n%d\n" % len(payload)
        new_dict = dict_part
    else:
        new_dict = re.sub(rb"/Length\s+\d+", b"/Length %d" % len(payload), dict_part, count=1)
    obj.body = b"\n" + new_dict.lstrip(b"\r\n") + b"stream\n" + payload + b"\nendstream\n"


def count_rgb_operators(data: bytes) -> int:
    """How many RGB colour operators / DeviceRGB spaces remain (gradients, images, groups)."""
    total = len(re.findall(rb"/DeviceRGB", data))
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\n?endstream", data, re.S):
        try:
            text = zlib.decompress(m.group(1)).decode("latin-1")
        except zlib.error:
            continue
        total += len(_RGB_OP.findall(text)) + len(_GRAY_OP.findall(text))
    return total


def add_output_intent(data: bytes, icc: bytes, description: str, identifier: str = "Custom") -> bytes:
    """Adds an /OutputIntents entry (GTS_PDFX) with the ICC profile so the CMYK numbers have a meaning."""
    doc = PdfDoc(data)
    catalog = next((o for o in doc.objs if re.search(rb"/Type\s*/Catalog\b", o.body)), None)
    if catalog is None:
        raise PdfStructureError("no catalog object")
    if b"/OutputIntents" in catalog.body:
        return data
    n_icc, n_intent = doc.next_num(), doc.next_num() + 1
    payload = zlib.compress(icc, 9)
    doc.objs.append(_Obj(n_icc, 0, b"\n<</N 4 /Alternate /DeviceCMYK /Filter /FlateDecode /Length %d>>\nstream\n" % len(payload) + payload + b"\nendstream\n"))
    safe = description.replace("\\", "").replace("(", "").replace(")", "")
    intent = (
        f"\n<</Type /OutputIntent /S /GTS_PDFX /OutputConditionIdentifier ({identifier}) "
        f"/Info ({safe}) /DestOutputProfile {n_icc} 0 R>>\n"
    ).encode("latin-1", "replace")
    doc.objs.append(_Obj(n_intent, 0, intent))
    catalog.body = re.sub(rb"(/Type\s*/Catalog)", rb"\1 /OutputIntents [%d 0 R]" % n_intent, catalog.body, count=1)
    return doc.serialize()
