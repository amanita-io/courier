"""Input model for media sync.

Replaces parse_json.sql + the media_staging table: assets arrive as a
JSON array, are validated here, and are processed straight from
memory — nothing is staged inside the TMS database.

Two key styles are accepted for each asset object:
- snake_case (native):   tms_record_id, file_name, image_url, ...
- legacy PascalCase (the original DAM feed): ObjectId,
  Filename, ShareURL, ThumbData, ...
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# legacy DAM-feed key → native key
LEGACY_KEYS = {
    "ObjectId": "tms_record_id",
    "CreatedDate": "created_date",
    "UpdatedDate": "updated_date",
    "DeletedDate": "deleted_date",
    "ImageSizeInKilobytes": "image_size",
    "Filename": "file_name",
    "MediaStatus": "media_status",
    "MediaType": "media_type",
    "MediaFormat": "media_format",
    "ImageRestrictions": "restrictions",
    "ImageDescription": "description",
    "IsPrimary": "primary_display",
    "ImageCreatorsDepartment": "department",
    "ImageWidth": "pixel_width",
    "ImageHeight": "pixel_height",
    "AssetExternalId": "external_id",
    "ApprovedForWebsite": "approved_for_web",
    "ShareURL": "image_url",
    "ThumbData": "thumbnail_b64",
    "AssetId": "asset_id",
    "ImageSource": "image_source",
}

_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_dt(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).split(".")[0].replace("Z", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unparseable date: {val!r}")


def _to_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return int(val)
    return int(val)


def _to_bool_int(val: Any) -> int:
    """Exact port of parse_json.sql: CASE x WHEN 'true' THEN 1 ELSE 0 END.

    The comparison is case-insensitive (the TMS DB collation is), and
    anything that isn't 'true' — including 'false', '', unknown text —
    is 0. Native numeric/bool inputs pass through as 0/1.
    """
    if isinstance(val, str):
        return 1 if val.strip().lower() == "true" else 0
    return 1 if val else 0


@dataclass
class MediaAsset:
    """One media asset to sync into TMS, keyed by (asset_id, tms_record_id)."""

    asset_id: str
    tms_record_id: int
    image_url: str
    file_name: str | None = None
    media_status: str | None = None
    media_type: str | None = None
    media_format: str | None = None
    restrictions: str | None = None
    description: str | None = None
    primary_display: int = 0
    department: str | None = None
    pixel_width: int = 0
    pixel_height: int = 0
    image_size: int = 0
    external_id: str | None = None
    approved_for_web: int = 0
    image_source: str | None = None
    created_date: datetime | None = None
    updated_date: datetime | None = None
    deleted_date: datetime | None = None
    thumbnail: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "MediaAsset":
        # Fold legacy keys into native keys (native wins on conflict)
        data = {LEGACY_KEYS.get(k, k): v for k, v in obj.items() if k in LEGACY_KEYS}
        data.update({k: v for k, v in obj.items() if k not in LEGACY_KEYS})

        # Required fields mirror the NOT NULL columns of the original
        # media_staging table (department is NOT NULL but has the DDL
        # default '(not assigned)'; thumbnail is NOT NULL — the
        # original pipeline required a thumbnail on every asset).
        required = (
            "asset_id", "tms_record_id", "image_url", "image_size", "file_name",
            "media_status", "media_type", "media_format", "pixel_width",
            "pixel_height", "external_id", "thumbnail_b64",
        )
        missing = [k for k in required if data.get(k) in (None, "")]
        if missing:
            raise ValueError(f"Asset missing required field(s): {', '.join(missing)}")

        # parse_json.sql: xs:base64Binary decode; size is DATALENGTH of the bytes
        thumbnail = base64.b64decode(data["thumbnail_b64"])

        return cls(
            asset_id=str(data["asset_id"]),
            tms_record_id=_to_int(data["tms_record_id"]),
            image_url=str(data["image_url"]),
            file_name=data.get("file_name"),
            media_status=data.get("media_status"),
            media_type=data.get("media_type"),
            media_format=data.get("media_format"),
            restrictions=data.get("restrictions"),
            description=data.get("description"),
            primary_display=_to_bool_int(data.get("primary_display")),
            department=data.get("department"),
            pixel_width=_to_int(data.get("pixel_width")),
            pixel_height=_to_int(data.get("pixel_height")),
            image_size=_to_int(data.get("image_size")),
            external_id=data.get("external_id"),
            approved_for_web=_to_bool_int(data.get("approved_for_web")),
            image_source=data.get("image_source"),
            created_date=_parse_dt(data.get("created_date")),
            updated_date=_parse_dt(data.get("updated_date")),
            deleted_date=_parse_dt(data.get("deleted_date")),
            thumbnail=thumbnail,
        )
