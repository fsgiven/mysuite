# mysuite shield - what it does, what it does not

`mysuite shield photo.png` makes an image **resist AI image editing**. It is experimental. Read this before relying on it.

## What it is

A PhotoGuard-style *encoder attack* (Salman et al., MIT, 2023; the reference code is MIT-licensed). It adds a small,
bounded change to the pixels (default: at most 12 of 255 per channel) chosen so that the Stable Diffusion image encoder
"sees" the picture as something else. Image-to-image editors built on that encoder then produce an unrelated or broken
picture instead of an edit of yours. The result is a lossless PNG of the same size, with the same transparency.

## What I measured (on this Mac, Stable Diffusion 1.5 image-to-image, strength 0.6)

Two generated photos (a fox in snow, a sailboat at sunset), shielded with `--strength standard` (60 steps, ~65 s per
512x512 tile on an Apple GPU, ~29 dB PSNR):

| What was edited | Result |
| --- | --- |
| the unprotected photo | a faithful edit: same fox/boat, same layout, snow added |
| the shielded PNG | a *different scene*: a cabin where the fox was, a cabin in the water instead of the boat |
| the shielded image saved as JPEG q85 | still disrupted (a cabin appears) |
| the shielded image shrunk to half size and enlarged again | **protection mostly lost**: the edit looks like the unprotected one |

The perturbation was made with the `sd-vae-ft-mse` VAE and tested against SD 1.5's own VAE, so it transferred between
those two. Not tested: SDXL, Flux, Midjourney, commercial services, newer editors.

## Why not to over-trust it

- **It is model-specific.** It targets the encoder of the SD 1.x/2.x family. SDXL, Flux and others use different encoders.
- **It is fragile.** Resizing, upscaling or denoising can remove it (my half-size test did). A 2024 study,
  [Adversarial Perturbations Cannot Reliably Protect Artists From Generative AI](https://arxiv.org/pdf/2406.12027),
  found that cheap steps like upscaling defeat Glaze, Mist and Anti-DreamBooth too, including updated versions.
- **It does not stop training or copying.** It aims at *editing/regeneration from your picture*, not at scrapers, and it does
  nothing for someone who simply downloads and reposts the picture.
- **It is visible on flat areas and text** at standard strength (~29 dB). Use `--strength light` for photos shown large.
- **It is an arms race.** Treat it as raising the cost for a casual attacker, not a guarantee.

## Cost

Needs `mysuite install shield` once (about 1.1-1.4 GB: a private PyTorch environment plus ~320 MB of weights, under
`~/.cache/mysuite`; remove with `mysuite install shield --remove`). Time is roughly `tiles x steps` seconds on an Apple GPU
(a 512x512 tile at 60 steps ~ 1 min), several times slower on CPU. Big images are processed in 512 px tiles. The worker
runs offline; only the install step uses the network.

## Better habits that cost nothing

Save the protected PNG as is (do not re-compress it), post a smaller preview than the original where you can, keep
originals private, and combine this with an invisible ownership mark and a "no AI training" declaration - see [PROTECTION.md](PROTECTION.md), which also lists what each layer proves and what survives.
