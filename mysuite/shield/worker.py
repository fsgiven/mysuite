"""Runs INSIDE the private `shield` environment (PyTorch lives there, not in mysuite's own install).

    python worker.py '<json job>'   ->  prints one JSON line with the result

PhotoGuard-style *encoder attack* (Salman et al. 2023, MIT licence): a small, bounded change to the pixels that makes the
Stable Diffusion image encoder see the picture as something else (a flat grey), so image-to-image editing models built on
that encoder produce garbage or an unrelated picture instead of an edit of YOUR picture. It does not touch the
image's size, format or metadata; it is meant to be saved losslessly (PNG).

Honest limits (see docs/SHIELD.md): tuned to the SD 1.x/2.x VAE family, not guaranteed against other models, and
research shows that cleaning steps (upscaling, denoising, re-encoding) can weaken this kind of protection.
"""
from __future__ import annotations

import json
import sys
import time


def main(job: dict) -> dict:
    import numpy as np
    import torch
    from diffusers import AutoencoderKL
    from huggingface_hub import snapshot_download
    from PIL import Image

    device = job.get("device") or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    path = snapshot_download(job["repo"], revision=job["revision"], cache_dir=job["models_dir"], local_files_only=True,
                             allow_patterns=["config.json", "diffusion_pytorch_model.safetensors"])
    vae = AutoencoderKL.from_pretrained(path).to(device).eval()
    for p in vae.parameters():
        p.requires_grad_(False)

    eps = job["epsilon"] / 255.0 * 2.0            # budget in [-1, 1] pixel space
    step = eps / 8.0
    steps = int(job["steps"])
    tile = int(job.get("tile", 512))
    torch.manual_seed(int(job.get("seed", 0)))

    def encode(v):
        return vae.encode(v).latent_dist.mean

    def protect(arr: "np.ndarray") -> "tuple[np.ndarray, float, float]":
        """arr: HxWx3 uint8. Returns (protected, encoder distance to the grey target before, after)."""
        h, w, _ = arr.shape
        ph, pw = (-h) % 8, (-w) % 8                  # the encoder needs multiples of 8
        padded = np.pad(arr, ((0, ph), (0, pw), (0, 0)), mode="reflect") if (ph or pw) else arr
        x = torch.from_numpy(padded).float().permute(2, 0, 1)[None].to(device) / 127.5 - 1
        with torch.no_grad():
            z0 = encode(x)
            target = encode(torch.zeros_like(x))
            before = ((z0 - target) ** 2).mean().sqrt().item()
        delta = torch.empty_like(x).uniform_(-eps / 4, eps / 4).requires_grad_(True)
        for _ in range(steps):
            loss = ((encode((x + delta).clamp(-1, 1)) - target) ** 2).mean()
            loss.backward()
            with torch.no_grad():
                delta -= step * delta.grad.sign()
                delta.clamp_(-eps, eps)
                delta.copy_((x + delta).clamp(-1, 1) - x)
                delta.grad.zero_()
        protected = (x + delta).detach()
        with torch.no_grad():
            after = ((encode(protected) - target) ** 2).mean().sqrt().item()
        out = ((protected[0].permute(1, 2, 0).cpu().numpy() + 1) * 127.5).round().clip(0, 255).astype(np.uint8)[:h, :w]
        return out, before, after

    t0 = time.time()
    with Image.open(job["input"]) as im:
        im.seek(0)
        rgba = im.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB"))
    alpha = rgba.getchannel("A")
    h, w, _ = rgb.shape
    result = np.empty_like(rgb)
    befores, afters, tiles = [], [], 0
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            block = rgb[y:y + tile, x:x + tile]
            if block.shape[0] < 16 or block.shape[1] < 16:      # a sliver too small to encode: leave it, merged below
                result[y:y + tile, x:x + tile] = block
                continue
            out, b, a = protect(block)
            result[y:y + tile, x:x + tile] = out
            befores.append(b)
            afters.append(a)
            tiles += 1
            print(json.dumps({"progress": tiles, "of": ((h + tile - 1) // tile) * ((w + tile - 1) // tile)}), file=sys.stderr, flush=True)
    img = Image.fromarray(result, "RGB")
    if alpha.getextrema()[0] < 255:
        img = img.convert("RGBA")
        img.putalpha(alpha)
    img.save(job["output"], format="PNG")
    diff = result.astype(float) - rgb.astype(float)
    mse = float((diff ** 2).mean())
    return {
        "width": w, "height": h, "tiles": tiles, "device": device, "seconds": round(time.time() - t0, 1),
        "psnr_db": None if mse == 0 else round(10 * float(np.log10(255 ** 2 / mse)), 2),
        "max_pixel_change": int(np.abs(diff).max()),
        "encoder_distance_to_grey_before": round(sum(befores) / max(1, len(befores)), 3),
        "encoder_distance_to_grey_after": round(sum(afters) / max(1, len(afters)), 3),
    }


if __name__ == "__main__":
    print(json.dumps(main(json.loads(sys.argv[1]))))
