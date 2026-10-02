"""On-demand components: the mysuite install stays small, heavier parts are fetched once, when you ask.

    mysuite install list          what exists, what it costs, whether it is installed
    mysuite install vision --yes  build the macOS text-recognition/QR helper (needs the Xcode command line tools)
    mysuite install shield --yes  a private Python environment with PyTorch + the VAE weights for `mysuite shield` (~1.4 GB)
    mysuite install remove NAME   delete it again

Everything lives under ~/.cache/mysuite (override: MYSUITE_HOME): `bin/` (helpers, put on PATH for mysuite itself),
`envs/<name>/` (private virtual environments, never your own), `models/` (downloaded weights). Nothing is fetched
unless you run `install` and confirm (`--yes`); removing a component deletes its folder.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PACKAGE = Path(__file__).resolve().parent

# Pinned so a later change upstream cannot silently change what gets downloaded.
SHIELD_PACKAGES = ["torch>=2.2", "diffusers>=0.30", "safetensors", "numpy", "pillow", "huggingface_hub"]
SHIELD_VAE_REPO = "stabilityai/sd-vae-ft-mse"
SHIELD_VAE_REVISION = "31f26fdeee1355a5c34592e401dd41e45d25a493"


class ComponentError(RuntimeError):
    pass


def root() -> Path:
    return Path(os.environ.get("MYSUITE_HOME", Path.home() / ".cache" / "mysuite")).expanduser()


def bin_dir() -> Path:
    return root() / "bin"


def env_dir(name: str) -> Path:
    return root() / "envs" / name


def models_dir() -> Path:
    return root() / "models"


def activate_path() -> None:
    """Put the helper folder on PATH (for mysuite and the tools it starts). Idempotent; nothing is created."""
    d = str(bin_dir())
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if d not in parts:
        os.environ["PATH"] = os.pathsep.join([d, *parts])


def env_python(name: str) -> Path:
    return env_dir(name) / "bin" / "python"


@dataclass
class Component:
    name: str
    title: str
    description: str
    size: str                                  # human estimate, shown before anything is fetched
    needs: str                                 # what the machine must already have
    platforms: tuple[str, ...] = ("Darwin", "Linux", "Windows")
    steps: list[str] = field(default_factory=list)
    is_installed: Callable[[], bool] = lambda: False
    install: Callable[[Callable[[str], None]], None] = lambda log: None
    remove: Callable[[], list[Path]] = lambda: []


def _run(cmd: list[str], log: Callable[[str], None], cwd: Path | None = None) -> None:
    log("$ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ComponentError(f"{cmd[0]} was not found") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
        raise ComponentError(f"{' '.join(cmd[:3])} failed:\n" + "\n".join(tail))


# ------------------------------------------------------------------------------ native helpers
def _native(name: str, binary: str) -> Component:
    def installed() -> bool:
        return (bin_dir() / binary).exists() or shutil.which(binary) is not None

    def install(log: Callable[[str], None]) -> None:
        source = PACKAGE / "native" / name / "main.swift"
        if not source.exists():
            raise ComponentError(f"the {name} helper's source is missing from this install")
        if shutil.which("swiftc") is None:
            raise ComponentError("swiftc was not found: run `xcode-select --install` first (the Xcode command line tools)")
        bin_dir().mkdir(parents=True, exist_ok=True)
        target = bin_dir() / binary
        _run(["swiftc", "-O", str(source), "-o", str(target)], log)
        log(f"built {target}")

    def remove() -> list[Path]:
        target = bin_dir() / binary
        if target.exists():
            target.unlink()
            return [target]
        return []

    return Component(
        name=name, title=binary, size="about 1 MB, a few seconds to build",
        needs="macOS 13+ and the Xcode command line tools (swiftc)", platforms=("Darwin",),
        steps=[f"compile mysuite/native/{name}/main.swift with swiftc", f"install the binary to {bin_dir() / binary}"],
        is_installed=installed, install=install, remove=remove,
        description="",
    )


_cutout = _native("cutout", "mysuite-cutout")
_cutout.description = "Subject isolation for `mysuite cutout` (macOS Vision)."
_vision = _native("vision", "mysuite-vision")
_vision.description = "Text recognition and QR/barcode reading for `mysuite ocr` and `mysuite qr read` (macOS Vision)."


# ------------------------------------------------------------------------------ the shield environment
def _shield_installed() -> bool:
    return env_python("shield").exists() and (env_dir("shield") / ".mysuite-ready").exists()


def _shield_install(log: Callable[[str], None]) -> None:
    env = env_dir("shield")
    if not env_python("shield").exists():
        env.parent.mkdir(parents=True, exist_ok=True)
        _run([sys.executable, "-m", "venv", str(env)], log)
    py = str(env_python("shield"))
    _run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *SHIELD_PACKAGES], log)
    models_dir().mkdir(parents=True, exist_ok=True)
    code = (
        "from huggingface_hub import snapshot_download as d;"
        f"print(d({SHIELD_VAE_REPO!r}, revision={SHIELD_VAE_REVISION!r}, cache_dir={str(models_dir())!r}, "
        "allow_patterns=['config.json','diffusion_pytorch_model.safetensors']))"
    )
    _run([py, "-c", code], log)
    (env / ".mysuite-ready").write_text(f"{SHIELD_VAE_REPO}@{SHIELD_VAE_REVISION}\n")
    log("the shield environment is ready")


def _shield_remove() -> list[Path]:
    gone = []
    for p in (env_dir("shield"), models_dir() / "models--stabilityai--sd-vae-ft-mse"):
        if p.exists():
            shutil.rmtree(p)
            gone.append(p)
    return gone


_shield = Component(
    name="shield", title="shield environment",
    description="PyTorch + the Stable Diffusion VAE weights, in a private environment, for `mysuite shield` (experimental image protection).",
    size="about 1.4 GB (PyTorch ~1 GB, VAE weights ~320 MB)",
    needs="Python 3.9+ with venv and internet access once; a Mac with Apple GPU (MPS) or an NVIDIA GPU is much faster than CPU",
    steps=[f"create a private virtual environment at {env_dir('shield')}",
           "pip install: " + ", ".join(SHIELD_PACKAGES),
           f"download {SHIELD_VAE_REPO} @ {SHIELD_VAE_REVISION[:10]} (config + weights only) into {models_dir()}"],
    is_installed=_shield_installed, install=_shield_install, remove=_shield_remove,
)

# ------------------------------------------------------------------------------ the command-line tools (Homebrew)
BREW_TOOLS = {  # binary -> formula
    "rsvg-convert": "librsvg", "gs": "ghostscript", "magick": "imagemagick", "exiftool": "exiftool", "c2patool": "c2patool",
    "pdftocairo": "poppler", "cwebp": "webp", "avifenc": "libavif", "oxipng": "oxipng", "pngquant": "pngquant", "gifsicle": "gifsicle",
}


def _missing_formulae() -> list[str]:
    return sorted({f for b, f in BREW_TOOLS.items() if shutil.which(b) is None})


def _tools_install(log: Callable[[str], None]) -> None:
    if shutil.which("brew") is None:
        raise ComponentError("Homebrew was not found: install it from https://brew.sh first (or install the tools another way)")
    missing = _missing_formulae()
    if missing:
        _run(["brew", "install", *missing], log)


_tools = Component(
    name="tools", title="command-line tools",
    description="The command-line tools mysuite drives (rsvg-convert, Ghostscript, ImageMagick, exiftool, c2patool, poppler for PDF/AI-to-SVG, WebP/AVIF/oxipng/pngquant/gifsicle), via Homebrew - only the ones you are missing.",
    size="about 300-600 MB for all of them; only missing ones are installed",
    needs="Homebrew (https://brew.sh) and internet access once", platforms=("Darwin", "Linux"),
    steps=["brew install <the missing ones among: " + ", ".join(sorted(set(BREW_TOOLS.values()))) + ">"],
    is_installed=lambda: not _missing_formulae(), install=_tools_install, remove=lambda: [],
)

COMPONENTS: dict[str, Component] = {c.name: c for c in (_tools, _cutout, _vision, _shield)}


def get(name: str) -> Component:
    if name not in COMPONENTS:
        raise ComponentError(f"unknown component {name!r} - available: {', '.join(COMPONENTS)}")
    return COMPONENTS[name]


def supported(c: Component) -> bool:
    return platform.system() in c.platforms


def disk_usage(c: Component) -> int:
    paths = {"shield": [env_dir("shield"), models_dir() / "models--stabilityai--sd-vae-ft-mse"]}.get(c.name, [bin_dir() / c.title])
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total
