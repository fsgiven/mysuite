"""WS-E inspect fixes: a malformed SVG is a warning (not "ok"), readable labels, PDF colour spaces from the
(decompressed) content, and statuses that are in the schema."""
from __future__ import annotations

import json
import zlib

from PIL import Image

from mysuite import schema
from mysuite.cli import app
from tests.helpers import runner


def inspect(*args):
    res = runner.invoke(app, ["inspect", *[str(a) for a in args], "--json"])
    return res, json.loads(res.stdout)


def test_malformed_svg_is_a_warning_not_ok(tmp_path):
    bad = tmp_path / "bad.svg"
    bad.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect fill="#d00" width="5"></svg>')
    res, doc = inspect(bad)
    item = doc["items"][0]
    assert res.exit_code == 0
    assert item["status"] == "warning" and item["well_formed"] is False
    assert "not well-formed" in item["warnings"][0]
    assert any("bad.svg" in w for w in doc["warnings"])


def test_good_svg_stays_ok(tmp_path):
    good = tmp_path / "good.svg"
    good.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect fill="#d00" width="5"/></svg>')
    res, doc = inspect(good)
    assert doc["items"][0]["status"] == "ok" and doc["items"][0]["well_formed"] is True


def test_inspect_statuses_are_in_the_schema():
    assert {"ok", "warning", "failed"} <= set(schema.STATUSES)


def test_human_output_has_readable_labels(tmp_path):
    Image.new("RGBA", (30, 20), (200, 0, 0, 128)).save(tmp_path / "a.png")
    res = runner.invoke(app, ["inspect", str(tmp_path / "a.png")], env={"COLUMNS": "120"})
    out = res.output
    assert "Colour space" in out and "Megapixels" in out and "Transparency used" in out
    assert "colorspace" not in out and "alpha_used" not in out
    assert "yes" in out                                                  # booleans as yes/no


def _pdf_with_content(path, content: bytes, resources: bytes = b"<< >>"):
    """A minimal one-page PDF whose content stream is Flate-compressed (so a byte search can't see it)."""
    stream = zlib.compress(content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Resources " + resources + b" >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_pdf_colour_spaces_from_compressed_content(tmp_path):
    pdf = _pdf_with_content(tmp_path / "c.pdf", b"0 0 0 1 k 0 0 50 50 re f 1 0 0 rg 50 50 50 50 re f")
    item = inspect(pdf)[1]["items"][0]
    assert set(item["colorspaces"]) == {"CMYK", "RGB"}


def test_pdf_spot_colours_are_named(tmp_path):
    res = b"<< /ColorSpace << /CS0 [/Separation /PANTONE#20485#20C /DeviceCMYK 5 0 R] >> >>"
    pdf = _pdf_with_content(tmp_path / "s.pdf", b"/CS0 cs 1 scn 0 0 50 50 re f", res)
    item = inspect(pdf)[1]["items"][0]
    assert "Separation" in item["colorspaces"] and item["spot_colours"] == ["PANTONE 485 C"]


def test_password_protected_pdf_is_a_clear_failure(tmp_path):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("secret")
    with open(tmp_path / "p.pdf", "wb") as fh:
        w.write(fh)
    res, doc = inspect(tmp_path / "p.pdf")
    assert res.exit_code == 1 and "password-protected" in doc["items"][0]["error"]
