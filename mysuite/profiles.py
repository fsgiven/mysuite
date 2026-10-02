"""Company profiles: one named bundle of defaults - design tokens, brand, variants, export settings, metadata policy,
default presets, allowed folders - in mysuite.toml under [profiles.NAME].

    [profiles.acme]
    description = "Acme Corp brand kit"
    tokens = "git+https://git.example.com/acme/tokens@v2.1#path=dist/css"
    brand = "acme"
    theme = "light"
    variants = "default,negative"
    allow = ["~/Brand/acme"]                  # sandbox: only these folders may be read/written
    compress_preset = "web"
    [profiles.acme.export]                    # any [export] setting
    formats = ["png", "pdf"]
    sizes = [64, 512]
    profiles = ["rgb", "cmyk"]
    cmyk_mode = "clean"
    [profiles.acme.metadata]                  # what `mysuite metadata apply` does
    policy = ["strip", "credit", "declare"]   # strip | randomize | credit | declare, in this order
    no_ai = true                              # credit also records "no AI training/mining" in the C2PA manifest
    terms_url = "https://acme.example/ai-terms"
    author = "Acme Corp"
    copyright = "© 2026 Acme Corp"

Layering, strongest first: command-line flags > --preset > the profile > [export] in the file > built-in defaults.
Select with `mysuite --profile acme …` or MYSUITE_PROFILE=acme.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Any

POLICIES = ("strip", "randomize", "credit", "declare")
TOP_KEYS = {"description", "tokens", "brand", "theme", "variants", "negative_map", "allow", "compress_preset",
            "enhance_preset", "export", "metadata"}
META_KEYS = {"policy", "author", "copyright", "no_ai", "terms_url"}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    name: str
    description: str | None = None
    tokens: str | None = None
    brand: str | None = None
    theme: str | None = None
    variants: str | None = None
    negative_map: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    compress_preset: str | None = None
    enhance_preset: str | None = None
    export: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def policy(self) -> list[str]:
        p = self.metadata.get("policy") or []
        return [p] if isinstance(p, str) else list(p)


def parse_profile(name: str, data: Any) -> Profile:
    from mysuite.color.variants import ALL as VARIANTS
    from mysuite.config import ExportSettings

    if not isinstance(data, dict):
        raise ProfileError(f"profile {name!r} must be a table")
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise ProfileError(f"profile {name!r}: unknown key(s) {', '.join(sorted(unknown))} (allowed: {', '.join(sorted(TOP_KEYS))})")
    export = data.get("export", {})
    if not isinstance(export, dict):
        raise ProfileError(f"profile {name!r}: [export] must be a table")
    valid_export = {f.name for f in fields(ExportSettings)}
    bad = set(export) - valid_export
    if bad:
        raise ProfileError(f"profile {name!r}: unknown export setting(s) {', '.join(sorted(bad))}")
    meta = data.get("metadata", {})
    if not isinstance(meta, dict) or set(meta) - META_KEYS:
        raise ProfileError(f"profile {name!r}: [metadata] may only contain {', '.join(sorted(META_KEYS))}")
    profile = Profile(
        name=name, description=data.get("description"), tokens=data.get("tokens"), brand=data.get("brand"),
        theme=data.get("theme"), variants=data.get("variants"), negative_map=list(data.get("negative_map", [])),
        allow=list(data.get("allow", [])), compress_preset=data.get("compress_preset"),
        enhance_preset=data.get("enhance_preset"), export=dict(export), metadata=dict(meta),
    )
    if profile.theme not in (None, "light", "dark"):
        raise ProfileError(f"profile {name!r}: theme must be light or dark")
    if profile.variants:
        bad_v = [v for v in profile.variants.split(",") if v.strip() and v.strip() not in VARIANTS]
        if bad_v:
            raise ProfileError(f"profile {name!r}: unknown variant(s) {', '.join(bad_v)}")
    bad_p = [p for p in profile.policy if p not in POLICIES]
    if bad_p or (meta.get("policy") is not None and not profile.policy):
        raise ProfileError(f"profile {name!r}: metadata policy must be a list of {', '.join(POLICIES)}")
    if "credit" in profile.policy and not meta.get("author"):
        raise ProfileError(f"profile {name!r}: the credit policy needs metadata.author")
    if "no_ai" in meta and not isinstance(meta["no_ai"], bool):
        raise ProfileError(f"profile {name!r}: metadata.no_ai must be true or false")
    for key in ("tokens", "brand", "variants", "compress_preset", "enhance_preset"):
        if getattr(profile, key) is not None and not isinstance(getattr(profile, key), str):
            raise ProfileError(f"profile {name!r}: {key} must be text")
    if not all(isinstance(a, str) for a in profile.allow + profile.negative_map):
        raise ProfileError(f"profile {name!r}: allow and negative_map must be lists of text")
    return profile


_active: str | None = None


def activate(name: str | None) -> None:
    global _active
    _active = name or None


def active_name() -> str | None:
    return _active or (os.environ.get("MYSUITE_PROFILE") or None)


def resolve(config) -> Profile | None:
    """The active profile from this config (None if no profile is selected). Unknown name -> ProfileError."""
    name = active_name()
    if not name:
        return None
    if name not in config.profiles:
        have = ", ".join(sorted(config.profiles)) or "none defined"
        raise ProfileError(f"unknown profile {name!r} (defined: {have})")
    return parse_profile(name, config.profiles[name])
