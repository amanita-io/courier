"""Lookup resolution and rendition numbering. No database.

Ports of get_media_path, get_media_department, get_media_status,
get_media_type_format and get_rendition_number. The auto-create
behaviour matters: these are the only rows courier ever adds to a TMS
lookup table, and it adds them exactly where the original did.
"""

from __future__ import annotations

from datetime import date

import pytest

from courier_tms.media.lookups import MAIN_TABLE_ID_MEDIA, MediaLookups

LOGIN = "courier-test"


class StubConn:
    """Answers queries from canned rows keyed by an SQL substring."""

    def __init__(self, results: dict | None = None, identity: int = 1000):
        self.results = results or {}
        self.queries: list[tuple] = []
        self.inserts: list[tuple] = []
        self._identity = identity

    def query(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        for needle, rows in self.results.items():
            if needle in sql:
                return rows
        return []

    def insert_identity(self, sql, params=()):
        self.inserts.append((sql, tuple(params)))
        self._identity += 1
        return self._identity


def lookups(**kwargs) -> tuple[MediaLookups, StubConn]:
    conn = StubConn(**kwargs)
    return MediaLookups(conn, LOGIN), conn


# ── the 5th-slash path rule ──────────────────────────────────────────


@pytest.mark.parametrize("url,expected", [
    ("https://dam.example.org/courier-test/assets/x.jpg",
     "https://dam.example.org/courier-test/assets/"),
    ("https://host/a/b/file.jpg", "https://host/a/b/"),
    ("https://host/a/b/c/d/file.jpg", "https://host/a/b/"),
])
def test_path_is_everything_up_to_the_fifth_slash(url, expected):
    assert MediaLookups.extract_path(url) == expected


@pytest.mark.parametrize("url", [
    "https://host/only/four.jpg",
    "https://host/",
    "no-slashes-at-all",
    "",
])
def test_a_url_without_a_fifth_slash_yields_an_empty_path(url):
    """CHARINDEX returns 0 when the slash is absent and SUBSTRING(x,1,0)
    is ''. Not None — the empty string is a real value here, and it is
    what the original wrote into MediaPaths."""
    assert MediaLookups.extract_path(url) == ""


def test_a_null_url_yields_no_path():
    assert MediaLookups.extract_path(None) is None


# ── MediaPaths ───────────────────────────────────────────────────────


def test_existing_path_is_reused_not_duplicated():
    lk, conn = lookups(results={"FROM MediaPaths": [{"PathID": 5}]})
    assert lk.path_id("https://host/a/b/file.jpg") == 5
    assert conn.inserts == []


def test_unknown_path_is_created():
    lk, conn = lookups()
    path_id = lk.path_id("https://host/a/b/file.jpg")
    assert path_id == 1001
    sql, params = conn.inserts[0]
    assert "INSERT INTO MediaPaths" in sql
    # Path and PhysicalPath both get the URL prefix, as in the original.
    assert params == ("https://host/a/b/", "https://host/a/b/", LOGIN)


def test_a_null_url_never_reaches_the_database():
    lk, conn = lookups()
    assert lk.path_id(None) is None
    assert conn.queries == [] and conn.inserts == []


# ── Departments (media departments live at MainTableID 318) ──────────


def test_existing_media_department_is_reused():
    lk, conn = lookups(results={"FROM Departments": [{"DepartmentID": 4}]})
    assert lk.department_id("Photography") == 4
    assert conn.inserts == []
    _, params = conn.queries[0]
    assert params == ("Photography", MAIN_TABLE_ID_MEDIA)


def test_unknown_department_is_created_under_the_media_table_id():
    lk, conn = lookups()
    assert lk.department_id("Photography") == 1001
    sql, params = conn.inserts[0]
    assert "INSERT INTO Departments" in sql
    assert params == ("Photography", MAIN_TABLE_ID_MEDIA, LOGIN)


def test_a_missing_department_looks_up_not_assigned():
    lk, conn = lookups(results={"FROM Departments": [{"DepartmentID": 0}]})
    assert lk.department_id(None) == 0
    _, params = conn.queries[0]
    assert params == ("(not assigned)", MAIN_TABLE_ID_MEDIA)


def test_not_assigned_is_never_invented():
    """If this TMS has no '(not assigned)' media department, courier
    leaves the column NULL rather than creating a vocabulary row the
    museum did not ask for."""
    lk, conn = lookups()
    assert lk.department_id(None) is None
    assert conn.inserts == []


# ── MediaStatuses ────────────────────────────────────────────────────


def test_existing_status_is_reused():
    lk, conn = lookups(results={"FROM MediaStatuses": [{"MediaStatusID": 2}]})
    assert lk.status_id("Publication Quality") == 2
    assert conn.inserts == []


def test_unknown_status_is_created():
    lk, conn = lookups()
    assert lk.status_id("Publication Quality") == 1001
    sql, params = conn.inserts[0]
    assert "INSERT INTO MediaStatuses" in sql
    assert params == ("Publication Quality", LOGIN)


def test_a_missing_status_is_never_invented():
    lk, conn = lookups()
    assert lk.status_id(None) is None
    assert conn.inserts == []


# ── MediaTypes / MediaFormats (resolve only) ─────────────────────────


def test_known_type_and_format_resolve():
    lk, _ = lookups(results={
        "FROM MediaTypes": [{"MediaTypeID": 1}],
        "FROM MediaFormats": [{"FormatID": 2}],
    })
    assert lk.type_and_format("Image", "JPEG") == (1, 2)


def test_tiffdocument_is_normalised_to_tiff():
    lk, conn = lookups(results={
        "FROM MediaTypes": [{"MediaTypeID": 1}],
        "FROM MediaFormats": [{"FormatID": 1}],
    })
    lk.type_and_format("Image", "TIFFDocument")
    format_query = [q for q in conn.queries if "FROM MediaFormats" in q[0]][0]
    assert format_query[1] == ("TIFF",)


def test_unknown_type_and_format_fall_back_to_zero():
    """These two are resolve-only: the original never created a
    MediaType or MediaFormat, so neither does courier."""
    lk, conn = lookups()
    assert lk.type_and_format("Hologram", "HOLO") == (0, 0)
    assert conn.inserts == []


def test_format_is_not_looked_up_when_the_type_is_unknown():
    """A quirk of get_media_type_format: the format lookup is nested
    inside the type match, so an unrecognised type suppresses it."""
    lk, conn = lookups(results={"FROM MediaFormats": [{"FormatID": 2}]})
    assert lk.type_and_format("Hologram", "JPEG") == (0, 0)
    assert not [q for q in conn.queries if "FROM MediaFormats" in q[0]]


# ── rendition numbering ──────────────────────────────────────────────


def rendition_rows(*numbers):
    return {"FROM MediaRenditions": [{"RenditionNumber": n} for n in numbers]}


def test_first_rendition_of_the_year_is_number_one():
    lk, _ = lookups()
    number, sort = lk.next_rendition_number()
    year = date.today().year
    assert number == f"R.{year}-1"
    assert sort == "R".ljust(8) + str(year) + "1".rjust(6)


def test_sequence_continues_from_the_highest_existing_number():
    year = date.today().year
    lk, _ = lookups(results=rendition_rows(f"R.{year}-1", f"R.{year}-11", f"R.{year}-2"))
    assert lk.next_rendition_number()[0] == f"R.{year}-12"


def test_other_years_do_not_advance_the_sequence():
    year = date.today().year
    lk, _ = lookups(results=rendition_rows(f"R.{year - 1}-99", f"R.{year}-3"))
    assert lk.next_rendition_number()[0] == f"R.{year}-4"


def test_the_year_filter_reads_characters_three_to_six_not_the_prefix():
    """get_rendition_number.sql checked SUBSTRING(n,3,4) = the year and
    never looked at the 'R.' — so a differently prefixed number from
    the same year still counts."""
    year = date.today().year
    lk, _ = lookups(results=rendition_rows(f"X.{year}-7"))
    assert lk.next_rendition_number()[0] == f"R.{year}-8"


def test_non_numeric_sequences_are_skipped():
    year = date.today().year
    lk, _ = lookups(results=rendition_rows(f"R.{year}-draft", f"R.{year}-2"))
    assert lk.next_rendition_number()[0] == f"R.{year}-3"


def test_only_this_tools_renditions_are_scanned():
    """Scoped by LoginID so courier never renumbers around — or
    collides with — renditions the museum's own staff created."""
    lk, conn = lookups()
    lk.next_rendition_number()
    _, params = conn.queries[0]
    assert params == (LOGIN,)


def test_sort_number_is_fixed_width_for_lexical_ordering():
    year = date.today().year
    lk, _ = lookups(results=rendition_rows(f"R.{year}-9"))
    _, sort = lk.next_rendition_number()
    assert len(sort) == 18
    assert sort == "R       " + str(year) + "    10"
