from __future__ import annotations

import pytest

from mysuite.color.lab import delta_e2000, delta_e_rgb
from mysuite.color.parse import parse_color
from mysuite.color.svg import palette, recolor_text


@pytest.mark.parametrize("text", ["#d00", "#DD0000", "rgb(221,0,0)", "rgb(86.7%,0%,0%)", "hsl(0,100%,43.3%)",
                                  "hsl(0deg 100% 43.3%)", "rgb(221 0 0)"])
def test_every_notation_of_the_same_red_parses_equal(text):
    assert parse_color(text)[:3] == (221, 0, 0)


@pytest.mark.parametrize("text", ["none", "transparent", "currentColor", "url(#g)", "#xyz", "", "rgb(1,2)"])
def test_non_colours_are_not_colours(text):
    assert parse_color(text) is None


def test_alpha_is_kept_separate():
    assert parse_color("#dd0000").alpha is None
    assert parse_color("#dd0000ff").alpha == 1.0
    assert parse_color("rgba(221,0,0,.5)").alpha == 0.5
    assert parse_color("rgb(221 0 0 / 50%)").alpha == 0.5


def test_ciede2000_matches_the_published_reference_pair():
    # Sharma, Wu, Dalal (2005) test data, pair 1
    assert delta_e2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(2.0425, abs=1e-3)
    assert delta_e_rgb((255, 255, 255), (255, 255, 255)) == 0
    assert delta_e_rgb((255, 255, 255), (0, 0, 0)) == pytest.approx(100, abs=0.01)


def test_recolor_catches_all_notations_everywhere_but_nothing_else():
    svg = (
        '<svg><style>.a{fill:red}.b{stroke:rgb(221,0,0)}</style>'
        '<rect fill="#d00"/><rect style="fill:hsl(0,100%,43.3%);stroke:#00f"/>'
        '<linearGradient id="dd0000"><stop stop-color="#DD0000"/></linearGradient>'
        '<rect fill="url(#dd0000)" id="x"/><title>fill: #dd0000</title></svg>'
    )
    out = recolor_text(svg, {"#dd0000": "#0000ff"}, tolerance=0)
    assert out.count("#0000ff") == 4          # #d00, hsl(), rgb() in .b, stop-color
    assert "fill:red" in out                  # pure red is a different colour at tolerance 0
    assert "stroke:#00f" in out               # untouched blue
    assert "url(#dd0000)" in out and 'id="dd0000"' in out  # references are not colours
    assert "<title>fill: #dd0000</title>" in out            # text content is not a colour context


def test_tolerance_catches_near_colours_but_not_far_ones():
    svg = '<rect fill="#dc0100"/><rect fill="#ee0000"/><rect fill="#ff0000"/>'
    out = recolor_text(svg, {"#dd0000": "#0000ff"}, tolerance=2.0)
    assert out.count("#0000ff") == 1 and "#ff0000" in out
    assert recolor_text(svg, {"#dd0000": "#0000ff"}, tolerance=0).count("#0000ff") == 0


def test_alpha_colours_only_match_alpha_froms():
    svg = '<rect fill="#dd0000ff"/><rect fill="rgba(221,0,0,.5)"/><rect fill="#dd0000"/>'
    out = recolor_text(svg, {"#dd0000": "#0000ff"}, tolerance=0)
    assert out.count("#0000ff") == 1 and "#dd0000ff" in out and "rgba(221,0,0,.5)" in out
    out2 = recolor_text(svg, {"#dd0000ff": "#00ff00"}, tolerance=0)
    assert out2.count("#00ff00") == 1


def test_named_from_matches_hex_spellings_too():
    out = recolor_text('<rect fill="#fff"/><rect fill="WHITE"/><rect fill="rgb(255,255,255)"/>', {"white": "black"}, 0)
    assert out.count("black") == 3


def test_palette_counts_distinct_colours():
    svg = '<rect fill="#d00"/><rect fill="rgb(221,0,0)"/><rect fill="blue" stroke="#00f"/>'
    assert dict(palette(svg)) == {"#dd0000": 2, "#0000ff": 2}


def test_recolor_leaves_the_rest_of_the_document_untouched():
    svg = '<svg  viewBox="0 0 1 1">\n  <rect   fill="#dd0000"/>\n</svg>\n'
    assert recolor_text(svg, {"#dd0000": "#fff"}, 0) == svg.replace("#dd0000", "#fff")
