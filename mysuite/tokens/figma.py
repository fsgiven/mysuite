"""Colour variables from Figma's REST API (GET /v1/files/:key/variables/local).

HONEST LIMITS: Figma's Variables REST API is only available on Enterprise plans, and this adapter has NOT been run
against a live Figma file - it is tested against a hand-written response in the documented shape. The token comes from
the FIGMA_TOKEN environment variable (a personal access token with the file_variables:read scope), never from a file
or an argument. If you can't use the API, export your variables to a DTCG JSON file (Tokens Studio, or the Figma
"Variables" export plugins) and load that instead.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from mysuite.tokens.model import Token, TokenError, TokenSet

API = "https://api.figma.com/v1/files/{key}/variables/local"
TIMEOUT = 20


def fetch_variables(file_key: str, env: dict[str, str] | None = None, opener=urllib.request.urlopen) -> dict[str, Any]:
    token = (env if env is not None else os.environ).get("FIGMA_TOKEN")
    if not token:
        raise TokenError("set the FIGMA_TOKEN environment variable (a Figma personal access token with file_variables:read)")
    if not file_key or not all(c.isalnum() or c in "-_" for c in file_key):
        raise TokenError("that doesn't look like a Figma file key")
    request = urllib.request.Request(API.format(key=file_key), headers={"X-Figma-Token": token, "User-Agent": "mysuite"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = {403: " (the Variables API needs an Enterprise plan and the file_variables:read scope)", 404: " (file not found)"}.get(exc.code, "")
        raise TokenError(f"Figma answered {exc.code}{hint}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TokenError(f"couldn't reach Figma: {exc}") from exc


def _hex(c: dict[str, float]) -> str:
    r, g, b = (round(max(0.0, min(1.0, c[k])) * 255) for k in ("r", "g", "b"))
    a = c.get("a", 1.0)
    return f"#{r:02x}{g:02x}{b:02x}" if a >= 0.999 else f"rgba({r}, {g}, {b}, {round(a, 3)})"


def parse_variables(payload: dict[str, Any], source: str = "figma", brand: str | None = None) -> TokenSet:
    meta = payload.get("meta") or payload
    variables, collections = meta.get("variables") or {}, meta.get("variableCollections") or {}
    tokens = TokenSet()

    def value_in_mode(var: dict[str, Any], mode_id: str, depth: int = 0) -> dict[str, float] | None:
        if depth > 10:
            return None
        v = (var.get("valuesByMode") or {}).get(mode_id)
        if isinstance(v, dict) and v.get("type") == "VARIABLE_ALIAS":
            target = variables.get(v.get("id"))
            if not target:
                return None
            tcol = collections.get(target.get("variableCollectionId"), {})
            return value_in_mode(target, mode_id if mode_id in (target.get("valuesByMode") or {}) else tcol.get("defaultModeId", mode_id), depth + 1)
        return v if isinstance(v, dict) and {"r", "g", "b"} <= v.keys() else None

    for var in variables.values():
        if var.get("resolvedType") != "COLOR":
            tokens.skipped_non_colour += 1
            continue
        collection = collections.get(var.get("variableCollectionId"), {})
        modes = {m["modeId"]: m.get("name", "").lower() for m in collection.get("modes", [])}
        values: dict[str, str] = {}
        default = collection.get("defaultModeId") or next(iter(modes), None)
        for mode_id, mode_name in modes.items():
            color = value_in_mode(var, mode_id)
            if color is None:
                continue
            theme = "dark" if "dark" in mode_name else "light" if ("light" in mode_name or mode_id == default) else None
            if theme and theme not in values:
                values[theme] = _hex(color)
        if values:
            tokens.add(Token(name=var.get("name", "").replace("/", "."), brand=brand, values=values, description=var.get("description") or None, source=source))
    tokens.sources.append(source)
    return tokens
