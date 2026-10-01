from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# Standard shutter speeds and full-stop apertures a real camera would report.
_SHUTTERS = ["1/30", "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000"]
_STOPS = [2.8, 4.0, 5.6, 8.0, 11.0]


@dataclass(frozen=True)
class CameraProfile:
    """One real device, with fields that belong together. A decoy is built from
    a single profile so make, model, lens and firmware never contradict each
    other — the point is a plausible-looking set, not independent random
    values. Firmware/Software strings follow each vendor's real style but are
    not verified against firmware databases, so treat them as plausible rather
    than authoritative."""

    make: str
    model: str
    software: str | None
    lens_make: str | None
    lens_model: str | None
    focal_lengths: tuple[float, ...]  # mm; one entry = fixed lens, several = zoom range to pick from
    apertures: tuple[float, ...]  # f-numbers this lens can really use
    isos: tuple[int, ...]
    crop_factor: float  # sensor crop vs 35mm, for FocalLengthIn35mmFormat
    dpi: int  # the X/YResolution this vendor writes
    phone: bool = False


PROFILES: tuple[CameraProfile, ...] = (
    CameraProfile(
        make="Canon", model="Canon EOS 5D Mark IV", software=None,
        lens_make="Canon", lens_model="EF24-70mm f/2.8L II USM",
        focal_lengths=(24.0, 35.0, 50.0, 70.0), apertures=tuple(_STOPS),
        isos=(100, 200, 400, 800, 1600, 3200), crop_factor=1.0, dpi=72,
    ),
    CameraProfile(
        make="NIKON CORPORATION", model="NIKON D850", software="Ver.1.10",
        lens_make="Nikon", lens_model="AF-S NIKKOR 24-70mm f/2.8E ED VR",
        focal_lengths=(24.0, 35.0, 50.0, 70.0), apertures=tuple(_STOPS),
        isos=(64, 100, 200, 400, 800, 1600, 3200), crop_factor=1.0, dpi=300,
    ),
    CameraProfile(
        make="SONY", model="ILCE-7M4", software="ILCE-7M4 v3.00",
        lens_make="Sony", lens_model="FE 24-70mm F2.8 GM II",
        focal_lengths=(24.0, 35.0, 50.0, 70.0), apertures=tuple(_STOPS),
        isos=(100, 200, 400, 800, 1600, 3200), crop_factor=1.0, dpi=350,
    ),
    CameraProfile(
        make="FUJIFILM", model="X-T4", software="Digital Camera X-T4 Ver1.20",
        lens_make="Fujifilm", lens_model="XF16-55mmF2.8 R LM WR",
        focal_lengths=(16.0, 23.0, 35.0, 55.0), apertures=tuple(_STOPS),
        isos=(160, 200, 400, 800, 1600, 3200), crop_factor=1.5, dpi=72,
    ),
    CameraProfile(
        make="Apple", model="iPhone 15 Pro", software="17.5.1",
        lens_make="Apple", lens_model="iPhone 15 Pro back triple camera 6.765mm f/1.78",
        focal_lengths=(6.765,), apertures=(1.78,),
        isos=(50, 64, 80, 125, 200, 400, 800), crop_factor=24 / 6.765, dpi=72, phone=True,
    ),
    CameraProfile(
        make="Apple", model="iPhone 13", software="16.6",
        lens_make="Apple", lens_model="iPhone 13 back dual wide camera 5.1mm f/1.6",
        focal_lengths=(5.1,), apertures=(1.6,),
        isos=(32, 50, 64, 100, 160, 250, 500), crop_factor=26 / 5.1, dpi=72, phone=True,
    ),
    CameraProfile(
        make="samsung", model="SM-S918B", software="S918BXXU2AWBN",
        lens_make=None, lens_model=None,
        focal_lengths=(6.3,), apertures=(1.7,),
        isos=(50, 64, 100, 200, 400, 800), crop_factor=23 / 6.3, dpi=72, phone=True,
    ),
)


def _random_capture_time(rng: random.Random, now: datetime | None = None) -> str:
    now = now or datetime.now()
    day = now - timedelta(days=rng.randint(1, 540))
    taken = day.replace(
        hour=rng.randint(7, 19), minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0
    )
    return taken.strftime("%Y:%m:%d %H:%M:%S")


def build_decoy_tags(
    rng: random.Random | None = None, now: datetime | None = None
) -> tuple[CameraProfile, dict[str, str]]:
    """Picks one real profile and returns (profile, {exiftool tag: value}).
    Per-photo values that legitimately vary (exposure, ISO, aperture within
    the lens's real range, focal length within the lens's real range, capture
    time) are drawn independently of each other but always from that profile's
    own valid sets. Never includes GPS: a fake location adds risk without
    helping the goal, which is plausible camera identity, not a made-up place."""
    rng = rng or random.Random()
    profile = rng.choice(PROFILES)

    focal = rng.choice(profile.focal_lengths)
    when = _random_capture_time(rng, now)
    tags: dict[str, str] = {
        "Make": profile.make,
        "Model": profile.model,
        "ExifVersion": "0232",
        "XResolution": str(profile.dpi),
        "YResolution": str(profile.dpi),
        "ResolutionUnit": "inches",
        "FNumber": str(rng.choice(profile.apertures)),
        "ExposureTime": rng.choice(_SHUTTERS),
        "ISO": str(rng.choice(profile.isos)),
        "FocalLength": f"{focal:g}",
        "FocalLengthIn35mmFormat": str(round(focal * profile.crop_factor)),
        "ExposureProgram": "Program AE" if profile.phone else rng.choice(
            ["Manual", "Program AE", "Aperture-priority AE"]
        ),
        "MeteringMode": "Multi-segment",
        "Flash": "Off, Did not fire",
        "WhiteBalance": "Auto",
        "DateTimeOriginal": when,
        "CreateDate": when,
        "ModifyDate": when,
    }
    if profile.software:
        tags["Software"] = profile.software
    if profile.lens_make:
        tags["LensMake"] = profile.lens_make
    if profile.lens_model:
        tags["LensModel"] = profile.lens_model
    if not profile.phone:
        tags["ColorSpace"] = "sRGB"
    return profile, tags
