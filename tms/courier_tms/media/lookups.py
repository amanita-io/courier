"""TMS lookup resolution for media sync.

Python translation of the legacy helper functions/procedures:
get_media_path, get_media_department, get_media_status,
get_media_type_format, get_rendition_number.

Semantics preserved from the originals:
- MediaPaths / MediaStatuses / Departments rows are auto-created if
  the value doesn't exist yet (plain INSERTs into standard TMS lookup
  tables, exactly as the original procedures did).
- MediaTypes / MediaFormats are resolved only, falling back to 0 —
  the originals never created these.
- Rendition numbers are R.<year>-<seq>, sequenced per year over
  renditions previously created by this tool (matched by login id).
"""

from __future__ import annotations

from datetime import date

from ..db import TMSConnection

MAIN_TABLE_ID_MEDIA = 318  # Departments.MainTableID for media departments
NOT_ASSIGNED = "(not assigned)"


class MediaLookups:
    def __init__(self, db: TMSConnection, login_id: str):
        self.db = db
        self.login_id = login_id

    # ── MediaPaths (exact port of get_media_path.sql) ────────────────

    @staticmethod
    def extract_path(url: str | None) -> str | None:
        """SUBSTRING(url, 1, position of the 5th '/') — verbatim from the
        original CHARINDEX chain. For https://host/a/b/file.jpg this is
        'https://host/a/b/' (the DAMS URL layout it was written for).
        Returns None for a NULL url, '' when there is no 5th slash."""
        if url is None:
            return None
        pos = 0
        for _ in range(5):
            pos = url.find("/", pos) + 1  # CHARINDEX(...)+1; find()+1 == 0 when absent
            if pos == 0:
                return ""
        return url[:pos]

    def path_id(self, url: str | None) -> int | None:
        path = self.extract_path(url)
        if path is None:
            return None
        rows = self.db.query("SELECT PathID FROM MediaPaths WHERE Path = ?", (path,))
        if rows:
            return rows[0]["PathID"]
        return self.db.insert_identity(
            "INSERT INTO MediaPaths (Path, PhysicalPath, LoginID) VALUES (?, ?, ?)",
            (path, path, self.login_id),
        )

    # ── Departments (media, MainTableID=318) ─────────────────────────

    def department_id(self, department: str | None) -> int | None:
        name = department or NOT_ASSIGNED
        rows = self.db.query(
            "SELECT DepartmentID FROM Departments WHERE Department = ? AND MainTableID = ?",
            (name, MAIN_TABLE_ID_MEDIA),
        )
        if rows:
            return rows[0]["DepartmentID"]
        if department is None:
            return None  # '(not assigned)' missing from this TMS — don't invent it
        return self.db.insert_identity(
            "INSERT INTO Departments (Department, MainTableID, LoginID) VALUES (?, ?, ?)",
            (department, MAIN_TABLE_ID_MEDIA, self.login_id),
        )

    # ── MediaStatuses ────────────────────────────────────────────────

    def status_id(self, status: str | None) -> int | None:
        name = status or NOT_ASSIGNED
        rows = self.db.query(
            "SELECT MediaStatusID FROM MediaStatuses WHERE MediaStatus = ?", (name,)
        )
        if rows:
            return rows[0]["MediaStatusID"]
        if status is None:
            return None
        return self.db.insert_identity(
            "INSERT INTO MediaStatuses (MediaStatus, LoginID) VALUES (?, ?)",
            (status, self.login_id),
        )

    # ── MediaTypes / MediaFormats (resolve only, 0 fallback) ─────────

    def type_and_format(self, media_type: str | None, media_format: str | None) -> tuple[int, int]:
        if media_format == "TIFFDocument":  # normalization from get_media_type_format.sql
            media_format = "TIFF"
        type_id = 0
        format_id = 0
        if media_type:
            rows = self.db.query(
                "SELECT MediaTypeID FROM MediaTypes WHERE MediaType = ?", (media_type,)
            )
            if rows:
                type_id = rows[0]["MediaTypeID"]
        if type_id and media_format:
            rows = self.db.query(
                "SELECT FormatID FROM MediaFormats WHERE Format = ?", (media_format,)
            )
            if rows:
                format_id = rows[0]["FormatID"]
        return type_id, format_id

    # ── Rendition numbering (was get_rendition_number.sql) ───────────

    def next_rendition_number(self) -> tuple[str, str]:
        """Next R.<year>-<seq> rendition number + fixed-width sort key."""
        year = date.today().year
        prefix = f"R.{year}-"
        # Exact filter from get_rendition_number.sql: chars 3-6 of the
        # rendition number equal the current year (no 'R.' prefix check),
        # rows created by this login; seq = text after the last '-', or
        # the whole value when there is no '-'.
        rows = self.db.query(
            "SELECT RenditionNumber FROM MediaRenditions WHERE LoginID = ?",
            (self.login_id,),
        )
        max_seq = 0
        for r in rows:
            rn = str(r["RenditionNumber"])
            if rn[2:6] != str(year):
                continue
            tail = rn.rsplit("-", 1)[-1] if "-" in rn else rn
            if tail.strip().isdigit():
                max_seq = max(max_seq, int(tail))
        seq = max_seq + 1
        rendition_number = f"{prefix}{seq}"
        sort_number = "R".ljust(8) + str(year) + str(seq).rjust(6)
        return rendition_number, sort_number
