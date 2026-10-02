"""`mysuite shield`: experimental image protection against diffusion-based editing (runs in the private shield env)."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from mysuite import components
from mysuite.utils.subprocess_utils import atomic_write_via

WORKER = Path(__file__).resolve().parent / "worker.py"
MAX_PIXELS = 40_000_000
TILE = 512
# (epsilon in 1/255 steps, iterations) per strength name. Measured on this Mac (MPS): ~1 s per iteration per 512x512 tile.
STRENGTHS = {"light": (8, 40), "standard": (12, 60), "strong": (16, 100)}


class ShieldError(RuntimeError):
    pass


@dataclass
class ShieldResult:
    input_path: Path
    output_path: Path
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def output_path_for(path: Path) -> Path:
    return path.with_name(f"{path.stem}_shielded.png")


def estimate_seconds(width: int, height: int, steps: int, device_speed: float = 1.0) -> int:
    tiles = ((height + TILE - 1) // TILE) * ((width + TILE - 1) // TILE)
    return round(tiles * steps * device_speed)


def shield_file(path: Path, *, strength: str = "standard", steps: int | None = None, epsilon: int | None = None,
                device: str | None = None, seed: int = 0, overwrite: bool = False,
                progress: Callable[[int, int], None] | None = None) -> ShieldResult:
    if strength not in STRENGTHS:
        raise ShieldError(f"strength must be one of {', '.join(STRENGTHS)}")
    eps, it = STRENGTHS[strength]
    eps, it = epsilon or eps, steps or it
    if not 1 <= eps <= 32 or not 5 <= it <= 400:
        raise ShieldError("epsilon must be 1-32 (out of 255) and steps 5-400")
    if device not in (None, "cpu", "mps", "cuda"):
        raise ShieldError("device must be cpu, mps or cuda")
    path = Path(os.path.abspath(path))
    out = output_path_for(path)
    if out == path:
        raise ShieldError("the output would replace the input")
    if out.exists() and not overwrite:
        return ShieldResult(path, out, "skipped_existing")
    if not components.get("shield").is_installed():
        raise ShieldError("the shield component is not installed: run `mysuite install shield`")
    try:
        with Image.open(path) as im:
            width, height = im.size
            animated = getattr(im, "n_frames", 1) > 1
    except Exception as exc:  # noqa: BLE001
        raise ShieldError(f"can't read {path.name}: {exc}") from exc
    if width * height > MAX_PIXELS:
        raise ShieldError(f"{width}x{height} is too large (limit {MAX_PIXELS // 1_000_000} MP)")

    job = {"input": str(path), "epsilon": eps, "steps": it, "device": device, "seed": seed,
           "repo": components.SHIELD_VAE_REPO, "revision": components.SHIELD_VAE_REVISION,
           "models_dir": str(components.models_dir())}
    notes = [f"saved as lossless PNG: re-saving it as JPEG or resizing it weakens the protection"]
    if animated:
        notes.append("animated source: only the first frame was protected")

    def write(tmp: Path) -> None:
        job["output"] = str(tmp)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["HF_HUB_OFFLINE"] = "1"                         # the worker never touches the network
        proc = subprocess.Popen([str(components.env_python("shield")), str(WORKER), json.dumps(job)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        assert proc.stderr is not None
        err_lines: list[str] = []
        for line in proc.stderr:
            try:
                msg = json.loads(line)
                if progress and "progress" in msg:
                    progress(msg["progress"], msg["of"])
                continue
            except ValueError:
                err_lines.append(line)
        stdout = proc.stdout.read() if proc.stdout else ""
        if proc.wait() != 0:
            raise ShieldError("the shield worker failed: " + ("".join(err_lines).strip().splitlines() or ["unknown error"])[-1])
        try:
            job["result"] = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ShieldError("the shield worker returned nothing readable") from exc

    atomic_write_via(out, write, preserve_extension=True)
    metrics = job.get("result", {})
    metrics.update({"epsilon": eps, "steps": it, "strength": strength})
    if metrics.get("psnr_db") is not None and metrics["psnr_db"] < 30:
        notes.append(f"the added pattern is visible on flat areas and text ({metrics['psnr_db']} dB): try --strength light for photos you will show large")
    return ShieldResult(path, out, "written", metrics, notes)
