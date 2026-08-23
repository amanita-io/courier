"""MediaAsset parsing. No database.

These pin the behaviour of parse_json.sql, which the Python port has
to reproduce exactly — including the parts that look like bugs.
"""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

from courier_tms.media.models import MediaAsset

from conftest import THUMBNAIL_B64, THUMBNAIL_BYTES, asset_json


def parse(**overrides) -> MediaAsset:
    return MediaAsset.from_json(asset_json("a1", 42, **overrides))


# ── key styles ───────────────────────────────────────────────────────


def test_legacy_pascal_case_feed_maps_onto_native_fields():
    asset = parse()
    assert asset.asset_id == "a1"
    assert asset.tms_record_id == 42
    assert asset.file_name == "a1.jpg"
    assert asset.media_format == "JPEG"
    assert asset.pixel_width == 800
    assert asset.pixel_height == 600
    assert asset.external_id == "ext-a1"


def test_native_snake_case_feed_parses_on_its_own():
    asset = MediaAsset.from_json({
        "asset_id": "n1",
        "tms_record_id": 7,
        "image_url": "https://dam.example.org/a/b/n1.jpg",
        "file_name": "n1.jpg",
        "image_size": 10,
        "media_status": "Internal Use Only",
        "media_type": "Image",
        "media_format": "TIFF",
        "pixel_width": 1,
        "pixel_height": 2,
        "external_id": "x",
        "thumbnail_b64": THUMBNAIL_B64,
    })
    assert asset.asset_id == "n1"
    assert asset.media_status == "Internal Use Only"


def test_native_key_wins_when_both_styles_name_the_same_field():
    asset = MediaAsset.from_json(
        asset_json("a1", 42, file_name="native-wins.jpg")
    )
    assert asset.file_name == "native-wins.jpg"


# ── required fields (the staging table's NOT NULL columns) ───────────


@pytest.mark.parametrize("legacy_key", [
    "AssetId", "ObjectId", "ShareURL", "ImageSizeInKilobytes", "Filename",
    "MediaStatus", "MediaType", "MediaFormat", "ImageWidth", "ImageHeight",
    "AssetExternalId", "ThumbData",
])
def test_missing_required_field_is_rejected(legacy_key):
    payload = asset_json("a1", 42)
    del payload[legacy_key]
    with pytest.raises(ValueError, match="missing required field"):
        MediaAsset.from_json(payload)


def test_empty_string_counts_as_missing():
    with pytest.raises(ValueError, match="missing required field"):
        MediaAsset.from_json(asset_json("a1", 42, Filename=""))


def test_optional_fields_may_be_absent():
    payload = asset_json("a1", 42)
    for key in ("ImageRestrictions", "ImageDescription", "ImageCreatorsDepartment",
                "ImageSource", "DeletedDate"):
        payload.pop(key, None)
    asset = MediaAsset.from_json(payload)
    assert asset.description is None
    assert asset.restrictions is None
    assert asset.department is None
    assert asset.image_source is None
    assert asset.deleted_date is None
    # ...and the required fields still came through
    assert asset.asset_id == "a1"


# ── the 'true'/'false' string booleans ───────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("true", 1), ("TRUE", 1), ("True", 1), (" true ", 1),
    ("false", 0), ("FALSE", 0), ("", 0), ("yes", 0), ("1", 0),
    (True, 1), (False, 0), (1, 1), (0, 0), (None, 0),
])
def test_boolean_fields_follow_the_case_when_true_rule(value, expected):
    """parse_json.sql was CASE x WHEN 'true' THEN 1 ELSE 0 END.

    Note what that means for the string '1': it is not 'true', so it is
    0. Faithful, and worth pinning — a feed that starts sending 1/0 as
    strings would silently unpublish images.
    """
    asset = parse(IsPrimary=value, ApprovedForWebsite=value)
    assert asset.primary_display == expected
    assert asset.approved_for_web == expected


# ── dates ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("2026-03-04T05:06:07", datetime(2026, 3, 4, 5, 6, 7)),
    ("2026-03-04 05:06:07", datetime(2026, 3, 4, 5, 6, 7)),
    ("2026-03-04", datetime(2026, 3, 4)),
    ("2026-03-04T05:06:07.123456", datetime(2026, 3, 4, 5, 6, 7)),
    ("2026-03-04T05:06:07Z", datetime(2026, 3, 4, 5, 6, 7)),
])
def test_date_formats_the_feed_actually_sends(value, expected):
    assert parse(UpdatedDate=value).updated_date == expected


@pytest.mark.parametrize("value", [None, ""])
def test_absent_dates_become_none(value):
    assert parse(DeletedDate=value).deleted_date is None


def test_a_datetime_passes_straight_through():
    when = datetime(2026, 5, 1, 12, 0, 0)
    assert parse(UpdatedDate=when).updated_date == when


def test_an_unparseable_date_is_an_error_not_a_silent_none():
    with pytest.raises(ValueError, match="Unparseable date"):
        parse(UpdatedDate="last Tuesday")


# ── thumbnail ────────────────────────────────────────────────────────


def test_thumbnail_is_decoded_to_bytes():
    asset = parse()
    assert isinstance(asset.thumbnail, bytes)
    assert asset.thumbnail == THUMBNAIL_BYTES
    assert asset.thumbnail.startswith(b"\x89PNG")


def test_thumbnail_length_is_the_datalength_written_to_thumbblobsize():
    asset = parse()
    assert len(asset.thumbnail) == len(base64.b64decode(THUMBNAIL_B64))


# ── numeric coercion ─────────────────────────────────────────────────


def test_numeric_strings_are_coerced():
    asset = parse(ImageWidth="1024", ImageHeight="768", ImageSizeInKilobytes="99")
    assert (asset.pixel_width, asset.pixel_height, asset.image_size) == (1024, 768, 99)
