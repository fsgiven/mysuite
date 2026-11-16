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
