"""Read-only SQL Server access layer with pluggable drivers.

Two supported drivers, tried in order unless one is forced:

- ``pyodbc``  — production-grade, requires the Microsoft ODBC Driver
  for SQL Server to be installed on the machine.
- ``pytds``   — pure Python (pip install python-tds), no system
  drivers needed. Slower, but runs anywhere, which matters for
  museum IT environments where installing system packages is hard.

Both are wrapped behind one tiny interface: ``query(sql, params)``
returning a list of dicts. All queries issued by this package are
SELECTs; the connection is never used to write.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)


class TMSConnection:
    """A read-only connection to a TMS SQL Server database."""

    def __init__(
        self,
        host: str,
        database: str,
        username: str,
        password: str,
        port: int = 1433,
        driver: str | None = None,
        odbc_driver: str = "ODBC Driver 18 for SQL Server",
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.odbc_driver = odbc_driver
        self._conn: Any = None
        self._paramstyle = "?"
        self._driver = driver  # None = auto: pyodbc, then pytds
        self._binary_wrap = None  # set by pytds: bytes params need pytds.Binary

    # ── connection ───────────────────────────────────────────────────

    def connect(self) -> None:
        if self._conn is not None:
            return
        drivers = [self._driver] if self._driver else ["pyodbc", "pytds"]
        errors: list[str] = []
        for name in drivers:
            try:
                if name == "pyodbc":
                    self._connect_pyodbc()
                elif name == "pytds":
                    self._connect_pytds()
                else:
                    raise ValueError(f"Unknown driver: {name}")
                logger.info("Connected to %s/%s via %s", self.host, self.database, name)
                return
            except ImportError as e:
                errors.append(f"{name}: not installed ({e})")
            except Exception as e:  # connection errors
                errors.append(f"{name}: {e}")
        raise ConnectionError(
            "Could not connect to TMS database. Tried: " + "; ".join(errors)
        )

    def _connect_pyodbc(self) -> None:
        import pyodbc

        conn_str = (
            f"DRIVER={{{self.odbc_driver}}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"TrustServerCertificate=yes"
        )
        self._conn = pyodbc.connect(conn_str)
        self._paramstyle = "?"

    def _connect_pytds(self) -> None:
        import pytds

        self._binary_wrap = pytds.Binary

        self._conn = pytds.connect(
            server=self.host,
            port=self.port,
            database=self.database,
            user=self.username,
            password=self.password,
            as_dict=False,
        )
        self._paramstyle = "%s"

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _pytds_sql(sql: str) -> str:
        """pytds uses %-style params: escape literal % (e.g. LIKE '%x%'),
        then turn ? placeholders into %s."""
        return sql.replace("%", "%%").replace("?", "%s")

    def _prep(self, params: Sequence[Any]) -> tuple:
        if self._binary_wrap is None:
            return tuple(params)
        return tuple(self._binary_wrap(p) if isinstance(p, (bytes, bytearray)) else p for p in params)

    # ── queries ──────────────────────────────────────────────────────

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        """Execute a SELECT and return rows as dicts keyed by column name."""
        if self._conn is None:
            self.connect()
        if self._paramstyle == "%s":
            sql = self._pytds_sql(sql)
        cursor = self._conn.cursor()
        cursor.execute(sql, self._prep(params))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # ── writes (media sync only — export never calls these) ─────────

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute a DML statement. Returns the affected row count.

        The connection is opened in a transaction; nothing is durable
        until commit() is called, and rollback() undoes the batch.
        """
        if self._conn is None:
            self.connect()
        if self._paramstyle == "%s":
            sql = self._pytds_sql(sql)
        cursor = self._conn.cursor()
        cursor.execute(sql, self._prep(params))
        return cursor.rowcount

    def insert_identity(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute an INSERT and return the generated identity value.

        The INSERT and SCOPE_IDENTITY() run in one batch so the scope
        is preserved across drivers.
        """
        if self._conn is None:
            self.connect()
        batch = f"SET NOCOUNT ON; {sql}; SELECT CAST(SCOPE_IDENTITY() AS INT) AS id"
        if self._paramstyle == "%s":
            batch = self._pytds_sql(batch)
        cursor = self._conn.cursor()
        cursor.execute(batch, self._prep(params))
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("INSERT did not produce an identity value")
        return int(row[0])

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn is not None:
            self._conn.rollback()

    def query_grouped(
        self, sql: str, ids: list[int], group_key: str = "ObjectID"
    ) -> dict[int, list[dict]]:
        """Run a query with an IN ({IDS}) clause, grouping rows by ``group_key``.

        The SQL must contain the literal placeholder ``{IDS}``.
        """
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.query(sql.replace("{IDS}", placeholders), ids)
        result: dict[int, list[dict]] = {}
        for row in rows:
            key = row.get(group_key)
            if key is not None:
                result.setdefault(key, []).append(row)
        return result
