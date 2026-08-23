"""Driver-layer tests. No database.

The two things that make pytds work — %-escaping and Binary-wrapping —
are invisible until they fail in production against a museum's server,
which is exactly why they are pinned here.
"""

from __future__ import annotations

import pytest

from courier_tms.db import TMSConnection
from courier_tms.extract import BATCH_SIZE


# ── fake driver ──────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = [("ObjectID",), ("Value",)]
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.owner.calls.append((sql, tuple(params)))

    def fetchall(self):
        return self.owner.rows

    def fetchone(self):
        return self.owner.rows[0] if self.owner.rows else None


class FakeDriverConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.calls.append(("COMMIT", ()))

    def rollback(self):
        self.calls.append(("ROLLBACK", ()))

    def close(self):
        pass


class FakeBinary:
    """Stand-in for pytds.Binary."""

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, FakeBinary) and self.value == other.value


def make_conn(rows=(), paramstyle="?", binary_wrap=None) -> TMSConnection:
    conn = TMSConnection(host="h", database="d", username="u", password="p")
    conn._conn = FakeDriverConn(rows)
    conn._paramstyle = paramstyle
    conn._binary_wrap = binary_wrap
    return conn


# ── pytds SQL translation ────────────────────────────────────────────


def test_pytds_sql_turns_qmarks_into_percent_s():
    assert TMSConnection._pytds_sql("SELECT * FROM T WHERE a = ? AND b = ?") == (
        "SELECT * FROM T WHERE a = %s AND b = %s"
    )


def test_pytds_sql_escapes_literal_percent_before_substituting():
    """A LIKE pattern must survive param substitution.

    Escaping has to happen first: doing it the other way round would
    turn the %s placeholders into %%s and break every query.
    """
    sql = "SELECT name FROM sys.databases WHERE LOWER(name) LIKE '%thes%'"
    assert TMSConnection._pytds_sql(sql) == (
        "SELECT name FROM sys.databases WHERE LOWER(name) LIKE '%%thes%%'"
    )


def test_pytds_sql_handles_percent_and_placeholder_together():
    assert TMSConnection._pytds_sql("WHERE a LIKE '%x%' AND b = ?") == (
        "WHERE a LIKE '%%x%%' AND b = %s"
    )


def test_query_leaves_sql_alone_under_pyodbc():
    conn = make_conn(rows=[(1, "a")])
    conn.query("SELECT * FROM T WHERE a = ? AND b LIKE '%x%'", (1,))
    sql, _ = conn._conn.calls[0]
    assert "?" in sql and "%%" not in sql


# ── binary parameter wrapping ────────────────────────────────────────


def test_prep_wraps_bytes_for_pytds():
    conn = make_conn(binary_wrap=FakeBinary)
    prepped = conn._prep([b"\x89PNG", "text", 42, bytearray(b"\x00")])
    assert prepped[0] == FakeBinary(b"\x89PNG")
    assert prepped[1] == "text"
    assert prepped[2] == 42
    assert prepped[3] == FakeBinary(bytearray(b"\x00"))


def test_prep_passes_bytes_through_for_pyodbc():
    conn = make_conn()
    assert conn._prep([b"\x89PNG"]) == (b"\x89PNG",)


def test_execute_wraps_binary_params():
    conn = make_conn(binary_wrap=FakeBinary)
    conn.execute("UPDATE T SET blob = ?", (b"data",))
    _, params = conn._conn.calls[0]
    assert params == (FakeBinary(b"data"),)


# ── identity retrieval ───────────────────────────────────────────────


def test_insert_identity_runs_insert_and_scope_identity_in_one_batch():
    """SCOPE_IDENTITY() is scope-bound: split across two round trips it
    would return NULL (or worse, another statement's identity)."""
    conn = make_conn(rows=[(77,)])
    got = conn.insert_identity("INSERT INTO T (a) VALUES (?)", (1,))
    assert got == 77
    sql, _ = conn._conn.calls[0]
    assert sql.count(";") >= 2
    assert "SET NOCOUNT ON" in sql
    assert "SCOPE_IDENTITY()" in sql
    assert sql.index("INSERT INTO T") < sql.index("SCOPE_IDENTITY()")


def test_insert_identity_translates_for_pytds():
    conn = make_conn(rows=[(5,)], paramstyle="%s")
    conn.insert_identity("INSERT INTO T (a) VALUES (?)", (1,))
    sql, _ = conn._conn.calls[0]
    assert "%s" in sql and "?" not in sql


def test_insert_identity_raises_when_no_identity_comes_back():
    conn = make_conn(rows=[(None,)])
    with pytest.raises(RuntimeError, match="identity"):
        conn.insert_identity("INSERT INTO T (a) VALUES (?)", (1,))


# ── IN-clause construction and grouping ──────────────────────────────


def test_query_grouped_expands_placeholders_one_per_id():
    conn = make_conn(rows=[(1, "a"), (1, "b"), (2, "c")])
    grouped = conn.query_grouped("SELECT * FROM T WHERE ObjectID IN ({IDS})", [1, 2])
    sql, params = conn._conn.calls[0]
    assert "IN (?,?)" in sql
    assert params == (1, 2)
    assert set(grouped) == {1, 2}
    assert len(grouped[1]) == 2 and len(grouped[2]) == 1


def test_query_grouped_short_circuits_on_empty_ids():
    conn = make_conn()
    assert conn.query_grouped("SELECT * FROM T WHERE ObjectID IN ({IDS})", []) == {}
    assert conn._conn.calls == []


def test_query_grouped_drops_rows_with_a_null_group_key():
    conn = make_conn(rows=[(None, "orphan"), (3, "kept")])
    grouped = conn.query_grouped("SELECT * FROM T WHERE ObjectID IN ({IDS})", [3])
    assert set(grouped) == {3}


def test_batch_size_keeps_in_clauses_under_the_sql_server_parameter_limit():
    """SQL Server allows ~2100 parameters per statement.

    This is the constant the Madrona connector is missing; a collection
    of more than 2100 objects blows the limit without it.
    """
    assert BATCH_SIZE <= 2000
