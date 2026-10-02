"""The MCP server, exercised through a real MCP client over stdio."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client, StdioServerParameters  # noqa: E402

from tests.helpers import SIMPLE_SVG  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
need = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsvg-convert", "gs", "magick", "exiftool", "oxipng")), reason="needs tools")


@asynccontextmanager
async def server(*allow: Path):
    args = ["-m", "mysuite", "mcp"]
    for a in allow:
        args += ["--allow", str(a)]
    params = StdioServerParameters(command=sys.executable, args=args, env={**os.environ, "PYTHONPATH": str(ROOT)})
    async with Client(params) as client:
        yield client


async def call(client, tool, /, **arguments):
    result = await client.call_tool(tool, arguments)
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


@pytest.fixture
def work(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "logo.svg").write_text(SIMPLE_SVG)
    subprocess.run(["magick", "-size", "40x30", "xc:#dd0000", f"PNG24:{root / 'pic.png'}"], check=True)
    return root


@pytest.mark.asyncio
async def test_lists_every_tool_with_descriptions(work):
    async with server(work) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"mysuite_inspect", "mysuite_export", "mysuite_convert", "mysuite_cutout", "mysuite_watermark",
            "mysuite_compress", "mysuite_enhance", "mysuite_transform", "mysuite_relight", "mysuite_metadata", "mysuite_kit", "mysuite_pdf", "mysuite_ocr", "mysuite_qr", "mysuite_dupes", "mysuite_diff", "mysuite_contrast", "mysuite_print", "mysuite_rename", "mysuite_sheet", "mysuite_profile", "mysuite_tokens", "mysuite_variants", "mysuite_profiles", "mysuite_shield", "mysuite_components", "mysuite_pipeline_run", "mysuite_doctor",
            "mysuite_schema"} <= set(tools)
    assert all(t.description for t in tools.values())
    assert "dry_run" in tools["mysuite_export"].input_schema["properties"]


@need
@pytest.mark.asyncio
async def test_inspect_and_thumbnail(work):
    async with server(work) as c:
        doc = await call(c, "mysuite_inspect", paths=[str(work / "pic.png")], thumb=str(work / "t.png"))
    assert doc["ok"] and doc["items"][0]["width"] == 40 and (work / "t.png").exists()


@need
@pytest.mark.asyncio
async def test_export_dry_run_then_real_then_no_overwrite(work):
    async with server(work) as c:
        plan = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["png"], sizes=["32"], out=str(work / "o"), dry_run=True)
        assert plan["ok"] and plan["items"][0]["status"] == "planned" and not (work / "o").exists()
        done = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["png"], sizes=["32"], out=str(work / "o"))
        assert done["items"][0]["status"] == "written"
        again = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["png"], sizes=["32"], out=str(work / "o"))
        assert again["items"][0]["status"] == "skipped_existing"
        forced = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["png"], sizes=["32"], out=str(work / "o"), overwrite=True)
        assert forced["items"][0]["status"] == "written"


@need
@pytest.mark.asyncio
async def test_cmyk_export_reports_inks(work):
    async with server(work) as c:
        doc = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["pdf"], profiles=["cmyk"],
                         sizes=["100"], out=str(work / "o"), cmyk_mode="clean")
    assert doc["ok"] and doc["cmyk"]["mode"] == "clean"


@need
@pytest.mark.asyncio
async def test_sandbox_refuses_outside_paths_and_writes_nothing(work, tmp_path):
    outside = tmp_path / "outside"
    async with server(work) as c:
        out_write = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], formats=["png"], sizes=["32"], out=str(outside))
        other = tmp_path / "other.png"
        subprocess.run(["magick", "-size", "5x5", "xc:red", f"PNG24:{other}"], check=True)
        out_read = await call(c, "mysuite_convert", inputs=[str(other)], to=["jpeg"])
        traversal = await call(c, "mysuite_convert", inputs=[str(work / ".." / "other.png")], to=["jpeg"])
    for doc in (out_write, out_read, traversal):
        assert doc["exit_code"] == 3 and not doc["ok"], doc
    assert not outside.exists() and not (tmp_path / "other.jpg").exists()


@pytest.mark.asyncio
async def test_unknown_option_is_a_structured_error_not_an_exception(work):
    async with server(work) as c:
        doc = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], options={"formatz": ["png"]})
    assert doc["ok"] is False and "formatz" in doc["errors"][0]


@need
@pytest.mark.asyncio
async def test_convert_and_metadata_and_validation(work):
    async with server(work) as c:
        conv = await call(c, "mysuite_convert", inputs=[str(work / "pic.png")], to=["jpeg"])
        assert conv["items"][0]["output"].endswith("pic.jpg")
        strip = await call(c, "mysuite_metadata", inputs=[str(work / "pic.png")], action="strip")
        assert strip["ok"]
        bad = await call(c, "mysuite_metadata", inputs=[str(work / "pic.png")], action="explode")
        assert bad["exit_code"] == 2
        noauthor = await call(c, "mysuite_metadata", inputs=[str(work / "pic.png")], action="credit")
        assert "author" in noauthor["errors"][0]


@need
@pytest.mark.asyncio
async def test_pipeline_through_mcp(work):
    pipeline = {"inputs": ["logo.svg"], "step": [
        {"tool": "export", "formats": ["png"], "sizes": [32], "out": "dist"},
        {"tool": "metadata", "action": "strip", "only": ["png"]},
        {"tool": "compress", "codec": "oxipng", "only": ["png"]},
    ]}
    async with server(work) as c:
        plan = await call(c, "mysuite_pipeline_run", pipeline=pipeline, dry_run=True)
        assert plan["ok"] and not (work / "dist").exists()
        doc = await call(c, "mysuite_pipeline_run", pipeline=pipeline)
        assert doc["ok"], doc
        assert [i["status"] for i in doc["items"]] == ["ok", "ok", "ok"]
        assert any(o.endswith("logo_32_stripped_compressed.png") for o in doc["items"][2]["outputs"])
        broken = await call(c, "mysuite_pipeline_run", pipeline={"inputs": ["logo.svg"], "step": [{"tool": "nope"}]})
        assert broken["ok"] is False and "unknown tool" in broken["errors"][0]


@need
@pytest.mark.asyncio
async def test_pipeline_cannot_escape_the_sandbox(work, tmp_path):
    outside = tmp_path / "outside"
    pipeline = {"inputs": ["logo.svg"], "step": [{"tool": "export", "formats": ["png"], "sizes": [32], "out": str(outside)}]}
    async with server(work) as c:
        doc = await call(c, "mysuite_pipeline_run", pipeline=pipeline)
    assert doc["ok"] is False and not outside.exists()


@pytest.mark.asyncio
async def test_doctor_schema_and_resources(work):
    async with server(work) as c:
        doctor = await call(c, "mysuite_doctor")
        assert any(i["tool"] == "magick" for i in doctor["items"])
        sch = await call(c, "mysuite_schema", command="export")
        assert "export" in sch["commands"] and sch["schema_version"] == 1
        resources = {str(r.uri) for r in (await c.list_resources()).resources}
        assert {"mysuite://guide", "mysuite://schema", "mysuite://allowed-folders"} <= resources
        allowed = await c.read_resource("mysuite://allowed-folders")
        assert str(work.resolve()) in allowed.contents[0].text
        guide = await c.read_resource("mysuite://guide")
        assert "mysuite" in guide.contents[0].text


@pytest.mark.asyncio
async def test_default_root_is_the_start_folder_not_the_whole_disk(work, tmp_path, monkeypatch):
    other = tmp_path / "x.png"
    subprocess.run(["magick", "-size", "5x5", "xc:red", f"PNG24:{other}"], check=True)
    params = StdioServerParameters(command=sys.executable, args=["-m", "mysuite", "mcp"], cwd=str(work),
                                   env={**os.environ, "PYTHONPATH": str(ROOT)})
    async with Client(params) as c:
        doc = await call(c, "mysuite_convert", inputs=[str(other)], to=["jpeg"])
    assert doc["exit_code"] == 3


@pytest.mark.asyncio
async def test_transform_through_mcp(work):
    from PIL import Image

    async with server(work) as c:
        doc = await call(c, "mysuite_transform", inputs=[str(work / "pic.png")], resize="20", pad="1:1", round_corners="50%")
        assert doc["ok"], doc
        with Image.open(doc["items"][0]["output"]) as im:
            assert im.size == (20, 20) and im.getpixel((0, 0))[3] == 0
        bad = await call(c, "mysuite_transform", inputs=[str(work / "pic.png")], resize="abc", overwrite=True)
        assert bad["ok"] is False


@pytest.mark.asyncio
async def test_new_tools_through_mcp(work):
    from PIL import Image
    from pypdf import PdfReader

    async with server(work) as c:
        kit = await call(c, "mysuite_kit", inputs=[str(work / "logo.svg")], kit="favicon", out=str(work / "k"))
        assert kit["ok"] and any(i["output"].endswith("favicon.ico") for i in kit["items"])
        qr = await call(c, "mysuite_qr", action="make", text="https://example.com/a?b=c", out=str(work / "q.png"))
        assert qr["ok"] and (work / "q.png").exists()
        ratio = await call(c, "mysuite_contrast", foreground="#000", background="#fff")
        assert ratio["items"][0]["ratio"] == 21.0
        pr = await call(c, "mysuite_print", inputs=[str(work / "pic.png")], size="10x15cm")
        assert Image.open(pr["items"][0]["output"]).size == (1181, 1772)
        ren = await call(c, "mysuite_rename", inputs=[str(work / "pic.png")], pattern="renamed_{n:2}{ext}", dry_run=True)
        assert ren["items"][0]["output"].endswith("renamed_01.png") and not (work / "renamed_01.png").exists()
        sheet = await call(c, "mysuite_sheet", inputs=[str(work / "pic.png")], out=str(work / "s.png"))
        assert sheet["ok"] and (work / "s.png").exists()
        diff = await call(c, "mysuite_diff", first=str(work / "pic.png"), second=str(work / "pic.png"))
        assert diff["items"][0]["identical"]
        dup = await call(c, "mysuite_dupes", inputs=[str(work)])
        assert dup["ok"]
        prof = await call(c, "mysuite_profile", inputs=[str(work / "pic.png")], to="cmyk")
        assert prof["ok"]
        Image.new("RGB", (100, 100), "white").save(work / "d.pdf")
        pdf = await call(c, "mysuite_pdf", action="rotate", inputs=[str(work / "d.pdf")], options={"degrees": 90})
        assert pdf["ok"] and len(PdfReader(pdf["items"][0]["output"]).pages) == 1
        bad = await call(c, "mysuite_pdf", action="explode", inputs=[str(work / "d.pdf")])
        assert bad["exit_code"] == 2
        wrong = await call(c, "mysuite_pdf", action="rotate", inputs=[str(work / "d.pdf")], options={"nope": 1})
        assert wrong["exit_code"] == 2 and "nope" in wrong["errors"][0]
        outside = await call(c, "mysuite_qr", action="make", text="x", out=str(work.parent / "escape.png"))
        assert outside["exit_code"] == 3 and not (work.parent / "escape.png").exists()


@pytest.mark.asyncio
async def test_tokens_and_variants_through_mcp(work):
    (work / "ds.css").write_text(':root{--ink:#dd0000}[data-theme="dark"]{--ink:#ff6b6b}')
    (work / "mono.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect fill="#dd0000" width="5" height="5"/></svg>')
    async with server(work) as c:
        listing = await call(c, "mysuite_tokens", action="list", source=str(work / "ds.css"))
        assert listing["ok"] and listing["items"][0]["dark"] == "#ff6b6b"
        chk = await call(c, "mysuite_tokens", action="check", source=str(work / "ds.css"), logo=str(work / "mono.svg"))
        assert chk["on_palette"] == 1
        var = await call(c, "mysuite_variants", inputs=[str(work / "mono.svg")], variants="negative,mono-white", tokens=str(work / "ds.css"))
        assert var["ok"] and "#ff6b6b" in (work / "mono_negative.svg").read_text()
        outside = await call(c, "mysuite_tokens", action="list", source=str(work.parent))
        assert outside["exit_code"] == 3
        bad = await call(c, "mysuite_tokens", action="show", source=str(work / "ds.css"))
        assert bad["exit_code"] == 2


@pytest.mark.asyncio
async def test_profiles_through_mcp(work):
    (work / "mysuite.toml").write_text('[profiles.acme]\ndescription = "Acme"\n[profiles.acme.export]\nformats = ["png"]\nsizes = [40]\nout_dir = "acme-out"\n')
    async with server(work) as c:
        listing = await call(c, "mysuite_profiles", action="list")
        assert listing["ok"] and listing["items"][0]["name"] == "acme"
        assert (await call(c, "mysuite_profiles", action="check", name="acme"))["ok"]
        exp = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], profile="acme")
        assert exp["ok"] and exp["items"][0]["output"].endswith("acme-out/logo/png/rgb/logo_40.png")
        ghost = await call(c, "mysuite_export", inputs=[str(work / "logo.svg")], profile="ghost")
        assert ghost["exit_code"] == 1 and "ghost" in ghost["errors"][0]
        assert (await call(c, "mysuite_profiles", action="show"))["exit_code"] == 2


@pytest.mark.asyncio
async def test_components_and_shield_through_mcp(work, tmp_path):
    import os

    from tests.helpers import runner  # noqa: F401

    async with server(work) as c:
        listing = await call(c, "mysuite_components")
        assert {i["name"] for i in listing["items"]} == {"tools", "cutout", "vision", "shield"}
        plan = await call(c, "mysuite_shield", inputs=[str(work / "pic.png")], dry_run=True)
        assert plan["ok"] and plan["items"][0]["status"] == "planned" and plan["items"][0]["estimated_seconds"] > 0
        tools = {t.name for t in (await c.list_tools()).tools}
        assert "mysuite_install" not in tools                              # an agent cannot start a multi-GB download
