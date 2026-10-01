# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's private reporting instead:
[Report a vulnerability](https://github.com/fsgiven/mysuite/security/advisories/new). Include what you
ran, on what kind of file, and what happened. I'll acknowledge reports as soon as I can and credit you
in the fix if you'd like.

Only the latest release on `main` is supported.

## What mysuite does, and the trust boundary

mysuite runs entirely on your machine — no network calls, no telemetry. It works by passing **your
files** to other programs: `exiftool`, ImageMagick (`magick`), Ghostscript (`gs`), `rsvg-convert`,
`c2patool`, and the compression encoders (`cjpeg`, `cwebp`, `avifenc`, `oxipng`, `pngquant`,
`gifsicle`). That has two consequences:

- **Treat input files as the attack surface.** Image, PDF, EPS and SVG parsers have a long history of
  vulnerabilities. Don't run mysuite on files or folders you don't trust (for example an unpacked
  download) unless you're comfortable with that, and keep those tools updated (`brew upgrade`).
- **Filenames are untrusted too.** mysuite always hands tools absolute paths, so a file named like
  `-all=.png` can't be mistaken for an option. If you find a way around that, please report it.

mysuite never modifies your originals; every output is a new file beside the source.

## What this is not

- `metadata strip` and `metadata randomize` remove or replace *metadata*. They don't alter pixels, so
  they don't defeat image forensics (sensor-noise fingerprints, content matching) and they don't
  anonymize the picture itself.
- `metadata credit` signs with c2patool's built-in **test** certificate: it records provenance for your
  own verification but is not third-party-trusted.
