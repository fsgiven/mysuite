# Protecting your images: what each layer does

No layer stops a determined person who copies your file. They differ in what they prove, prevent or merely request.

| Layer | Command | What it does | What it does not do | Strength |
| --- | --- | --- | --- | --- |
| Ownership mark | `mysuite mark embed / detect` | Hides a keyed pattern in the picture. Later, `detect` tells you whether a picture carries *your* mark. | It carries no message and prevents nothing. It is a proof, not a lock. | Measured below |
| "No AI" declaration | `mysuite metadata declare`, `credit --no-ai`, profile `no_ai` | Writes the PLUS `DataMining` property and XMP Rights into a copy, and a C2PA "training-mining: notAllowed" assertion with `credit --no-ai`. | Only crawlers and tools that choose to read it obey it. Stripping metadata removes it. | A legal/consent signal |
| Shield | `mysuite shield` | Disrupts Stable Diffusion-style *editing* of the picture. See [SHIELD.md](SHIELD.md). | Not a guarantee; model-specific; weakened by resizing. | Experimental |
| Visible watermark | `mysuite watermark` | A logo on the picture. | Can be cropped or painted out. | Deterrent only |

## The ownership mark, measured

A keyed spread-spectrum pattern, band-limited and tiled over the picture's brightness, weighted so flat areas get less. Detection
folds the suspect picture into one tile and correlates it with your pattern at several scales; a score (z) of 7 or more counts as
found. On generated 512 px photos at the default strength (about 40 dB PSNR, invisible to me): own mark z = 13-17; unmarked
pictures, a wrong key and a wrong id z = 3.5-4.3.

| Done to the marked picture | Found? |
| --- | --- |
| JPEG quality 30-90 | yes (z 12.7-13.5) |
| half size, three-quarter size, 1.5x enlargement (scale is found automatically) | yes |
| cropping | yes |
| mirroring | yes |
| mild blur (σ 1-2), brightness changes, added noise (even σ 40) | yes |
| half size then JPEG 70 | yes |
| heavy blur (σ 4+) | **no** |
| rotation (even 1°) | no, unless you pass `--deep` (slow, ±3°) |
| passing it through an AI image model (img2img, even a light 0.15 strength) | **no**: the pattern is gone |
| re-photographing the screen, screenshots at odd scales | not measured |

Practical meaning: it can show that a *copy of your file* came from you. It cannot show anything about a picture an AI
generated from yours. It is not secret from someone who has both the algorithm and your key, and different `--id` values give
different marks (e.g. one per recipient).

## A sensible routine

1. Keep the original private. Publish a copy with `mark embed` (and a visible watermark if you like).
2. Run `metadata declare` (or use a profile with `no_ai = true` and `metadata apply`) so the file says no AI use.
3. For images where editing misuse worries you most, try `shield` (experimental, see its limits) on the published copy.
4. Keep a log of what you published with which key/id, so a later `detect` means something.
