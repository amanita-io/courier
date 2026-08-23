"""Media sync against a real TMS database.

Skipped unless connection details are supplied — see conftest.

Everything here writes to the media tables, so read the safety
contract in conftest before changing it: test media only ever attaches
to an object that has none of its own, so the primary-display promote
and demote statements cannot reach a row the museum owns.
"""

from __future__ import annotations

from datetime import date

import pytest

from courier_tms.media import MediaAsset, MediaSync, SyncState

from conftest import (
    ASSET_URL_PREFIX,
    TABLE_ID_OBJECTS,
    TEST_LOGIN_ID,
    THUMBNAIL_BYTES,
    asset_json,
    marked_row_counts,
)

pytestmark = pytest.mark.live


@pytest.fixture
def sync(clean, tmp_path):
    """A MediaSync over a purged database and a fresh state file."""
    state = SyncState(str(tmp_path / "state.sqlite"))
    try:
        yield MediaSync(clean, state, login_id=TEST_LOGIN_ID)
    finally:
        state.close()


def feed(object_id: int, *asset_ids: str, **overrides) -> list[MediaAsset]:
    return [MediaAsset.from_json(asset_json(a, object_id, **overrides)) for a in asset_ids]


# ── read helpers, all scoped to rows this suite owns ─────────────────


def ours(conn, table: str) -> list[dict]:
    return conn.query(f"SELECT * FROM {table} WHERE LoginID = ?", (TEST_LOGIN_ID,))


def xrefs_for(conn, object_id: int) -> list[dict]:
    return conn.query(
        "SELECT * FROM MediaXrefs WHERE ID = ? AND TableID = ? ORDER BY MediaXrefID",
        (object_id, TABLE_ID_OBJECTS),
    )


def count(conn, sql: str, params=()) -> int:
    return conn.query(sql, params)[0]["n"]


# ── the full lifecycle ───────────────────────────────────────────────


def test_insert_then_bypass_then_update_then_delete(sync, clean, canary_object_id):
    """One asset through every state the feed can put it in.

    The phases run in one test because each depends on the sync state
    the previous one wrote — that dependency is the thing being tested.
    """
    conn, oid = clean, canary_object_id

    # ── phase 1: a new asset is inserted ─────────────────────────────
    result = sync.process(feed(oid, "courier-test-1"))
    assert (result.inserted, result.updated, result.deleted, result.ignored, result.failed) == (
        1, 0, 0, 0, 0
    ), f"unexpected result, errors: {result.errors}"

    masters = ours(conn, "MediaMaster")
    renditions = ours(conn, "MediaRenditions")
    files = ours(conn, "MediaFiles")
    assert len(masters) == len(renditions) == len(files) == 1

    master, rendition, mediafile = masters[0], renditions[0], files[0]

    # the two-pass identity dance: master, then rendition, then the
    # master updated to point back at it
    assert rendition["MediaMasterID"] == master["MediaMasterID"]
    assert master["DisplayRendID"] == rendition["RenditionID"]
    assert master["PrimaryRendID"] == rendition["RenditionID"]
    assert mediafile["RenditionID"] == rendition["RenditionID"]
    assert rendition["PrimaryFileID"] == mediafile["FileID"]

    # field mapping
    assert master["Description"] == "courier-test asset"
    assert master["ApprovedForWeb"] == 1
    assert master["PublicAccess"] == 1
    assert mediafile["FileName"] == "courier-test-1.jpg"
    assert mediafile["PixelW"] == 800
    assert mediafile["PixelH"] == 600
    assert mediafile["FileSize"] == 1234
    assert rendition["ParentRendID"] == -1
    assert rendition["RenditionDate"] is not None

    # the thumbnail survived base64 -> bytes -> image column intact,
    # which is what pytds.Binary wrapping exists for
    assert bytes(rendition["ThumbBLOB"]) == THUMBNAIL_BYTES
    assert rendition["ThumbBlobSize"] == len(THUMBNAIL_BYTES)

    # rendition numbering starts fresh for this login
    assert rendition["RenditionNumber"] == f"R.{date.today().year}-1"

    # the 5th-slash path rule, auto-created under our marker
    path = conn.query(
        "SELECT Path, PhysicalPath FROM MediaPaths WHERE PathID = ?", (mediafile["PathID"],)
    )[0]
    assert path["Path"] == ASSET_URL_PREFIX
    assert path["PhysicalPath"] == ASSET_URL_PREFIX

    # status auto-created, and its rank applied to the xref
    status = conn.query(
        "SELECT MediaStatus FROM MediaStatuses WHERE MediaStatusID = ?",
        (rendition["MediaStatusID"],),
    )[0]
    assert status["MediaStatus"] == "Publication Quality"

    xrefs = xrefs_for(conn, oid)
    assert len(xrefs) == 1
    assert xrefs[0]["MediaMasterID"] == master["MediaMasterID"]
    assert xrefs[0]["PrimaryDisplay"] == 1
    assert xrefs[0]["Rank"] == 1  # Publication Quality

    master_id = master["MediaMasterID"]
    rendition_id = rendition["RenditionID"]

    # ── phase 2: the same feed again changes nothing ─────────────────
    result = sync.process(feed(oid, "courier-test-1"))
    assert (result.inserted, result.updated, result.ignored, result.failed) == (0, 0, 1, 0)
    assert len(ours(conn, "MediaMaster")) == 1
    assert len(ours(conn, "MediaRenditions")) == 1
    assert len(ours(conn, "MediaFiles")) == 1
    assert len(xrefs_for(conn, oid)) == 1

    # ── phase 3: a newer updated_date applies an update ──────────────
    result = sync.process(feed(
        oid, "courier-test-1",
        UpdatedDate="2099-01-01T00:00:00",
        ApprovedForWebsite="false",
        IsPrimary="false",
        MediaStatus="Internal Use Only",
    ))
    assert (result.inserted, result.updated, result.ignored, result.failed) == (0, 1, 0, 0), (
        f"unexpected result, errors: {result.errors}"
    )

    # an update mutates in place; it must not fan out into new rows
    assert len(ours(conn, "MediaMaster")) == 1
    assert len(ours(conn, "MediaRenditions")) == 1
    assert len(xrefs_for(conn, oid)) == 1

    master = ours(conn, "MediaMaster")[0]
    assert master["MediaMasterID"] == master_id
    assert master["ApprovedForWeb"] == 0
    assert master["PublicCaption"] is None  # faithful: never assigned by the original

    rendition = ours(conn, "MediaRenditions")[0]
    new_status = conn.query(
        "SELECT MediaStatus FROM MediaStatuses WHERE MediaStatusID = ?",
        (rendition["MediaStatusID"],),
    )[0]["MediaStatus"]
    assert new_status == "Internal Use Only"
    assert xrefs_for(conn, oid)[0]["PrimaryDisplay"] == 0

    # ── phase 4: a deleted_date removes it, triggers and all ─────────
    result = sync.process(feed(oid, "courier-test-1", DeletedDate="2099-02-01T00:00:00"))
    assert (result.deleted, result.failed) == (1, 0), f"errors: {result.errors}"

    assert ours(conn, "MediaMaster") == []
    # TMS's own MediaDelete / MediaRenditionDelete triggers cascade —
    # courier deletes the master row and nothing else.
    assert count(conn, "SELECT COUNT(*) AS n FROM MediaRenditions WHERE MediaMasterID = ?",
                 (master_id,)) == 0
    assert count(conn, "SELECT COUNT(*) AS n FROM MediaFiles WHERE RenditionID = ?",
                 (rendition_id,)) == 0
    assert count(conn, "SELECT COUNT(*) AS n FROM MediaXrefs WHERE MediaMasterID = ?",
                 (master_id,)) == 0
    assert xrefs_for(conn, oid) == []

    # no orphans anywhere in our own rows
    counts = marked_row_counts(conn)
    assert counts["MediaMaster"] == 0
    assert counts["MediaRenditions"] == 0
    assert counts["MediaFiles"] == 0
    assert counts["MediaXrefs"] == 0

    # ── phase 5: replaying the delete is harmless ────────────────────
    result = sync.process(feed(oid, "courier-test-1", DeletedDate="2099-02-01T00:00:00"))
    assert result.failed == 0
    assert ours(conn, "MediaMaster") == []
    assert xrefs_for(conn, oid) == []


# ── individual behaviours ────────────────────────────────────────────


def test_a_dry_run_writes_nothing(sync, clean, canary_object_id):
    result = sync.process(feed(canary_object_id, "courier-test-dry"), dry_run=True)
    assert result.total == 1
    assert not {t: n for t, n in marked_row_counts(clean).items() if n}
    assert xrefs_for(clean, canary_object_id) == []


def test_an_unknown_asset_marked_deleted_is_ignored(sync, clean, canary_object_id):
    """Nothing was ever inserted for this pair, so there is nothing to
    delete — and a DELETE would have to guess at a MediaMasterID."""
    result = sync.process(
        feed(canary_object_id, "courier-test-ghost", DeletedDate="2026-01-02T00:00:00")
    )
    assert (result.ignored, result.deleted, result.failed) == (1, 0, 0)
    assert ours(clean, "MediaMaster") == []


def test_a_second_primary_asset_demotes_the_first(sync, clean, canary_object_id):
    oid = canary_object_id
    sync.process(feed(oid, "courier-test-a"))
    sync.process(feed(oid, "courier-test-b"))

    xrefs = xrefs_for(clean, oid)
    assert len(xrefs) == 2
    primaries = [x for x in xrefs if x["PrimaryDisplay"]]
    assert len(primaries) == 1, "an object must have exactly one primary image"
    assert primaries[0]["MediaXrefID"] == max(x["MediaXrefID"] for x in xrefs)


def test_deleting_the_primary_promotes_a_survivor(sync, clean, canary_object_id):
    oid = canary_object_id
    sync.process(feed(oid, "courier-test-a"))
    sync.process(feed(oid, "courier-test-b"))  # b is now primary

    result = sync.process(feed(oid, "courier-test-b", DeletedDate="2099-01-01T00:00:00"))
    assert result.deleted == 1

    xrefs = xrefs_for(clean, oid)
    assert len(xrefs) == 1
    assert xrefs[0]["PrimaryDisplay"] == 1, "the surviving image should become primary"


def test_a_failing_asset_rolls_back_whole_and_the_run_continues(sync, clean, canary_object_id):
    """Per-asset transactions: a failure half-way through an insert must
    leave nothing behind, and must not abort the rest of the feed.

    The over-long file name fails at the MediaFiles insert — after
    MediaMaster and MediaRenditions have already been written — so this
    exercises a genuine mid-transaction rollback.
    """
    oid = canary_object_id
    assets = [
        MediaAsset.from_json(asset_json("courier-test-bad", oid, Filename="x" * 500 + ".jpg")),
        MediaAsset.from_json(asset_json("courier-test-good", oid)),
    ]
    result = sync.process(assets)

    assert result.failed == 1
    assert result.inserted == 1, "a bad asset must not stop the feed"
    assert len(result.errors) == 1
    assert "courier-test-bad" in result.errors[0]

    # the failed asset left no partial rows
    masters = ours(clean, "MediaMaster")
    assert len(masters) == 1
    files = ours(clean, "MediaFiles")
    assert len(files) == 1
    assert files[0]["FileName"] == "courier-test-good.jpg"
    assert len(xrefs_for(clean, oid)) == 1

    # and the failure was recorded locally, not in the TMS database
    errors = sync.state.conn.execute(
        "SELECT asset_id, error_msg FROM error_log"
    ).fetchall()
    assert len(errors) == 1
    assert errors[0][0] == "courier-test-bad"


def test_every_row_written_carries_the_login_id(sync, clean, canary_object_id):
    """--login-id is how a museum tells courier's rows from its own,
    and how this suite cleans up after itself."""
    sync.process(feed(canary_object_id, "courier-test-1"))
    for table in ("MediaMaster", "MediaRenditions", "MediaFiles", "MediaXrefs",
                  "MediaPaths", "MediaStatuses"):
        rows = ours(clean, table)
        assert rows, f"{table} got no marked row"
        assert all(r["LoginID"] == TEST_LOGIN_ID for r in rows)


def test_department_handling_matches_this_databases_schema(sync, clean, canary_object_id):
    """TMS 9.1 has no MediaMaster.DepartmentID; later versions do. The
    sync detects which, and this asserts the detection agrees with
    INFORMATION_SCHEMA rather than with an assumption."""
    detected = sync._media_master_has_department()
    actual = bool(clean.query(
        "SELECT 1 AS x FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = 'MediaMaster' AND COLUMN_NAME = 'DepartmentID'"
    ))
    assert detected is actual

    sync.process(feed(canary_object_id, "courier-test-dept",
                      ImageCreatorsDepartment="Courier Test Department"))
    master = ours(clean, "MediaMaster")[0]
    if actual:
        department = clean.query(
            "SELECT Department FROM Departments WHERE DepartmentID = ?",
            (master["DepartmentID"],),
        )[0]["Department"]
        assert department == "Courier Test Department"
    else:
        assert "DepartmentID" not in master
        # nothing should have been created in Departments either
        assert ours(clean, "Departments") == []
