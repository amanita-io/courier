"""Canonical envelope normalisation. No database.

The envelope is the contract downstream tools consume, so its shape is
pinned here rather than left to whatever the extractor happens to emit.
"""

from __future__ import annotations

from courier_tms.canonical import normalize


def record(**overrides) -> dict:
    base = {
        "ObjectID": 42,
        "ObjectNumber": "1998.1.1",
        "ObjectName": "Painting",
        "Title": None,
        "titles": [{"title": "Sunrise", "title_type": "Primary", "display_order": 1}],
        "constituents": [],
        "components": [],
        "media": [],
    }
    base.update(overrides)
    return base


# ── envelope shape ───────────────────────────────────────────────────


def test_envelope_carries_a_namespaced_id_and_source():
    env = normalize(record())
    assert env["id"] == "tms:42"
    assert env["type"] == "Object"
    assert env["source"] == {"system": "tms", "id": "42"}


def test_raw_record_is_preserved_verbatim():
    """Nothing may be lost in normalisation: an old export has to be
    remappable years later without going back to the museum."""
    original = record(Medium="oil on canvas")
    env = normalize(original)
    assert env["raw"] == original


# ── label resolution ─────────────────────────────────────────────────


def test_label_prefers_the_first_objtitles_entry():
    assert normalize(record())["label"] == "Sunrise"


def test_label_skips_blank_titles():
    env = normalize(record(titles=[{"title": None}, {"title": "Real Title"}]))
    assert env["label"] == "Real Title"


def test_label_falls_back_to_object_name():
    assert normalize(record(titles=[]))["label"] == "Painting"


def test_label_falls_back_to_the_legacy_title_column():
    env = normalize(record(titles=[], ObjectName=None, Title="Legacy Title"))
    assert env["label"] == "Legacy Title"


def test_label_of_last_resort_names_the_object_number():
    env = normalize(record(titles=[], ObjectName=None, Title=None))
    assert env["label"] == "Object 1998.1.1"


def test_a_missing_object_number_is_synthesised_from_the_id():
    env = normalize(record(ObjectNumber=None))
    assert env["object_number"] == "TMS-42"
    assert env["properties"]["object_number"] == "TMS-42"


# ── creators ─────────────────────────────────────────────────────────


def test_creators_are_the_creator_roled_constituents():
    env = normalize(record(constituents=[
        {"display_name": "A. Painter", "role": "Artist", "display_date": "1900"},
        {"display_name": "B. Donor", "role": "Donor", "display_date": None},
        {"display_name": "C. Photog", "role": "Photographer", "display_date": None},
    ]))
    assert [c["name"] for c in env["properties"]["creators"]] == ["A. Painter", "C. Photog"]
    # every constituent is still available, whatever their role
    assert len(env["properties"]["all_constituents"]) == 3


def test_no_creators_means_no_creators_key():
    env = normalize(record(constituents=[{"display_name": "B. Donor", "role": "Donor"}]))
    assert "creators" not in env["properties"]


# ── location ─────────────────────────────────────────────────────────


def test_current_location_is_the_first_component_that_has_one():
    env = normalize(record(components=[
        {"component_id": 1, "current_location": None},
        {"component_id": 2, "current_location": "Main > Gallery 3"},
        {"component_id": 3, "current_location": "Store > Rack 1"},
    ]))
    assert env["properties"]["current_location"] == "Main > Gallery 3"


# ── emptiness stripping ──────────────────────────────────────────────


def test_null_values_and_empty_lists_are_stripped():
    env = normalize(record(Medium=None, Provenance=None, exhibitions=[]))
    props = env["properties"]
    assert "medium" not in props
    assert "provenance" not in props
    assert "exhibitions" not in props


def test_an_all_null_nested_dict_is_stripped():
    env = normalize(record(context={"culture": None, "style": None, "period": None}))
    assert "context" not in env["properties"]


def test_a_partially_filled_nested_dict_is_kept():
    env = normalize(record(context={"culture": "Byzantine", "style": None}))
    assert env["properties"]["context"] == {"culture": "Byzantine", "style": None}


def test_tms_zero_dates_are_dropped_rather_than_exported_as_year_zero():
    env = normalize(record(DateBegin=0, DateEnd=0))
    assert "date_begin" not in env["properties"]
    assert "date_end" not in env["properties"]


def test_real_dates_survive():
    env = normalize(record(DateBegin=1889, DateEnd=1890))
    assert env["properties"]["date_begin"] == 1889
    assert env["properties"]["date_end"] == 1890


def test_a_falsy_but_meaningful_value_is_kept():
    """public_access = 0 means 'not public', which is not the same as
    'unknown' — stripping it would silently publish a restricted record."""
    env = normalize(record(PublicAccess=0, OnView=0))
    assert env["properties"]["public_access"] == 0
    assert env["properties"]["on_view"] == 0


# ── media ────────────────────────────────────────────────────────────


def test_media_entries_become_canonical_references():
    env = normalize(record(media=[
        {"media_master_id": 7, "file_name": "a.jpg", "primary_display": 1},
        {"media_master_id": 8, "file_name": "b.jpg", "primary_display": 0},
    ]))
    assert env["media"] == [
        {"id": "tms-media:7", "type": "image", "label": "a.jpg", "role": "primary"},
        {"id": "tms-media:8", "type": "image", "label": "b.jpg", "role": "alternate"},
    ]


def test_media_without_a_filename_is_not_referenced():
    """A MediaMaster row with no file behind it is a broken link, not
    an image — the raw record still has it."""
    env = normalize(record(media=[{"media_master_id": 9, "file_name": None}]))
    assert "media" not in env
    assert env["raw"]["media"][0]["media_master_id"] == 9


def test_no_media_key_when_the_object_has_none():
    assert "media" not in normalize(record())
