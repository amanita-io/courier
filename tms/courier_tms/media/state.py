"""Local sync-state store (SQLite).

Replaces the four custom tables the legacy pipeline created inside
the TMS database (media_staging, media_record_log, media_process_log,
media_error_log). The state file lives wherever the operator runs the
tool — the TMS database is never used to store tool state.

The record log answers the same question get_action.sql answered:
"have I already inserted this (asset_id, tms_record_id) pair, and
which MediaMaster row did it create?" — which drives the
INSERT / UPDATE / DELETE / IGNORE decision.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS record_log (
    record_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id      INTEGER NOT NULL,
    media_master_id INTEGER NOT NULL,
    tms_record_id   INTEGER NOT NULL,
    asset_id        TEXT NOT NULL,
    table_id        INTEGER NOT NULL,
    action          TEXT NOT NULL,
    created_date    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS record_log_i1
    ON record_log (asset_id, tms_record_id, table_id);

CREATE TABLE IF NOT EXISTS process_log (
    process_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    process_name      TEXT NOT NULL,
    records_succeeded INTEGER,
    records_failed    INTEGER,
    records_total     INTEGER,
    additional_info   TEXT,
    start_date        TEXT NOT NULL,
    end_date          TEXT
);

CREATE TABLE IF NOT EXISTS error_log (
    error_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id    INTEGER NOT NULL,
    tms_record_id INTEGER,
    asset_id      TEXT,
    error_msg     TEXT,
    error_date    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncState:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── process log ──────────────────────────────────────────────────

    def start_process(self, name: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO process_log (process_name, start_date) VALUES (?, ?)",
            (name, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_process(
        self, process_id: int, total: int, succeeded: int, failed: int, info: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE process_log SET records_total=?, records_succeeded=?, "
            "records_failed=?, additional_info=?, end_date=? WHERE process_id=?",
            (total, succeeded, failed, info, _now(), process_id),
        )
        self.conn.commit()

    # ── record + error logs ──────────────────────────────────────────

    def log_record(
        self,
        process_id: int,
        media_master_id: int,
        tms_record_id: int,
        asset_id: str,
        table_id: int,
        action: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO record_log (process_id, media_master_id, tms_record_id, "
            "asset_id, table_id, action, created_date) VALUES (?,?,?,?,?,?,?)",
            (process_id, media_master_id, tms_record_id, asset_id, table_id, action, _now()),
        )
        self.conn.commit()

    def log_error(
        self, process_id: int, error_msg: str, tms_record_id: int | None, asset_id: str | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO error_log (process_id, tms_record_id, asset_id, error_msg, error_date) "
            "VALUES (?,?,?,?,?)",
            (process_id, tms_record_id, asset_id, error_msg, _now()),
        )
        self.conn.commit()

    # ── action resolution (was get_action.sql) ───────────────────────

    def get_action(
        self, asset_id: str, tms_record_id: int, table_id: int, deleted: bool
    ) -> tuple[str, int | None, str | None]:
        """Exact port of get_action.sql.

        Returns (action, media_master_id, last_update_iso):
        - deleted feed: DELETE with the logged INSERT's media_master_id
          if an INSERT row exists for (asset, record, table), else IGNORE.
        - live feed: UPDATE with that media_master_id if an INSERT row
          exists — last_update is MAX(created_date) over the pair's
          non-DELETE log rows — else INSERT.
        As in the original, logged DELETE rows are not consulted.
        """
        insert_rows = self.conn.execute(
            "SELECT media_master_id FROM record_log "
            "WHERE tms_record_id=? AND asset_id=? AND table_id=? AND action='INSERT' "
            "ORDER BY record_id",
            (tms_record_id, asset_id, table_id),
        ).fetchall()

        if deleted:
            if insert_rows:
                return ("DELETE", insert_rows[-1][0], None)
            return ("IGNORE", None, None)

        if not insert_rows:
            return ("INSERT", None, None)

        row = self.conn.execute(
            "SELECT MAX(created_date) FROM record_log "
            "WHERE tms_record_id=? AND asset_id=? AND table_id=? AND action!='DELETE'",
            (tms_record_id, asset_id, table_id),
        ).fetchone()
        return ("UPDATE", insert_rows[-1][0], row[0] if row else None)
