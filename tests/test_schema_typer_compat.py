"""schema._param must give the same entry whether a parameter carries Click's `.type` (Typer <0.29)
or only Typer 0.29+'s `type_descriptor`. CI installs one Typer version, so both shapes are faked here."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from typing import List

import pytest

from mysuite import schema


def _info(**kw):
    base = dict(file_okay=True, dir_okay=True, min=None, max=None)
    base.update(kw)
    return NS(**base)


def _desc(annotation, *, choices=None, is_path=False, is_ranged=False, info=None):
    is_list = getattr(annotation, "__origin__", None) is list
    return NS(annotation=annotation, parameter_info=info or _info(), choices=choices, is_list=is_list,
              is_path=is_path, is_ranged=is_ranged,
              ranged_type_name=getattr(annotation, "__name__", ""))


def _p(**kw):
    base = dict(name="x", opts=["--x"], secondary_opts=[], param_type_name="option", is_flag=False,
                multiple=False, nargs=1, default=None, required=False, help="Help.")
    base.update(kw)
    return NS(**base)


CASES = [
    # (click ParamType stand-in, typer 0.29 descriptor)
    (NS(name="str"), _desc(str)),
    (NS(name="int"), _desc(int)),
    (NS(name="float"), _desc(float)),
    (NS(name="int range"), _desc(int, is_ranged=True, info=_info(min=1))),
    (NS(name="float range"), _desc(float, is_ranged=True, info=_info(max=1.0))),
    (NS(name="path"), _desc(Path, is_path=True)),
    (NS(name="file"), _desc(Path, is_path=True, info=_info(dir_okay=False))),
    (NS(name="directory"), _desc(Path, is_path=True, info=_info(file_okay=False))),
    # Typer 0.29 reports is_path=False for list[Path]; Click still called it "file".
    (NS(name="file"), _desc(List[Path], info=_info(dir_okay=False))),
    (NS(name="str"), _desc(List[str])),
    (NS(name="choice", choices=("a", "b")), _desc(str, choices=("a", "b"))),
]


@pytest.mark.parametrize("click_type, descriptor", CASES)
def test_same_entry_from_click_type_and_type_descriptor(click_type, descriptor):
    old = schema._param(_p(type=click_type))
    new = schema._param(_p(type_descriptor=descriptor))
    assert old == new
    assert new["type"] == click_type.name


def test_flag_is_boolean_on_both():
    old = schema._param(_p(type=NS(name="boolean"), is_flag=True, default=False))
    new = schema._param(_p(type_descriptor=_desc(bool), is_flag=True, default=False))
    assert old == new and new["type"] == "boolean"


def test_choices_from_an_enum_and_a_click_type_on_both():
    """Other workstreams declare choices as Enums or click.Choice; both must come out as `choices`."""
    import enum

    class Level(str, enum.Enum):
        low = "low"
        high = "high"

    old = schema._param(_p(type=NS(name="choice", choices=["low", "high"])))
    from_enum = schema._param(_p(type_descriptor=_desc(Level)))
    via_click_type = schema._param(_p(type_descriptor=_desc(str, info=_info(click_type=NS(name="choice", choices=("low", "high"))))))
    list_of_enum = schema._param(_p(type_descriptor=_desc(List[Level])))
    assert old["choices"] == from_enum["choices"] == via_click_type["choices"] == list_of_enum["choices"] == ["low", "high"]
    assert old["type"] == from_enum["type"] == "choice"


def test_enum_members_and_defaults_become_plain_values():
    import enum

    class Level(str, enum.Enum):
        low = "low"

    entry = schema._param(_p(type=NS(name="choice", choices=[Level.low]), default=Level.low))
    assert entry["choices"] == ["low"] and entry["default"] == "low"


def test_path_defaults_are_shown_as_text():
    entry = schema._param(_p(type=NS(name="path"), default=Path("qr.png")))
    assert entry["default"] == "qr.png"
