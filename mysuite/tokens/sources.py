"""Where tokens come from: a local file or folder, a pinned git repo (opt-in), or Figma (opt-in).

    tokens.json                      a DTCG JSON file
    brand.css                        a CSS custom-property file
    ./design-system/                 a folder: every .css/.json inside is merged (e.g. an Axis `css/bild` folder)
    git+https://host/org/repo@v1.2#path=packages/tokens/dist/css
                                     shallow clone pinned to a tag/branch/commit, cached in ~/.cache/mysuite/tokens
    figma:FILEKEY                    Figma variables (needs FIGMA_TOKEN; Enterprise API - see tokens/figma.py)

Network is touched ONLY by git+ and figma: sources, never implicitly.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mysuite.tokens import css as css_mod
from mysuite.tokens import dtcg, figma
from mysuite.tokens.model import TokenError, TokenSet

MAX_FILES = 400
MAX_FILE_BYTES = 8_000_000
_ALLOWED_PROTOCOLS = "https:ssh:file"
_SCHEMES = ("https://", "ssh://", "file://")


@dataclass
class Loaded:
    tokens: TokenSet
    description: str                 # for messages / JSON: where it came from
    commit: str | None = None        # git sources: the exact commit used


def cache_root() -> Path:
    return Path(os.environ.get("MYSUITE_CACHE", Path.home() / ".cache" / "mysuite")) / "tokens"


def parse_git_spec(spec: str) -> tuple[str, str, str]:
    """'git+https://host/org/repo@REF#path=sub' -> (url, ref, sub)."""
    body = spec[len("git+"):]
    body, _, sub = body.partition("#path=")
    if not body.startswith(_SCHEMES):
        raise TokenError("a git token source looks like git+https://host/org/repo@ref#path=sub/folder (https, ssh or file URLs)")
    url, sep, ref = body.rpartition("@")
    if not sep or "/" in ref or not ref or "://" not in url:
        raise TokenError("pin the git source to a tag, branch or commit: git+https://host/org/repo@REF")
    if not re.fullmatch(r"[\w.\-/]+", ref) or ref.startswith("-"):
        raise TokenError("that ref contains characters a tag, branch or commit never has")
    return url, ref, sub.strip("/")


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": _ALLOWED_PROTOCOLS, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    try:
        proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=180)
    except FileNotFoundError as exc:
        raise TokenError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise TokenError("git took too long") from exc
    if proc.returncode != 0:
        raise TokenError("git failed: " + (proc.stderr.strip().splitlines() or ["unknown error"])[-1])
    return proc.stdout.strip()


def fetch_git(spec: str, refresh: bool = False) -> tuple[Path, str]:
    url, ref, sub = parse_git_spec(spec)
    if ".." in Path(sub).parts or Path(sub).is_absolute():
        raise TokenError("the #path= must be a folder inside the repository")
    key = hashlib.sha256(f"{url}@{ref}".encode()).hexdigest()[:16]
    repo = cache_root() / key
    if refresh and repo.exists():
        shutil.rmtree(repo)
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        _run_git(["init", "-q"], repo)
        _run_git(["remote", "add", "origin", url], repo)
        _run_git(["fetch", "-q", "--depth", "1", "origin", ref], repo)
        _run_git(["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], repo)
    commit = _run_git(["rev-parse", "HEAD"], repo)
    folder = (repo / sub) if sub else repo
    if not folder.exists():
        raise TokenError(f"the path {sub!r} does not exist in {url}@{ref}")
    return folder, commit


def _load_path(path: Path, tokens: TokenSet) -> None:
    files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in (".css", ".json")) if path.is_dir() else [path]
    if not files:
        raise TokenError(f"no .css or .json token files in {path}")
    if len(files) > MAX_FILES:
        raise TokenError(f"{len(files)} token files; the limit is {MAX_FILES} - point at a narrower folder")
    css_files: list[tuple[str, str | None]] = []
    for f in sorted(files):
        if f.stat().st_size > MAX_FILE_BYTES:
            raise TokenError(f"{f.name} is too large to be a token file")
        text = f.read_text(encoding="utf-8", errors="replace")
        if f.suffix.lower() == ".css":
            if "--" in text:
                css_files.append((text, str(f)))
        elif text.lstrip().startswith(("{", "[")):
            dtcg.load_json_text(text, str(f), tokens)
    if css_files:
        css_mod.parse_css_files(css_files, tokens)        # together: aliases may point into another file


def load_tokens(spec: str, *, refresh: bool = False) -> Loaded:
    """Resolve a token source spec (see the module docstring) into colour tokens."""
    if not spec or not spec.strip():
        raise TokenError("no token source given")
    spec = spec.strip()
    tokens = TokenSet()
    if spec.startswith("figma:"):
        payload = figma.fetch_variables(spec[len("figma:"):])
        return Loaded(figma.parse_variables(payload, spec), spec)
    commit = None
    if spec.startswith("git+"):
        folder, commit = fetch_git(spec, refresh)
        _load_path(folder, tokens)
        description = f"{spec} ({commit[:10]})"
    else:
        path = Path(spec).expanduser()
        if not path.exists():
            raise TokenError(f"token source not found: {spec}")
        from mysuite import sandbox

        sandbox.check_read(path.resolve())
        _load_path(path, tokens)
        description = str(path)
    if len(tokens) == 0:
        raise TokenError(f"no colour tokens found in {spec}")
    return Loaded(tokens, description, commit)
