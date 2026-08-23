"""Media sync engine — translation of process_media.sql.

For each asset: resolve lookups, decide INSERT/UPDATE/DELETE/IGNORE
from local sync state, apply the same DML the original procedure
applied to MediaMaster / MediaRenditions / MediaFiles / MediaXrefs,
one transaction per asset. Errors roll back that asset only and are
recorded in the local error log; the run continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ..db import TMSConnection
from .lookups import MediaLookups
from .models import MediaAsset
from .state import SyncState

logger = logging.getLogger(__name__)

TABLE_ID_OBJECTS = 108

# Media status → MediaXrefs.Rank (from the original procedure)
STATUS_RANK = {"Publication Quality": 1, "Internal Use Only": 2}


@dataclass
class SyncResult:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    ignored: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return self.inserted + self.updated + self.deleted


class MediaSync:
    def __init__(self, db: TMSConnection, state: SyncState, login_id: str = "courier"):
        self.db = db
        self.state = state
        self.login_id = login_id
        self.lookups = MediaLookups(db, login_id)
        self._mm_has_department: bool | None = None

    def _media_master_has_department(self) -> bool:
        """TMS versions differ: MediaMaster gained DepartmentID later."""
        if self._mm_has_department is None:
            rows = self.db.query(
                "SELECT 1 AS x FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'MediaMaster' AND COLUMN_NAME = 'DepartmentID'"
            )
            self._mm_has_department = bool(rows)
        return self._mm_has_department

    def process(self, assets: list[MediaAsset], dry_run: bool = False) -> SyncResult:
        result = SyncResult()
        process_id = self.state.start_process("PROCESS_MEDIA")
        for asset in assets:
            result.total += 1
            try:
                action = self._process_one(process_id, asset, dry_run)
                if action == "INSERT":
                    result.inserted += 1
                elif action == "UPDATE":
                    result.updated += 1
                elif action == "DELETE":
                    result.deleted += 1
                else:
                    result.ignored += 1
            except Exception as e:
                self.db.rollback()
                result.failed += 1
                msg = f"{asset.asset_id}/{asset.tms_record_id}: {e}"
                result.errors.append(msg)
                self.state.log_error(process_id, str(e), asset.tms_record_id, asset.asset_id)
                logger.error("FAILED %s", msg)
        self.state.finish_process(
            process_id, result.total, result.succeeded, result.failed,
            info="dry run — no writes" if dry_run else None,
        )
        return result

    # ── per-asset processing ─────────────────────────────────────────

    def _process_one(self, process_id: int, asset: MediaAsset, dry_run: bool) -> str:
        action, media_master_id, last_update = self.state.get_action(
            asset.asset_id, asset.tms_record_id, TABLE_ID_OBJECTS,
            deleted=asset.deleted_date is not None,
        )
        logger.info(
            "%s asset=%s object=%s%s",
            action, asset.asset_id, asset.tms_record_id, " (dry run)" if dry_run else "",
        )
        if dry_run or action == "IGNORE":
            return action

        if action == "INSERT":
            media_master_id = self._insert(asset)
        elif action == "UPDATE":
            # Only apply if the source changed since our last write
            if last_update and asset.updated_date and asset.updated_date.isoformat() <= last_update:
                self.db.rollback()  # discard any open read transaction
                return "IGNORE"
            self._update(asset, media_master_id)
        elif action == "DELETE":
            self._delete(asset, media_master_id)

        self.db.commit()
        self.state.log_record(
            process_id, media_master_id, asset.tms_record_id,
            asset.asset_id, TABLE_ID_OBJECTS, action,
        )
        return action

    def _insert(self, asset: MediaAsset) -> int:
        has_dept = self._media_master_has_department()
        path_id = self.lookups.path_id(asset.image_url)
        if path_id is None:
            # exact behavior of process_media.sql when get_media_path yields NULL
            raise ValueError("Media Path is null. Avoiding corrupt media record.")
        file_name = asset.file_name
        department_id = self.lookups.department_id(asset.department) if has_dept else None
        status_id = self.lookups.status_id(asset.media_status)
        type_id, format_id = self.lookups.type_and_format(asset.media_type, asset.media_format)
        rendition_number, sort_number = self.lookups.next_rendition_number()
        rank = STATUS_RANK.get(asset.media_status or "", 0)

        mm_cols = ("DisplayRendID, PrimaryRendID, MediaView, Description, "
                   "PublicAccess, PublicCaption, Remarks, LoginID, Copyright, ApprovedForWeb")
        mm_vals = "0, 0, NULL, ?, 1, NULL, NULL, ?, NULL, ?"
        mm_params = [asset.description, self.login_id, asset.approved_for_web]
        if has_dept:
            mm_cols += ", DepartmentID"
            mm_vals += ", ?"
            mm_params.append(department_id)
        media_master_id = self.db.insert_identity(
            f"INSERT INTO MediaMaster ({mm_cols}) VALUES ({mm_vals})", mm_params
        )
        rendition_id = self.db.insert_identity(
            "INSERT INTO MediaRenditions (MediaMasterID, PrimaryFileID, ParentRendID, "
            "MediaTypeID, MediaStatusID, MediaStatusDate, RenditionNumber, SortNumber, "
            "RenditionDate, MediaSizeID, Technique, Duration, IsColor, Quality, QualityDate, "
            "QualityConID, Remarks, ThumbBlob, ThumbPathID, ThumbFileName, ThumbExtensionID, "
            "ThumbBlobSize, LocTermID, QuantityMade, QuantityAvailable, LoginID) "
            "VALUES (?, 0, -1, ?, ?, NULL, ?, ?, ?, 0, NULL, NULL, 0, NULL, NULL, NULL, NULL, "
            "?, NULL, NULL, NULL, ?, NULL, 1, NULL, ?)",
            (
                media_master_id, type_id, status_id, rendition_number, sort_number,
                date.today().isoformat(), asset.thumbnail,
                len(asset.thumbnail) if asset.thumbnail else 0, self.login_id,
            ),
        )
        self.db.execute(
            "UPDATE MediaMaster SET DisplayRendID = ?, PrimaryRendID = ? WHERE MediaMasterID = ?",
            (rendition_id, rendition_id, media_master_id),
        )
        file_id = self.db.insert_identity(
            "INSERT INTO MediaFiles (RenditionID, PathID, FileName, FormatID, PixelH, PixelW, "
            "ColorDepthID, Duration, FileSize, MemorySize, LoginID, FileDate) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 0, ?, ?)",
            (
                rendition_id, path_id, file_name, format_id, asset.pixel_height,
                asset.pixel_width, asset.image_size, self.login_id,
                asset.created_date.isoformat() if asset.created_date else None,
            ),
        )
        self.db.execute(
            "UPDATE MediaRenditions SET PrimaryFileID = ? WHERE RenditionID = ?",
            (file_id, rendition_id),
        )
        self.db.execute(
            "INSERT INTO MediaXrefs (MediaMasterID, ID, TableID, PrimaryDisplay, Rank, LoginID) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (media_master_id, asset.tms_record_id, TABLE_ID_OBJECTS,
             asset.primary_display, rank, self.login_id),
        )
        if asset.primary_display:
            self._demote_other_primaries(asset.tms_record_id, media_master_id)
        return media_master_id

    def _update(self, asset: MediaAsset, media_master_id: int) -> None:
        status_id = self.lookups.status_id(asset.media_status)
        # PublicCaption = NULL is faithful: the original sets it to
        # @l_credit_line_repro, which is declared but never assigned.
        if self._media_master_has_department():
            department_id = self.lookups.department_id(asset.department)
            self.db.execute(
                "UPDATE MediaMaster SET DepartmentID = ?, ApprovedForWeb = ?, "
                "PublicCaption = NULL WHERE MediaMasterID = ?",
                (department_id, asset.approved_for_web, media_master_id),
            )
        else:
            self.db.execute(
                "UPDATE MediaMaster SET ApprovedForWeb = ?, PublicCaption = NULL "
                "WHERE MediaMasterID = ?",
                (asset.approved_for_web, media_master_id),
            )
        self.db.execute(
            "UPDATE MediaRenditions SET MediaStatusID = ? WHERE MediaMasterID = ?",
            (status_id, media_master_id),
        )
        self.db.execute(
            "UPDATE MediaXrefs SET PrimaryDisplay = ? WHERE MediaMasterID = ?",
            (asset.primary_display, media_master_id),
        )
        if asset.primary_display:
            self._demote_other_primaries(asset.tms_record_id, media_master_id)

    def _delete(self, asset: MediaAsset, media_master_id: int) -> None:
        # Exact port: delete only the MediaMaster row. TMS's own
        # MediaDelete / MediaRenditionDelete triggers cascade to
        # renditions, files, and xrefs.
        self.db.execute("DELETE FROM MediaMaster WHERE MediaMasterID = ?", (media_master_id,))
        # Promote the newest remaining media for the object to primary
        self.db.execute(
            "UPDATE MediaXrefs SET PrimaryDisplay = 1 WHERE MediaXrefID IN "
            "(SELECT TOP 1 MediaXrefID FROM MediaXrefs "
            " WHERE ID = ? AND TableID = ? ORDER BY EnteredDate DESC)",
            (asset.tms_record_id, TABLE_ID_OBJECTS),
        )

    def _demote_other_primaries(self, tms_record_id: int, keep_media_master_id: int) -> None:
        self.db.execute(
            "UPDATE MediaXrefs SET PrimaryDisplay = 0 "
            "WHERE PrimaryDisplay != 0 AND MediaMasterID != ? AND ID = ? AND TableID = ?",
            (keep_media_master_id, tms_record_id, TABLE_ID_OBJECTS),
        )
