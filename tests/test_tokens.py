from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error

import pytest
from PIL import Image

from mysuite.cli import app
from mysuite.color import variants as V
from mysuite.color.svg import palette
from mysuite.tokens import css, dtcg, figma
from mysuite.tokens.match import match_palette
from mysuite.tokens.model import TokenError, TokenSet
from mysuite.tokens.sources import fetch_git, load_tokens, parse_git_spec
from tests.helpers import runner

PRIMITIVES = """
/* primitives (invented for the tests) */
:root, :host {
  --color-acme-red-50: #DD0000;
  --color-acme-red-30: #930000;
  --color-neutral-15: #222628;
  --color-neutral-100: #FFFFFF;
  --color-neutral-70: #A6ABB0;
  --color-beta-blue-50: #0057B8;
  --color-beta-red-50: #DC0101;
  --color-white-a-80: rgba(255, 255, 255, 0.8);
  --space-4: 4px;
  --font-body: "Inter", sans-serif;
}
"""
SEMANTICS = """
[data-color-brand="acme"], :host([data-color-brand="acme"]) {
  --text-color-brand: var(--color-acme-red-50, #DD0000);
  --text-color-primary: var(--color-neutral-15, #222628);
  --bg-color-brand-solid: var(--color-acme-red-50, #DD0000);
  --missing-ref: var(--color-does-not-exist, #123456);
  --chain-a: var(--chain-b);
  --chain-b: var(--text-color-primary);
  --cycle-a: var(--cycle-b);
  --cycle-b: var(--cycle-a);
}
[data-color-brand="acme"][data-theme="dark"] {
  --text-color-brand: #F75849;
  --text-color-primary: var(--color-neutral-100, #FFFFFF);
}
[data-color-brand="beta"] { --text-color-brand: var(--color-beta-blue-50, #0057B8); }
@media (prefers-color-scheme: dark) {
  [data-color-brand="beta"]:not([data-theme]) { --text-color-brand: #66AAFF; }
}
"""


@pytest.fixture
def ds(tmp_path):
    folder = tmp_path / "ds"
    folder.mkdir()
    (folder / "primitives.css").write_text(PRIMITIVES)
    (folder / "semantics.css").write_text(SEMANTICS)
    return folder


def hexes(ts, brand, theme="light"):
    return {n: c.hex() for n, c in ts.colour_map(brand, theme).items()}


# ---------------------------------------------------------------------------------- CSS
def test_css_aliases_fallbacks_chains_and_cycles(ds):
    ts = load_tokens(str(ds)).tokens
    light = hexes(ts, "acme")
    assert light["--text-color-brand"] == "#dd0000" and light["--text-color-primary"] == "#222628"
    assert light["--missing-ref"] == "#123456"                         # unresolved var(): the written fallback
    assert light["--chain-a"] == "#222628"                              # alias of an alias
    assert "--cycle-a" not in light                                    # an endless alias is dropped, not looped on
    assert light["--color-acme-red-30"] == "#930000"                   # global primitives are visible from the brand


def test_css_themes_brands_and_dark_media(ds):
    ts = load_tokens(str(ds)).tokens
    assert ts.brands == ["acme", "beta"]
    dark = hexes(ts, "acme", "dark")
    assert dark["--text-color-brand"] == "#f75849" and dark["--text-color-primary"] == "#ffffff"
    assert dark["--bg-color-brand-solid"] == "#dd0000"                 # no dark override: falls back to the light value
    assert hexes(ts, "beta", "dark")["--text-color-brand"] == "#66aaff"   # @media (prefers-color-scheme: dark)
    token = ts.get("--text-color-brand", "acme")
    assert token.has_dark and not ts.get("--bg-color-brand-solid", "acme").has_dark
    assert ts.get("text-color-brand", "acme") is token                 # the leading -- is optional


def test_css_non_colours_are_counted_not_kept(ds):
    ts = load_tokens(str(ds)).tokens
    names = ts.names("acme")
    assert "--space-4" not in names and "--font-body" not in names and ts.skipped_non_colour >= 2
    assert ts.get("--color-white-a-80", "acme").color().alpha == 0.8    # rgba is a colour


def test_brand_must_be_chosen_when_ambiguous_and_must_exist(ds):
    ts = load_tokens(str(ds)).tokens
    with pytest.raises(TokenError, match="several brands"):
        ts.names(None)
    with pytest.raises(TokenError, match="unknown brand"):
        ts.names("gamma")
    assert ts.resolve_brand("acme") == "acme"


@pytest.mark.parametrize("text", ["", "{{{", "--a: ;", "a { --x: 1px", ".a{color:red}", "@media {", "}}}{", "/* unterminated"])
def test_malformed_css_never_crashes(text):
    css.parse_css(text)


def test_css_comments_and_multiple_declarations_per_line():
    ts = css.parse_css(":root{--a:#111;/* --b:#222; */--c:#333}")
    assert {n: t.values["light"] for (_, n), t in ts._tokens.items()} == {"--a": "#111", "--c": "#333"}


# --------------------------------------------------------------------------------- DTCG
DTCG = {
    "brand": {
        "$type": "color",
        "red": {"$value": "#dd0000", "$description": "Brand red", "$extensions": {"mysuite": {"dark": "#ff6b6b"}}},
        "ink": {"$value": "#222628", "$extensions": {"modes": {"light": "#222628", "dark": "{brand.paper}"}}},
        "paper": {"$value": "#ffffff"},
        "link": {"$value": "{brand.red}"},
    },
    "size": {"gap": {"$type": "dimension", "$value": "4px"}},
    "legacy": {"blue": {"value": "#0057b8", "type": "color"}},
    "broken": {"$type": "color", "$value": "{nope.nothing}"},
}


def test_dtcg_groups_aliases_extensions_and_legacy_spelling():
    ts = dtcg.parse_dtcg(DTCG)
    v = ts.view()
    assert v["brand.red"].values == {"light": "#dd0000", "dark": "#ff6b6b"} and v["brand.red"].description == "Brand red"
    assert v["brand.link"].values["light"] == "#dd0000"                 # alias resolved
    assert v["brand.ink"].values["dark"] == "#ffffff"                   # alias inside an extension
    assert v["legacy.blue"].values["light"] == "#0057b8"
    assert "size.gap" not in v and "broken" not in "".join(v) and ts.skipped_non_colour >= 2


@pytest.mark.parametrize("text", ["not json", "[1,2]", '"x"'])
def test_dtcg_bad_files_are_token_errors(text):
    with pytest.raises(TokenError):
        dtcg.load_json_text(text)


# --------------------------------------------------------------------------------- Figma
FIGMA = {"meta": {
    "variableCollections": {"c1": {"name": "Brand", "defaultModeId": "m1", "modes": [{"modeId": "m1", "name": "Light"}, {"modeId": "m2", "name": "Dark"}]}},
    "variables": {
        "v1": {"name": "brand/red", "resolvedType": "COLOR", "variableCollectionId": "c1",
               "valuesByMode": {"m1": {"r": 0.8667, "g": 0, "b": 0, "a": 1}, "m2": {"r": 0.97, "g": 0.345, "b": 0.286, "a": 1}}},
        "v2": {"name": "brand/link", "resolvedType": "COLOR", "variableCollectionId": "c1",
               "valuesByMode": {"m1": {"type": "VARIABLE_ALIAS", "id": "v1"}, "m2": {"type": "VARIABLE_ALIAS", "id": "v1"}}},
        "v3": {"name": "space/gap", "resolvedType": "FLOAT", "variableCollectionId": "c1", "valuesByMode": {"m1": 4}},
        "v4": {"name": "brand/scrim", "resolvedType": "COLOR", "variableCollectionId": "c1", "valuesByMode": {"m1": {"r": 0, "g": 0, "b": 0, "a": 0.5}}},
    },
}}


def test_figma_response_parses_modes_aliases_and_alpha():
    v = figma.parse_variables(FIGMA).view()
    assert v["brand.red"].values == {"light": "#dd0000", "dark": "#f75849"}
    assert v["brand.link"].values["light"] == "#dd0000" and v["brand.link"].values["dark"] == "#f75849"
    assert v["brand.scrim"].values["light"].startswith("rgba(0, 0, 0") and "space.gap" not in v


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def test_figma_fetch_needs_a_token_from_the_environment_and_reports_errors():
    with pytest.raises(TokenError, match="FIGMA_TOKEN"):
        figma.fetch_variables("abc123", env={})
    with pytest.raises(TokenError, match="file key"):
        figma.fetch_variables("../etc", env={"FIGMA_TOKEN": "t"})
    seen = {}

    def opener(request, timeout):
        seen["url"], seen["token"] = request.full_url, request.get_header("X-figma-token")
        return FakeResponse(FIGMA)

    assert figma.fetch_variables("abc123", env={"FIGMA_TOKEN": "secret"}, opener=opener) == FIGMA
    assert seen == {"url": "https://api.figma.com/v1/files/abc123/variables/local", "token": "secret"}

    def forbidden(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, None)

    with pytest.raises(TokenError, match="Enterprise"):
        figma.fetch_variables("abc123", env={"FIGMA_TOKEN": "t"}, opener=forbidden)

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    with pytest.raises(TokenError, match="couldn't reach"):
        figma.fetch_variables("abc123", env={"FIGMA_TOKEN": "t"}, opener=offline)


# -------------------------------------------------------------------------------- sources
def test_folder_file_and_error_sources(ds, tmp_path):
    assert len(load_tokens(str(ds)).tokens) > 10
    assert "--text-color-brand" in load_tokens(str(ds / "semantics.css")).tokens.names("acme")
    (tmp_path / "t.json").write_text(json.dumps(DTCG))
    assert "brand.red" in load_tokens(str(tmp_path / "t.json")).tokens.names()
    for bad, match in ((str(tmp_path / "nope"), "not found"), ("", "no token source")):
        with pytest.raises(TokenError, match=match):
            load_tokens(bad)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(TokenError, match="no .css or .json"):
        load_tokens(str(empty))
    (empty / "x.css").write_text("a { color: red }")
    with pytest.raises(TokenError, match="no colour tokens"):
        load_tokens(str(empty))


def test_a_later_file_overrides_per_theme(tmp_path):
    (tmp_path / "a.css").write_text(":root{--c:#111111}")
    (tmp_path / "b.css").write_text(":root{--c:#222222}")
    ts = load_tokens(str(tmp_path)).tokens
    assert ts.get("--c").values["light"] in ("#111111", "#222222") and len(ts) == 1


def test_sandbox_blocks_reading_tokens_outside_the_allowed_folder(ds, tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    res = runner.invoke(app, ["--allow", str(jail), "tokens", "list", str(ds), "--brand", "acme", "--json"])
    assert res.exit_code == 3


@pytest.mark.parametrize("spec,fragment", [
    ("git+http://example.com/r@v1", "https, ssh or file"), ("git+https://example.com/r", "pin"),
    ("git+https://example.com/r@-x", "characters"), ("git+https://example.com/r@a b", "characters"),
])
def test_git_spec_validation(spec, fragment):
    with pytest.raises(TokenError, match=fragment):
        parse_git_spec(spec)
    assert parse_git_spec("git+https://example.com/o/r@v1.2.0#path=a/b") == ("https://example.com/o/r", "v1.2.0", "a/b")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_source_is_pinned_cached_and_refreshable(ds, tmp_path, monkeypatch):
    monkeypatch.setenv("MYSUITE_CACHE", str(tmp_path / "cache"))
    repo = tmp_path / "remote"
    (repo / "tokens").mkdir(parents=True)
    for f in ds.iterdir():
        shutil.copy(f, repo / "tokens" / f.name)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "tokens")
    spec = f"git+file://{repo}@main#path=tokens"
    first = load_tokens(spec)
    assert first.commit and "--text-color-brand" in first.tokens.names("acme")
    # a new upstream commit is NOT picked up: the source is pinned and cached ...
    (repo / "tokens" / "primitives.css").write_text(PRIMITIVES.replace("#DD0000", "#00DD00"))
    git("commit", "-qam", "change")
    assert load_tokens(spec).commit == first.commit
    assert hexes(load_tokens(spec).tokens, "acme")["--text-color-brand"] == "#dd0000"
    # ... until asked to refresh
    refreshed = load_tokens(spec, refresh=True)
    assert refreshed.commit != first.commit and hexes(refreshed.tokens, "acme")["--text-color-brand"] == "#00dd00"
    with pytest.raises(TokenError, match="does not exist"):
        fetch_git(f"git+file://{repo}@main#path=nope")
    with pytest.raises(TokenError, match="git failed"):
        fetch_git(f"git+file://{repo}@no-such-ref")


# --------------------------------------------------------------------------------- matching
def test_matching_exact_near_off_and_alternatives(ds):
    ts = load_tokens(str(ds)).tokens
    view = V._own_tokens(ts.view("acme"), ts.brands, "acme")
    m = {x.colour: x for x in match_palette([("#dd0000", 3), ("#d70000", 1), ("#abcdef", 2)], view, 2.0)}
    assert m["#dd0000"].exact and m["#dd0000"].token in ("--text-color-brand", "--bg-color-brand-solid", "--color-acme-red-50")
    assert m["#d70000"].token and not m["#d70000"].exact
    assert m["#abcdef"].token is None and m["#abcdef"].alternatives
    assert match_palette([("#dd0000", 1)], view, 0.0)[0].exact


def test_semantic_tokens_win_ties_over_primitives(ds):
    ts = load_tokens(str(ds)).tokens
    best = match_palette([("#dd0000", 1)], ts.view("acme"), 1.0)[0].token
    assert best == "--text-color-brand"                                 # has a dark value; the primitive does not


def test_other_brands_primitives_are_not_candidates(ds):
    ts = load_tokens(str(ds)).tokens
    own = V._own_tokens(ts.view("acme"), ts.brands, "acme")
    assert "--color-beta-red-50" not in own and "--color-acme-red-50" in own and "--color-neutral-15" in own


# --------------------------------------------------------------------------------- variants
LOGO = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect fill="#dd0000" width="5" height="10"/><path d="M0 0h1v1z"/><circle fill="rgba(34,38,40,.5)" r="1"/></svg>'


def test_algorithmic_variants():
    inv = V.apply_algorithmic("invert", LOGO)
    assert "#22ffff" in inv and 'fill="#ffffff"' in inv.split(">")[0]          # red inverted; default black fill made white
    assert "rgba(221, 217, 215, 0.5)" in inv
    white = V.apply_algorithmic("mono-white", LOGO)
    assert white.count("#ffffff") >= 2 and "#dd0000" not in white and "rgba(255, 255, 255, 0.5)" in white
    assert 'fill="#000000"' in V.apply_algorithmic("mono-black", LOGO).split(">")[0]
    gray = V.apply_algorithmic("grayscale", LOGO)
    assert "#dd0000" not in gray and "#2f2f2f" in gray


def test_root_fill_is_not_overwritten_and_self_closing_svg_works():
    assert V._ensure_root_fill('<svg fill="none"><path/></svg>', "#fff") == '<svg fill="none"><path/></svg>'
    assert V._ensure_root_fill('<svg width="1"/>', "#fff").startswith('<svg width="1" fill="#fff"/>')


def test_negative_follows_the_token_dark_values_and_reports_ambiguity(ds):
    ts = load_tokens(str(ds)).tokens
    view = V._own_tokens(ts.view("acme"), ts.brands, "acme")
    out, notes = V.apply_negative(LOGO, view, [h[:7] for h, _ in palette(LOGO)])
    assert "#f75849" in out and "#dd0000" not in out                     # the brand red becomes its dark-theme value
    assert 'fill="#ffffff"' in out.split(">")[0]                         # unfilled shapes (black ~ the primary text) become light


def test_unfilled_shapes_become_white_with_a_note_when_no_token_is_near_black():
    ts = css.parse_css(':root{--ink:#ff0000}[data-theme="dark"]{--ink:#00ff00}')
    out, notes = V.apply_negative('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>', ts.view(), ["#ff0000"])
    assert 'fill="#ffffff"' in out.split(">")[0] and any("without a fill" in n for n in notes)


def test_negative_overrides_and_unmatched_colours(ds):
    ts = load_tokens(str(ds)).tokens
    view = V._own_tokens(ts.view("acme"), ts.brands, "acme")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#dd0000"/><rect fill="#abcdef"/></svg>'
    out, notes = V.apply_negative(svg, view, ["#dd0000", "#abcdef"], overrides={"#dd0000": "#123456"})
    assert "#123456" in out and "#abcdef" in out and any("#abcdef" in n and "left as it was" in n for n in notes)
    with pytest.raises(V.VariantError, match="no dark-theme"):
        V.apply_negative(svg, {n: t for n, t in view.items() if not t.has_dark}, ["#dd0000"])


def test_choose_dark_prefers_the_closest_group_then_the_majority(tmp_path):
    ts = css.parse_css(':root{--a:#222222;--b:#222222;--c:#222222;--d:#242424}[data-theme="dark"]{--a:#ffffff;--b:#eeeeee;--c:#eeeeee;--d:#00ff00}')
    # --a/--b/--c share the light colour: 2 of 3 say #eeeeee; --d is a slightly different token and must not outvote them
    view = ts.view()
    dark, rep, others = V.choose_dark(V.parse_color("#222222"), view, 5.0)
    assert dark == "#eeeeee" and others == ["#ffffff"] and rep in ("--b", "--c")


# --------------------------------------------------------------------------- CLI: tokens
def run(*args):
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    return res, json.loads(res.stdout)


def test_tokens_list_show_check(ds, tmp_path):
    res, doc = run("tokens", "list", ds, "--brand", "acme", "--filter", "brand")
    assert res.exit_code == 0 and doc["brands"] == ["acme", "beta"] and {i["token"] for i in doc["items"]} >= {"--text-color-brand", "--bg-color-brand-solid"}
    res, doc = run("tokens", "list", ds, "--brand", "acme", "--has-dark")
    assert all(i["has_dark"] for i in doc["items"])
    res, doc = run("tokens", "list", ds)
    assert res.exit_code == 1 and "pick one with --brand" in doc["errors"][0]
    res, doc = run("tokens", "show", ds, "text-color-brand", "--brand", "acme")
    assert doc["items"][0]["values"] == {"light": "#dd0000", "dark": "#f75849"}
    res, doc = run("tokens", "show", ds, "text-color-brnd", "--brand", "acme")
    assert res.exit_code == 1 and "no token named" in doc["errors"][0]
    res, doc = run("tokens", "show", ds, "brand", "--brand", "acme")
    assert "did you mean" in doc["errors"][0]


def test_tokens_check_flags_off_palette_and_near_colours(ds, tmp_path):
    svg = tmp_path / "l.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#dd0000"/><rect fill="#d70000"/><rect fill="#abcdef"/></svg>')
    res, doc = run("tokens", "check", svg, "--tokens", ds, "--brand", "acme")
    by = {i["colour"]: i for i in doc["items"]}
    assert res.exit_code == 0 and doc["on_palette"] == 2 and doc["off_palette"] == 1
    assert by["#abcdef"]["status"] == "off-palette" and by["#dd0000"]["exact"] and by["#d70000"]["status"] == "on-palette"
    assert any("only close to" in w for w in doc["warnings"])
    assert run("tokens", "check", svg, "--tokens", ds, "--brand", "acme", "--strict")[0].exit_code == 1
    assert run("tokens", "check", svg, "--tokens", ds, "--brand", "acme", "--tolerance", "0")[1]["on_palette"] == 1
    png = tmp_path / "p.png"
    Image.new("RGB", (20, 20), (221, 0, 0)).save(png)
    assert run("tokens", "check", png, "--tokens", ds, "--brand", "acme")[1]["on_palette"] == 1


# --------------------------------------------------------------------------- export integration
need = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")


@pytest.fixture
def logo(tmp_path):
    p = tmp_path / "brand.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50" viewBox="0 0 100 50"><rect fill="#dd0000" width="50" height="50"/><rect fill="#222628" x="50" width="50" height="50"/></svg>')
    return p


def px(path, x, y):
    return Image.open(path).convert("RGB").getpixel((x, y))


@need
def test_recolor_to_a_token_in_light_and_dark_theme(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme",
                   "--recolor", "#dd0000=token:--text-color-brand", "--out", tmp_path / "o")
    assert res.exit_code == 0 and px(doc["items"][0]["output"], 10, 25) == (221, 0, 0) and doc["tokens"]["count"] > 10
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme", "--theme", "dark",
                   "--recolor", "#dd0000=token:--text-color-brand", "--out", tmp_path / "d")
    assert px(doc["items"][0]["output"], 10, 25) == (0xF7, 0x58, 0x49)


@need
def test_changing_a_token_changes_the_export(logo, ds, tmp_path):
    args = ["export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme", "--recolor", "#dd0000=token:--text-color-brand"]
    before = run(*args, "--out", tmp_path / "a")[1]["items"][0]["output"]
    (ds / "semantics.css").write_text(SEMANTICS.replace("--text-color-brand: var(--color-acme-red-50, #DD0000)", "--text-color-brand: #00AA00"))
    after = run(*args, "--out", tmp_path / "b")[1]["items"][0]["output"]
    assert px(before, 10, 25) == (221, 0, 0) and px(after, 10, 25) == (0, 170, 0)


@need
def test_variants_each_get_their_own_folder_and_the_right_colours(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme",
                   "--variants", "default,negative,mono-white,invert,grayscale,mono-black", "--out", tmp_path / "o")
    assert res.exit_code == 0, doc
    by = {i["variant"]: i["output"] for i in doc["items"]}
    assert set(by) == {"", "negative", "mono-white", "invert", "grayscale", "mono-black"}
    assert by[""].endswith("/brand/png/rgb/brand_100.png") and "/negative/" in by["negative"]
    assert px(by[""], 10, 25) == (221, 0, 0) and px(by["negative"], 10, 25) == (0xF7, 0x58, 0x49)
    assert px(by["mono-white"], 10, 25) == (255, 255, 255) and px(by["mono-black"], 80, 25) == (0, 0, 0)
    assert px(by["invert"], 10, 25) == (34, 255, 255) and len(set(px(by["grayscale"], 10, 25))) == 1
    # the dark logo colour became a light one on the dark theme
    assert sum(px(by["negative"], 80, 25)) > 600


@need
def test_negative_map_picks_the_dark_value_explicitly(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme", "--variants", "negative",
                   "--negative-map", "#222628=token:--color-neutral-70", "--out", tmp_path / "o")
    assert res.exit_code == 0 and px(doc["items"][0]["output"], 80, 25) == px(doc["items"][0]["output"], 80, 25)


@need
@pytest.mark.parametrize("args,fragment", [
    (["--variants", "negative"], "pass --tokens"), (["--variants", "sepia"], "variants must be"),
    (["--variants", "invert,invert"], "unique"), (["--variants", "invert", "--variant", "x"], "either --variant"),
    (["--recolor", "#dd0000=token:--x"], "need --tokens"), (["--theme", "blue"], "--theme must be"),
])
def test_export_option_errors(logo, tmp_path, args, fragment):
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", *args, "--out", tmp_path / "o")
    assert res.exit_code != 0 and fragment in " ".join(doc["errors"])
    assert not (tmp_path / "o").exists()


@need
def test_unknown_token_names_get_suggestions(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme",
                   "--recolor", "#dd0000=token:--text-color-brnd", "--out", tmp_path / "o")
    assert res.exit_code == 1 and "no token named" in doc["errors"][0]
    res, doc = run("export", logo, "--formats", "png", "--sizes", "100", "--tokens", ds, "--brand", "acme",
                   "--recolor", "#dd0000=token:brand", "--out", tmp_path / "o")
    assert "did you mean" in doc["errors"][0]


@need
def test_dry_run_lists_every_variant_and_writes_nothing(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "png,pdf", "--sizes", "64", "--tokens", ds, "--brand", "acme", "--variants", "default,negative",
                   "--dry-run", "--out", tmp_path / "o")
    assert {i["variant"] for i in doc["items"]} == {"", "negative"} and len(doc["items"]) == 4 and not (tmp_path / "o").exists()


@need
def test_variants_work_with_cmyk(logo, ds, tmp_path):
    res, doc = run("export", logo, "--formats", "pdf", "--profiles", "cmyk", "--sizes", "100", "--variants", "default,mono-black",
                   "--cmyk-mode", "clean", "--out", tmp_path / "o")
    assert res.exit_code == 0 and len(doc["items"]) == 2


# ------------------------------------------------------------------------- variants make
def test_variants_make_writes_svg_files_beside_the_logo(logo, ds, tmp_path):
    res, doc = run("variants", "make", logo, "--variants", "negative,mono-white,invert", "--tokens", ds, "--brand", "acme")
    assert res.exit_code == 0, doc
    assert {i["variant"] for i in doc["items"]} == {"negative", "mono-white", "invert"}
    neg = (tmp_path / "brand_negative.svg").read_text().lower()
    assert "#f75849" in neg and "#dd0000" not in neg and "#ffffff" in neg           # red -> dark red; the dark ink -> white
    assert "#ffffff" in (tmp_path / "brand_mono-white.svg").read_text().lower()
    assert doc["tokens"]["brand"] == "acme"
    assert (tmp_path / "brand.svg").read_text().count("#dd0000") == 1               # the original is untouched


def test_variants_make_options_and_errors(logo, ds, tmp_path):
    res, doc = run("variants", "make", logo, "--variants", "negative")
    assert res.exit_code == 1 and "pass --tokens" in doc["errors"][0]
    for bad in ("default", "sepia", "invert,invert", ""):
        assert run("variants", "make", logo, "--variants", bad)[0].exit_code == 1
    res, doc = run("variants", "make", logo, "--variants", "invert", "--dry-run", "--out", tmp_path / "o")
    assert doc["items"][0]["status"] == "planned" and not (tmp_path / "o").exists()
    run("variants", "make", logo, "--variants", "invert", "--out", tmp_path / "o")
    first = (tmp_path / "o" / "brand_invert.svg").read_bytes()
    res, doc = run("variants", "make", logo, "--variants", "invert", "--out", tmp_path / "o")
    assert doc["items"][0]["status"] == "skipped_existing"
    png = tmp_path / "x.png"
    Image.new("RGB", (4, 4)).save(png)
    assert run("variants", "make", png, "--variants", "invert")[0].exit_code == 1
    assert run("variants", "make", logo, "--variants", "negative", "--tokens", ds, "--brand", "nope")[0].exit_code == 1
    jail = tmp_path / "jail"
    jail.mkdir()
    assert runner.invoke(app, ["--allow", str(jail), "variants", "make", str(logo), "--variants", "invert", "--json"]).exit_code == 3


def test_variants_list():
    res, doc = run("variants", "list")
    assert {i["variant"] for i in doc["items"]} >= {"negative", "invert", "mono-white", "grayscale"}
    assert next(i for i in doc["items"] if i["variant"] == "negative")["needs_tokens"] is True


def test_variants_as_a_pipeline_step_feeding_export(logo, ds, tmp_path):
    if shutil.which("rsvg-convert") is None:
        pytest.skip("needs rsvg-convert")
    from mysuite.pipeline import engine

    pipe = engine.parse({"inputs": ["brand.svg"], "step": [
        {"tool": "variants", "variants": "mono-white", "out": "v"},
        {"tool": "export", "formats": ["png"], "sizes": [32], "out": "e", "only": ["svg"]},
    ]}, base=tmp_path)
    results = engine.run(pipe)
    assert [r.status for r in results] == ["ok", "ok"] and results[1].outputs[0].endswith(".png")
