"""WS-C1 item 7: options with a fixed set of values declare it (a Choice: str Enums, which every Typer since 0.12
turns into click.Choice), so `mysuite schema` and the dashboard can offer them and a typo is a usage error (exit 2)."""
from __future__ import annotations

import pytest

from mysuite import schema
from mysuite.cli import app
from tests.helpers import runner

EXPECTED = {
    ("compress",): {"--codec": {"mozjpeg", "webp", "avif", "oxipng", "pngquant", "gifsicle"},
                    "--subsample": {"4:2:0", "4:4:4"}},
    ("watermark",): {"--position": {"bottom-right", "top-left", "center", "bottom", "top"},
                     "--size-by": {"width", "height"}},
    ("enhance", "run"): {"--format": {"png", "jpg", "webp"}, "--backend": {"auto", "classical", "realesrgan"}},
    ("relight",): {"--format": {"png", "jpg", "webp"}, "--direction": {"top-left", "right"},
                   "--preset": {"side", "golden-hour"}},
}


@pytest.mark.parametrize("path", list(EXPECTED))
def test_fixed_value_options_declare_their_choices(path):
    entry = schema.build()["commands"][" ".join(path)]
    options = {flag: o for o in entry["options"] for flag in o["flags"]}
    for flag, must_contain in EXPECTED[path].items():
        choices = options[flag].get("choices")
        assert choices, f"{' '.join(path)} {flag} declares no choices"
        assert must_contain <= set(choices), (flag, choices)


@pytest.mark.parametrize("argv", [
    ["compress", "x.png", "--codec", "jpegxl"],
    ["watermark", "x.png", "--logo", "x.png", "--size-by", "depth"],
    ["enhance", "run", "x.png", "--backend", "magic"],
    ["relight", "x.png", "--format", "gif"],
])
def test_a_value_outside_the_choices_is_a_usage_error(tmp_path, argv):
    from PIL import Image

    Image.new("RGB", (4, 4)).save(tmp_path / "x.png")
    argv = [str(tmp_path / a) if a == "x.png" else a for a in argv]
    assert runner.invoke(app, argv).exit_code == 2
