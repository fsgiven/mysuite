from __future__ import annotations

import random
import re
from datetime import datetime

from mysuite.metadata.camera_profiles import PROFILES, build_decoy_tags

_SEEDS = range(300)


def test_every_decoy_comes_from_exactly_one_real_profile():
    by_key = {(p.make, p.model): p for p in PROFILES}
    for seed in _SEEDS:
        profile, tags = build_decoy_tags(random.Random(seed))
        assert by_key[(tags["Make"], tags["Model"])] is profile


def test_per_photo_values_stay_inside_that_profiles_valid_sets():
    for seed in _SEEDS:
        profile, tags = build_decoy_tags(random.Random(seed))
        assert float(tags["FNumber"]) in profile.apertures
        assert float(tags["FocalLength"]) in profile.focal_lengths
        assert int(tags["ISO"]) in profile.isos


def test_lens_and_software_fields_match_what_the_profile_actually_has():
    for seed in _SEEDS:
        profile, tags = build_decoy_tags(random.Random(seed))
        assert tags.get("LensModel") == profile.lens_model or (profile.lens_model is None and "LensModel" not in tags)
        assert tags.get("Software") == profile.software or (profile.software is None and "Software" not in tags)


def test_never_writes_gps_or_identifying_free_text():
    forbidden = ("GPS", "Artist", "Copyright", "OwnerName", "SerialNumber", "UserComment", "ImageDescription")
    for seed in _SEEDS:
        _, tags = build_decoy_tags(random.Random(seed))
        assert not [k for k in tags if k.startswith(forbidden)]


def test_capture_time_is_past_daytime_and_well_formed():
    now = datetime(2026, 10, 1, 12, 0, 0)
    for seed in _SEEDS:
        _, tags = build_decoy_tags(random.Random(seed), now=now)
        assert re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", tags["DateTimeOriginal"])
        taken = datetime.strptime(tags["DateTimeOriginal"], "%Y:%m:%d %H:%M:%S")
        assert taken < now
        assert 7 <= taken.hour <= 19
        assert tags["CreateDate"] == tags["ModifyDate"] == tags["DateTimeOriginal"]


def test_all_profiles_are_reachable():
    seen = {build_decoy_tags(random.Random(s))[0].model for s in range(500)}
    assert seen == {p.model for p in PROFILES}


def test_same_seed_is_reproducible():
    assert build_decoy_tags(random.Random(7), now=datetime(2026, 1, 1)) == build_decoy_tags(
        random.Random(7), now=datetime(2026, 1, 1)
    )
