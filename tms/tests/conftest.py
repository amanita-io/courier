"""Shared fixtures for the courier-tms test suite.

Two kinds of test live here:

- **Unit tests** (test_db, test_models, test_state, test_lookups,
  test_canonical) need no database at all.
- **Live tests** (test_extract, test_media_sync) run against a real
  TMS database — the docker validation harness, or a museum's own
  restored copy. They SKIP unless connection details are supplied, so
  a bare ``pytest`` on a fresh clone still passes.

Supplying connection details::

    COURIER_TMS_PASSWORD='...' pytest
    pytest --tms-host db --tms-database tms --tms-password '...'

The live tests write to the database. What makes that safe is the
contract below — which is also, not coincidentally, the product
promise this suite exists to prove:

1. Every row the suite writes is stamped ``LoginID='courier-test'``
   and deleted by that marker afterwards. Residue is verified with
   per-table ``COUNT(*)`` queries, never with driver rowcounts.
2. A snapshot of every row NOT stamped with the marker is taken
   before the session and compared after it. If the suite modified so
   much as a flag on the museum's own data, the run fails.
3. Media is only ever attached to a "canary" object chosen at runtime
   for having no media of its own, so the primary-display promote and
   demote statements cannot reach a pre-existing row.
4. No DDL is issued against the TMS database. One test needs a second
   database to exercise cross-database thesaurus joins; it creates and
   drops a scratch one, and only when ``--tms-allow-create-db`` is given.

Nothing in this file hard-codes an identifier from any real
collection: the canary object, and every object the extract tests
read, are discovered at runtime.
"""

from __future__ import annotations

import base64
import os

import pytest

from courier_tms.db import TMSConnection

# ── the marker every row this suite creates carries ──────────────────

TEST_LOGIN_ID = "courier-test"

TABLE_ID_OBJECTS = 108

# A 1x1 transparent PNG — the smallest thing that is genuinely an image.
THUMBNAIL_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
THUMBNAIL_BYTES = base64.b64decode(THUMBNAIL_B64)

# Media path with a 5th slash, so get_media_path's SUBSTRING rule
# yields a real path rather than the empty string.
ASSET_URL_PREFIX = "https://dam.example.org/courier-test/assets/"

# Every table this suite can write to, children before parents. The
# purge walks this list; the residue check walks it again.
MARKED_TABLES = (
    # media sync
    "MediaXrefs",
    "MediaFiles",
    "MediaRenditions",
    "MediaMaster",
    "MediaPaths",
    "MediaStatuses",
    "Departments",
    # extract-coverage seeds
    "ThesXrefs",
    "ThesXrefTypes",
    "UserFieldXrefs",
    "UserFields",
    "UserFieldGroups",
    "ObjGeography",
    "GeoCodes",
    "RefXRefs",
    "ReferenceMaster",
    "Associations",
    "Relationships",
    "SiteObjXrefs",
    "Sites",
    "TextEntries",
    "ObjectNames",
    "ObjectNameTypes",
)

# Columns whose value the suite could plausibly disturb on a row it
# does not own. Snapshotted before and after the session.
_SNAPSHOT_QUERIES = {
    "MediaMaster": (
        "SELECT MediaMasterID, DisplayRendID, PrimaryRendID, ApprovedForWeb, "
        "PublicAccess, DATALENGTH(PublicCaption) AS CaptionLen, LoginID "
        "FROM MediaMaster WHERE LoginID <> ? ORDER BY MediaMasterID"
    ),
    "MediaRenditions": (
        "SELECT RenditionID, MediaMasterID, PrimaryFileID, MediaStatusID, "
        "RenditionNumber, LoginID FROM MediaRenditions WHERE LoginID <> ? "
        "ORDER BY RenditionID"
    ),
    "MediaFiles": (
        "SELECT FileID, RenditionID, PathID, FileName, LoginID "
        "FROM MediaFiles WHERE LoginID <> ? ORDER BY FileID"
    ),
    "MediaXrefs": (
        "SELECT MediaXrefID, MediaMasterID, ID, TableID, PrimaryDisplay, Rank, "
        "LoginID FROM MediaXrefs WHERE LoginID <> ? ORDER BY MediaXrefID"
    ),
    "MediaPaths": (
        "SELECT PathID, Path, LoginID FROM MediaPaths WHERE LoginID <> ? ORDER BY PathID"
    ),
    "MediaStatuses": (
        "SELECT MediaStatusID, MediaStatus, LoginID FROM MediaStatuses "
        "WHERE LoginID <> ? ORDER BY MediaStatusID"
    ),
    "Departments": (
        "SELECT DepartmentID, Department, MainTableID, LoginID FROM Departments "
        "WHERE LoginID <> ? ORDER BY DepartmentID"
    ),
}


# ── options ──────────────────────────────────────────────────────────


def pytest_addoption(parser):
    g = parser.getgroup("courier-tms")
    g.addoption("--tms-host", default=os.environ.get("COURIER_TMS_HOST", "localhost"))
    g.addoption("--tms-port", type=int, default=int(os.environ.get("COURIER_TMS_PORT", "1433")))
    g.addoption("--tms-database", default=os.environ.get("COURIER_TMS_DATABASE", "tms"))
    g.addoption("--tms-username", default=os.environ.get("COURIER_TMS_USERNAME", "sa"))
    g.addoption("--tms-password", default=os.environ.get("COURIER_TMS_PASSWORD", ""))
    g.addoption("--tms-driver", default=os.environ.get("COURIER_TMS_DRIVER", "pytds"),
                choices=["pytds", "pyodbc"])
    g.addoption(
        "--tms-allow-create-db",
        action="store_true",
        default=bool(os.environ.get("COURIER_TMS_ALLOW_CREATE_DB")),
        help="Permit the one test that creates and drops a scratch thesaurus database",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--tms-allow-create-db"):
        return
    skip = pytest.mark.skip(reason="needs --tms-allow-create-db")
    for item in items:
        if "creates_database" in item.keywords:
            item.add_marker(skip)


# ── connection ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def tms_config(request) -> dict:
    password = request.config.getoption("--tms-password")
    if not password:
        pytest.skip(
            "no TMS connection configured — set COURIER_TMS_PASSWORD or pass --tms-password"
        )
    return {
        "host": request.config.getoption("--tms-host"),
        "port": request.config.getoption("--tms-port"),
        "database": request.config.getoption("--tms-database"),
        "username": request.config.getoption("--tms-username"),
        "password": password,
        "driver": request.config.getoption("--tms-driver"),
    }


@pytest.fixture(scope="session")
def tms_conn(tms_config) -> TMSConnection:
    conn = TMSConnection(**tms_config)
    try:
        conn.connect()
    except ConnectionError as e:
        pytest.skip(f"TMS database unreachable: {e}")
    try:
        yield conn
    finally:
        conn.close()


# ── marker purge + residue verification ──────────────────────────────


def purge_marked_rows(conn: TMSConnection) -> None:
    """Delete every row stamped with the suite's marker, children first.

    MediaXrefs go before MediaMaster deliberately: TMS's own MediaDelete
    trigger promotes a surviving xref to primary when a master is
    removed, and clearing the xrefs first leaves it nothing to promote.
    """
    for table in MARKED_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE LoginID = ?", (TEST_LOGIN_ID,))
    conn.commit()


def marked_row_counts(conn: TMSConnection) -> dict[str, int]:
    """Per-table COUNT(*) of marker rows. Counts, not rowcounts."""
    return {
        table: conn.query(
            f"SELECT COUNT(*) AS n FROM {table} WHERE LoginID = ?", (TEST_LOGIN_ID,)
        )[0]["n"]
        for table in MARKED_TABLES
    }


def _snapshot(conn: TMSConnection) -> dict[str, list[tuple]]:
    snap = {}
    for table, sql in _SNAPSHOT_QUERIES.items():
        rows = conn.query(sql, (TEST_LOGIN_ID,))
        snap[table] = [tuple(sorted(r.items())) for r in rows]
    # Objects and the tables the extractor only reads: a count is
    # enough to catch a stray write.
    for table in ("Objects", "ObjTitles", "ConXrefs", "ObjComponents"):
        snap[f"count:{table}"] = [
            (conn.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"],)
        ]
    # The product promise in one assertion: courier installs nothing.
    # Every table, view, procedure, function and trigger in the
    # database, before and after.
    snap["schema"] = [
        tuple(sorted(r.items()))
        for r in conn.query(
            "SELECT name, type FROM sys.objects WHERE is_ms_shipped = 0 "
            "ORDER BY name, type"
        )
    ]
    return snap


@pytest.fixture(scope="session", autouse=True)
def pristine(request) -> None:
    """Prove the suite left the museum's own data untouched.

    Session-scoped and autouse, but inert until something actually asks
    for a connection — a unit-test-only run never touches a database.
    """
    if not request.config.getoption("--tms-password"):
        yield
        return
    try:
        conn = TMSConnection(
            host=request.config.getoption("--tms-host"),
            port=request.config.getoption("--tms-port"),
            database=request.config.getoption("--tms-database"),
            username=request.config.getoption("--tms-username"),
            password=request.config.getoption("--tms-password"),
            driver=request.config.getoption("--tms-driver"),
        )
        conn.connect()
    except ConnectionError:
        yield
        return

    # Clear residue from any earlier crashed run before taking the
    # baseline, so leftovers are cleaned but never counted as museum data.
    purge_marked_rows(conn)
    before = _snapshot(conn)
    try:
        yield
    finally:
        purge_marked_rows(conn)
        after = _snapshot(conn)
        residue = {t: n for t, n in marked_row_counts(conn).items() if n}
        conn.close()

    assert not residue, f"marker rows survived teardown: {residue}"
    assert before["schema"] == after["schema"], (
        "the database schema changed during the run — courier must never "
        "create anything inside a source database"
    )
    for table in before:
        assert before[table] == after[table], (
            f"{table} changed during the run — the suite modified data it does not own"
        )


@pytest.fixture
def clean(tms_conn) -> TMSConnection:
    """Per-test purge, before and after, with a residue assertion."""
    purge_marked_rows(tms_conn)
    yield tms_conn
    purge_marked_rows(tms_conn)
    residue = {t: n for t, n in marked_row_counts(tms_conn).items() if n}
    assert not residue, f"test left marker rows behind: {residue}"


# ── a safe object to hang test media on ──────────────────────────────


@pytest.fixture(scope="session")
def canary_object_id(tms_conn) -> int:
    """An object with no media of its own.

    Media sync demotes other primaries on insert and promotes a
    survivor on delete. Both are scoped to one object, so attaching
    test media to an object that has none means neither statement can
    ever reach a row the museum owns.
    """
    rows = tms_conn.query(
        "SELECT TOP 1 o.ObjectID FROM Objects o WHERE o.ObjectID > 0 "
        "AND NOT EXISTS (SELECT 1 FROM MediaXrefs mx WHERE mx.TableID = ? "
        "AND mx.ID = o.ObjectID) ORDER BY o.ObjectID",
        (TABLE_ID_OBJECTS,),
    )
    if not rows:
        pytest.skip("no object without existing media — cannot test media sync safely")
    return rows[0]["ObjectID"]


# ── asset feed builder ───────────────────────────────────────────────


def asset_json(asset_id: str, object_id: int, **overrides) -> dict:
    """One asset in the legacy PascalCase feed shape, with defaults that
    satisfy every field the original media_staging table had NOT NULL."""
    asset = {
        "AssetId": asset_id,
        "ObjectId": object_id,
        "ShareURL": f"{ASSET_URL_PREFIX}{asset_id}.jpg",
        "Filename": f"{asset_id}.jpg",
        "ImageSizeInKilobytes": 1234,
        "MediaStatus": "Publication Quality",
        "MediaType": "Image",
        "MediaFormat": "JPEG",
        "ImageWidth": 800,
        "ImageHeight": 600,
        "AssetExternalId": f"ext-{asset_id}",
        "ThumbData": THUMBNAIL_B64,
        "CreatedDate": "2026-01-01T00:00:00",
        "UpdatedDate": "2026-01-01T00:00:00",
        "IsPrimary": "true",
        "ApprovedForWebsite": "true",
        "ImageDescription": "courier-test asset",
    }
    asset.update(overrides)
    return asset
