# Protecting your images: what each layer does

No layer stops a determined person who copies your file. They differ in what they prove, prevent or merely request.

| Layer | Command | What it does | What it does not do | Strength |
| --- | --- | --- | --- | --- |
| Private data out | `mysuite metadata strip` (`--keep-credit` keeps the credit) | Removes GPS, camera make/model/serial numbers, software, dates and comments from a copy. The colour profile stays (`--drop-icc` removes it). | Nothing in the pixels: a street sign still shows where you were. | Dependable |
| Credit | `mysuite metadata credit` | Writes author (IPTC By-line, `dc:creator`), copyright notice (`dc:rights`), credit line, source and usage terms as IPTC Core with exiftool. When `c2patool` is installed, also a C2PA record, signed **with c2patool's test certificate** (it shows tampering; verifiers show the signer as untrusted). | Metadata is easy to strip; many sites remove it on upload. | A label, not a lock |
| Ownership mark | `mysuite mark embed / detect` | Hides a keyed pattern in the picture. Later, `detect` tells you whether a picture carries *your* mark. Keeps the file format and the metadata. | It carries no message and prevents nothing. It is a proof, not a lock. | Measured below |
| "No AI" declaration | `mysuite metadata declare`, `credit --no-ai`, profile `no_ai` | Writes the PLUS `DataMining` property and XMP Rights into a copy, and a C2PA "training-mining: notAllowed" assertion when c2patool is installed. | Only crawlers and tools that choose to read it obey it. Stripping metadata removes it. It removes nothing: GPS stays (a warning says so). | A legal/consent signal |
| Shield (experimental, CLI only) | `mysuite shield` | Disrupts Stable Diffusion 1.x/2.x-style *editing* of the picture. See [SHIELD.md](SHIELD.md). | Does **not** stop ChatGPT, Gemini, Midjourney or other closed services, training or copying; resizing removes most of it. | Experimental |
| Visible watermark | `mysuite watermark` | A logo on the picture. | Can be cropped or painted out. | Deterrent only |

**All in one go:** `mysuite metadata protect photo.jpg --author "Jane Doe" --copyright "© 2026 Jane Doe"` removes the private
data (keeps your credit and the colour profile), writes your credit, declares "no AI use" and, with `--mark` (key in
`MYSUITE_MARK_KEY`), adds the ownership mark. It writes **one** file, `photo_protected.jpg`, and leaves no intermediate files.
The mark goes in before the metadata steps, and a C2PA record (if any) is signed last so it covers the finished file.

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

1. Keep the original private. Publish a copy made with `metadata protect --mark` (private data out, credit in, "no AI" notice,
   ownership mark), and a visible watermark if you like. A company profile with `policy = ["protect"]` does the same (without
   the mark; add `"mark"` to the policy and set `MYSUITE_MARK_KEY` for it) with `metadata apply`.
2. Only if editing misuse of a particular picture worries you, try `shield` (experimental, CLI only; see its limits) on the
   published copy, and do not count on it.
3. Keep a log of what you published with which key/id, so a later `detect` means something.
