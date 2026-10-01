from __future__ import annotations

from pathlib import Path

import tomlkit


def save_preset(config_path: Path, name: str, settings: dict) -> None:
    """Writes/overwrites a [presets.NAME] block in the TOML file at config_path,
    creating the file (and any missing [presets] table) if needed, and preserving
    all existing content — comments, formatting, other presets and sections."""
    if config_path.exists():
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()

    if "presets" not in doc:
        doc["presets"] = tomlkit.table(is_super_table=True)

    preset_table = tomlkit.table()
    for key, value in settings.items():
        if value is not None:
            preset_table[key] = value

    doc["presets"][name] = preset_table

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
