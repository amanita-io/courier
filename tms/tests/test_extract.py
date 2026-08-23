"""Object extraction and export against a real TMS database.

Skipped unless connection details are supplied — see conftest.

Extraction is read-only, so most of this reads whatever collection the
harness holds and asserts on shape and invariants. Nothing asserts on
a particular object's content: the tests must pass against any TMS
database, and no real collection's identifiers belong in this repo.

The one exception is the auxiliary-table coverage test, which seeds
marker rows of its own — those tables are commonly empty in a restored
backup, and the mapping code for them would otherwise never run.
"""

from __future__ import annotations

import csv
import json

import pytest

from courier_tms.canonical import normalize
from courier_tms.cli import main
from courier_tms.db import TMSConnection
from courier_tms.extract import BATCH_SIZE, TABLE_ID_OBJECTS, TMSExtractor

from conftest import TEST_LOGIN_ID, marked_row_counts

SCRATCH_THES_DB = "courierTestThes"


# ── no database needed ───────────────────────────────────────────────


def test_an_unsafe_thesaurus_database_name_is_refused():
    """The thesaurus name is interpolated into SQL — it cannot be
    parameterised — so it is validated instead."""
    extractor = TMSExtractor(
        TMSConnection(host="h", database="d", username="u", password="p"),
        thes_database="tms]; DROP DATABASE tms--",
    )
    with pytest.raises(ValueError, match="Unsafe thesaurus database name"):
        extractor.thes_database


# ── live ─────────────────────────────────────────────────────────────


@pytest.fixture
def extractor(tms_conn) -> TMSExtractor:
    return TMSExtractor(tms_conn)


def extract_one(conn, object_id: int, thes_database: str | None = None) -> dict:
    """Extract a single object through the real batch path."""
    extractor = TMSExtractor(conn, thes_database=thes_database)
    rows = conn.query("SELECT * FROM Objects WHERE ObjectID = ?", (object_id,))
    return list(extractor._extract_batch(rows, extractor.load_lookups()))[0]


@pytest.mark.live
def test_object_count_matches_the_table(tms_conn, extractor):
    expected = tms_conn.query(
        "SELECT COUNT(*) AS n FROM Objects WHERE ObjectID > 0"
    )[0]["n"]
    assert extractor.count_objects() == expected
    assert expected > 0, "the harness database has no objects to test against"


@pytest.mark.live
def test_lookups_load_and_are_cached(extractor):
    lookups = extractor.load_lookups()
    expected = {
        "classifications", "departments", "object_statuses", "roles", "title_types",
        "locations", "dimension_types", "dimension_units", "accession_methods",
        "constituents", "object_name_types",
    }
    assert expected <= set(lookups)
    assert extractor.load_lookups() is lookups, "lookups should load once per extractor"


@pytest.mark.live
def test_records_have_the_full_canonical_key_set(extractor):
    records = list(extractor.extract(limit=5))
    assert records

    nested = (
        "titles", "constituents", "dates", "alt_numbers", "components", "dimensions",
        "media", "exhibitions", "conditions", "insurance", "text_entries",
        "object_names", "vocabulary_terms", "user_fields", "geography", "references",
        "related_objects", "sites",
    )
    for record in records:
        assert record["ObjectID"] > 0
        for key in nested:
            assert isinstance(record[key], list), f"{key} should always be a list"
        for key in ("context", "accession"):
            assert record[key] is None or isinstance(record[key], dict)


@pytest.mark.live
def test_every_record_normalises_into_a_valid_envelope(extractor):
    for record in extractor.extract(limit=25):
        envelope = normalize(record)
        assert envelope["id"] == f"tms:{record['ObjectID']}"
        assert envelope["type"] == "Object"
        assert envelope["label"], "every object needs a human-readable label"
        assert envelope["object_number"]
        assert envelope["source"]["system"] == "tms"
        assert isinstance(envelope["properties"], dict)
        # the whole envelope has to survive a JSON round trip
        json.dumps(envelope, default=str)


@pytest.mark.live
def test_extraction_writes_nothing(tms_conn, extractor):
    """The extractor's guarantee, asserted rather than assumed. The
    session-wide pristine check covers the museum's own rows; this
    catches anything created under our own marker."""
    list(extractor.extract(limit=50))
    assert not {t: n for t, n in marked_row_counts(tms_conn).items() if n}


@pytest.mark.live
@pytest.mark.slow
def test_a_full_pass_covers_every_object_exactly_once(tms_conn, extractor):
    total = extractor.count_objects()
    ids = [r["ObjectID"] for r in extractor.extract()]

    assert len(ids) == total
    assert len(set(ids)) == total, "an object was emitted twice"
    assert ids == sorted(ids)

    expected = {
        r["ObjectID"]
        for r in tms_conn.query("SELECT ObjectID FROM Objects WHERE ObjectID > 0")
    }
    assert set(ids) == expected

    if total <= BATCH_SIZE:
        pytest.skip(
            f"collection of {total} fits in one batch of {BATCH_SIZE} — "
            "IN-clause batching not exercised"
        )


# ── the CLI, end to end ──────────────────────────────────────────────


def cli_args(tms_config: dict) -> list[str]:
    return [
        "--host", tms_config["host"],
        "--port", str(tms_config["port"]),
        "--database", tms_config["database"],
        "--username", tms_config["username"],
        "--password", tms_config["password"],
        "--driver", tms_config["driver"],
    ]


@pytest.mark.live
def test_cli_export_writes_one_json_object_per_line(tms_config, tmp_path):
    out = tmp_path / "collection.ndjson"
    assert main(["export", *cli_args(tms_config), "--limit", "10", "--out", str(out)]) == 0

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    for line in lines:
        envelope = json.loads(line)
        assert envelope["type"] == "Object"
        assert envelope["id"].startswith("tms:")


@pytest.mark.live
def test_cli_export_omits_the_raw_record_unless_asked(tms_config, tmp_path):
    lean = tmp_path / "lean.ndjson"
    full = tmp_path / "full.ndjson"
    main(["export", *cli_args(tms_config), "--limit", "5", "--out", str(lean)])
    main(["export", *cli_args(tms_config), "--limit", "5", "--raw", "--out", str(full)])

    assert "raw" not in json.loads(lean.read_text().splitlines()[0])
    assert "raw" in json.loads(full.read_text().splitlines()[0])
    assert full.stat().st_size > lean.stat().st_size


@pytest.mark.live
def test_cli_media_manifest_lists_the_files_an_export_references(tms_config, tmp_path):
    out = tmp_path / "collection.ndjson"
    manifest = tmp_path / "media.csv"
    main([
        "export", *cli_args(tms_config), "--limit", "200",
        "--out", str(out), "--media-manifest", str(manifest),
    ])

    if not manifest.exists():
        pytest.skip("no media with filenames in the first 200 objects")

    with manifest.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert set(rows[0]) == {
        "object_id", "object_number", "media_master_id", "file_name",
        "file_path", "pixel_w", "pixel_h", "file_size", "primary",
    }
    assert all(r["file_name"] for r in rows)

    exported = {json.loads(line)["source"]["id"] for line in out.read_text().splitlines()}
    assert {r["object_id"] for r in rows} <= exported


@pytest.mark.live
def test_cli_probe_reports_without_writing(tms_config, tms_conn, capsys):
    assert main(["probe", *cli_args(tms_config)]) == 0
    out = capsys.readouterr().out
    assert "Objects:" in out
    assert "Thesaurus database:" in out
    assert not {t: n for t, n in marked_row_counts(tms_conn).items() if n}


# ── auxiliary tables (usually empty in a restored backup) ────────────


@pytest.fixture
def seeded_object(clean, canary_object_id):
    """Seed one row into each auxiliary table for the canary object.

    Every row carries the suite's marker, so the standard teardown
    removes them. Returns (object_id, related_object_id).
    """
    conn = clean
    oid = canary_object_id
    related = conn.query(
        "SELECT TOP 1 ObjectID FROM Objects WHERE ObjectID > 0 AND ObjectID <> ? "
        "ORDER BY ObjectID", (oid,),
    )[0]["ObjectID"]

    name_type_id = conn.insert_identity(
        "INSERT INTO ObjectNameTypes (ObjectNameType, LoginID) VALUES (?, ?)",
        ("Courier Test Name Type", TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO ObjectNames (ObjectID, ObjectNameTypeID, ObjectName, Active, "
        "DisplayOrder, LoginID) VALUES (?, ?, ?, 1, 1, ?)",
        (oid, name_type_id, "Courier Test Object Name", TEST_LOGIN_ID),
    )

    xref_type_id = conn.insert_identity(
        "INSERT INTO ThesXrefTypes (ThesXrefType, TableID, ThesXrefTableID, LoginID) "
        "VALUES (?, ?, ?, ?)",
        ("Courier Test Subject", TABLE_ID_OBJECTS, TABLE_ID_OBJECTS, TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO ThesXrefs (ThesXrefTypeID, ID, TableID, TermID, ThesXrefTableID, "
        "DisplayOrder, Remarks, Active, LoginID) VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?)",
        (xref_type_id, oid, TABLE_ID_OBJECTS, 999001, TABLE_ID_OBJECTS,
         "courier test term", TEST_LOGIN_ID),
    )

    field_id = conn.insert_identity(
        "INSERT INTO UserFields (UserFieldName, LoginID) VALUES (?, ?)",
        ("Courier Test Field", TEST_LOGIN_ID),
    )
    group_id = conn.insert_identity(
        "INSERT INTO UserFieldGroups (GroupName, ContextID, LoginID) VALUES (?, ?, ?)",
        ("Courier Test Group", TABLE_ID_OBJECTS, TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO UserFieldXrefs (UserFieldID, ID, UserFieldGroupID, ContextID, "
        "FieldValue, ValueRemarks, LoginID) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (field_id, oid, group_id, TABLE_ID_OBJECTS, "courier test value",
         "courier test remarks", TEST_LOGIN_ID),
    )

    geo_code_id = conn.insert_identity(
        "INSERT INTO GeoCodes (GeoCode, LoginID) VALUES (?, ?)", ("CT", TEST_LOGIN_ID)
    )
    conn.execute(
        "INSERT INTO ObjGeography (ObjectID, GeoCodeID, Country, City, Notes, LoginID) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (oid, geo_code_id, "Courier Test Country", "Courier Test City",
         "courier test note", TEST_LOGIN_ID),
    )

    reference_id = conn.insert_identity(
        "INSERT INTO ReferenceMaster (FormatID, Heading, Title, BoilerText, "
        "YearPublished, LoginID) VALUES (0, ?, ?, ?, ?, ?)",
        ("Courier Test Heading", "Courier Test Title", "Courier test citation.",
         "2026", TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO RefXRefs (ID, TableID, ReferenceID, PageNumber, CatalogueNumber, "
        "FigureNumber, Remarks, LoginID) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, TABLE_ID_OBJECTS, reference_id, "p. 7", "cat. 3", "fig. 2",
         "courier test ref", TEST_LOGIN_ID),
    )

    relationship_id = conn.insert_identity(
        "INSERT INTO Relationships (Relation1, Relation2, TableID, LoginID) "
        "VALUES (?, ?, ?, ?)",
        ("is courier-test parent of", "is courier-test child of", TABLE_ID_OBJECTS,
         TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO Associations (ID1, ID2, TableID, RelationshipID, Remarks, "
        "DisplayDate, LoginID) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (oid, related, TABLE_ID_OBJECTS, relationship_id, "courier test assoc",
         "2026", TEST_LOGIN_ID),
    )

    site_id = conn.insert_identity(
        "INSERT INTO Sites (SiteName, SiteNumber, LoginID) VALUES (?, ?, ?)",
        ("Courier Test Site", "CTS-1", TEST_LOGIN_ID),
    )
    conn.execute(
        "INSERT INTO SiteObjXrefs (SiteID, ObjectID, Subsite1, Subsite2, Remarks, LoginID) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (site_id, oid, "Trench A", "Layer 2", "courier test site", TEST_LOGIN_ID),
    )

    conn.execute(
        "INSERT INTO TextEntries (TableID, ID, TextTypeID, TextEntry, LoginID) "
        "VALUES (?, ?, ?, ?, ?)",
        (TABLE_ID_OBJECTS, oid, 0, "Courier test text entry.", TEST_LOGIN_ID),
    )
    conn.commit()
    return oid, related


@pytest.mark.live
def test_auxiliary_tables_are_mapped(clean, seeded_object):
    """Thesaurus terms, flex fields, geography, references, relationships,
    object names and sites — the coverage added for real-world TMS
    databases, which a restored backup usually leaves empty."""
    oid, related = seeded_object
    record = extract_one(clean, oid)

    assert record["object_names"][0]["name"] == "Courier Test Object Name"
    assert record["object_names"][0]["name_type"] == "Courier Test Name Type"

    term = record["vocabulary_terms"][0]
    assert term["term_id"] == 999001
    assert term["xref_type"] == "Courier Test Subject"
    assert term["remarks"] == "courier test term"

    field = record["user_fields"][0]
    assert (field["field"], field["group"], field["value"]) == (
        "Courier Test Field", "Courier Test Group", "courier test value"
    )

    geography = record["geography"][0]
    assert geography["geocode"] == "CT"
    assert geography["country"] == "Courier Test Country"
    assert geography["city"] == "Courier Test City"

    reference = record["references"][0]
    assert reference["heading"] == "Courier Test Heading"
    assert reference["citation"] == "Courier test citation."
    assert reference["page_number"] == "p. 7"

    relation = record["related_objects"][0]
    assert relation["related_object_id"] == related
    assert relation["relation"] == "is courier-test parent of"

    site = record["sites"][0]
    assert site["site_name"] == "Courier Test Site"
    assert site["subsites"] == ["Trench A", "Layer 2"]

    assert record["text_entries"][0]["text"] == "Courier test text entry."


@pytest.mark.live
def test_associations_are_read_from_both_directions(clean, seeded_object):
    """The association was written with the canary as ID1; the related
    object must still see it, with the reverse relation label."""
    oid, related = seeded_object
    record = extract_one(clean, related)

    match = [r for r in record["related_objects"] if r["related_object_id"] == oid]
    assert match, "the reverse side of the association was not extracted"
    assert match[0]["relation"] == "is courier-test child of"


@pytest.mark.live
def test_terms_export_as_ids_when_no_thesaurus_database_is_present(clean, seeded_object):
    oid, _ = seeded_object
    record = extract_one(clean, oid, thes_database=None)
    term = record["vocabulary_terms"][0]

    if TMSExtractor(clean).thes_database:
        pytest.skip("this server has a thesaurus database; term labels resolve")
    assert term["term_id"] == 999001, "the TermID is exported even with no label"
    assert term["term"] is None


# ── cross-database thesaurus join ────────────────────────────────────


@pytest.fixture
def scratch_thesaurus(tms_config):
    """A throwaway thesaurus database, so the cross-database join can be
    exercised on a harness that has none.

    Nothing is created inside the TMS database itself. CREATE/DROP
    DATABASE cannot run inside a transaction, hence autocommit.
    """
    admin = TMSConnection(**tms_config)
    admin.connect()
    admin._conn.autocommit = True
    try:
        admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_THES_DB}")
        admin.execute(f"CREATE DATABASE {SCRATCH_THES_DB}")
        admin.execute(
            f"CREATE TABLE {SCRATCH_THES_DB}.dbo.Terms "
            "(TermID int NOT NULL, TermMasterID int NULL, Term varchar(255) NULL)"
        )
        admin.execute(
            f"INSERT INTO {SCRATCH_THES_DB}.dbo.Terms (TermID, TermMasterID, Term) "
            "VALUES (999001, 42, 'Courier Test Term Label')"
        )
        yield SCRATCH_THES_DB
    finally:
        try:
            admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_THES_DB}")
        except Exception:
            # A session that read across into it still counts as using
            # it. Only thes_conn ever does, and it closes first, so this
            # is a backstop rather than the normal path.
            admin.execute(
                f"ALTER DATABASE {SCRATCH_THES_DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
            )
            admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_THES_DB}")
        finally:
            admin.close()


@pytest.fixture
def thes_conn(scratch_thesaurus, tms_config):
    """A connection dedicated to the cross-database tests.

    Reading across into the scratch database pins it open for the
    session that did the reading, so that must not be the shared
    session connection — it would still be pinned when the fixture
    tries to drop the database. Depending on scratch_thesaurus puts
    this connection's close ahead of the drop.
    """
    conn = TMSConnection(**tms_config)
    conn.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.live
@pytest.mark.creates_database
def test_term_labels_resolve_across_databases(seeded_object, thes_conn, scratch_thesaurus):
    oid, _ = seeded_object
    record = extract_one(thes_conn, oid, thes_database=scratch_thesaurus)

    term = record["vocabulary_terms"][0]
    assert term["term"] == "Courier Test Term Label"
    assert term["term_master_id"] == 42
    assert term["term_id"] == 999001


@pytest.mark.live
@pytest.mark.creates_database
def test_the_thesaurus_database_is_found_by_name(thes_conn, scratch_thesaurus):
    """TMS names it *TMSThes by convention; courier looks for anything
    matching %thes% rather than requiring the operator to know."""
    assert TMSExtractor(thes_conn).thes_database == scratch_thesaurus
