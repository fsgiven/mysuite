from __future__ import annotations

from pathlib import Path

from mysuite.tui.dragdrop import merge_paths_into_input, parse_input_files_field


def test_parse_input_files_field_single_path_with_comma_in_name_not_split(tmp_path):
    # macOS's own default export filenames often contain a literal comma,
    # e.g. "ChatGPT Image 26 ago 2026, 12_32_12.png" — a naive split(",")
    # incorrectly breaks this into two bogus path fragments.
    weird = tmp_path / "ChatGPT Image 26 ago 2026, 12_32_12.png"
    weird.write_bytes(b"\x89PNG")

    result = parse_input_files_field(str(weird))

    assert result == [weird]


def test_parse_input_files_field_splits_genuine_comma_separated_list(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"\x89PNG")
    b.write_bytes(b"\x89PNG")

    result = parse_input_files_field(f"{a}, {b}")

    assert result == [a, b]


def test_parse_input_files_field_empty_string_returns_empty():
    assert parse_input_files_field("") == []
    assert parse_input_files_field("   ") == []


def test_parse_input_files_field_nonexistent_single_path_falls_back_unsplit():
    # No commas at all — a single nonexistent path is still returned as one
    # entry, not silently dropped (existence is validated later by callers).
    result = parse_input_files_field("/does/not/exist.png")
    assert result == [Path("/does/not/exist.png")]


def test_merge_paths_into_input_preserves_existing_comma_containing_entry(tmp_path):
    weird = tmp_path / "Photo, edited.png"
    weird.write_bytes(b"\x89PNG")
    new_file = tmp_path / "second.png"

    merged = merge_paths_into_input(str(weird), [new_file])

    # the comma-containing entry must survive intact, not get corrupted
    assert str(weird) in merged
    assert str(new_file) in merged


def test_merge_paths_into_input_dedupes():
    existing = "/a/one.png,/a/two.png"
    merged = merge_paths_into_input(existing, [Path("/a/one.png")])
    assert merged == existing


def test_merge_paths_into_input_empty_existing_value():
    merged = merge_paths_into_input("", [Path("/a/one.png")])
    assert merged == "/a/one.png"


def test_merge_paths_into_input_appends_to_existing():
    merged = merge_paths_into_input("/a/one.png", [Path("/a/two.png")])
    assert merged == "/a/one.png,/a/two.png"
