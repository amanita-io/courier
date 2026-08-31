"""Normalization to the open canonical record format.

Every extracted TMS object becomes one canonical envelope:

    {
      "id": "tms:<ObjectID>",
      "type": "Object",
      "label": "<primary title>",
      "object_number": "<accession number>",
      "source": {"system": "tms", "id": "<ObjectID>", "institution": "<name>"},
      "properties": { ...flat + nested collection data... },
      "media": [ {id, type, label, role}, ... ],
      "raw": { ...the full denormalized TMS record, untouched... }
    }

Design rules:
- ``properties`` holds cleaned, resolved values (lookups applied,
  empty values stripped) — what a target system imports.
- ``raw`` preserves the original extraction verbatim, so nothing is
  ever lost in normalization and any future mapping can be rebuilt
  from an old export file.
"""

from __future__ import annotations

from typing import Any

CREATOR_ROLES = ("Artist", "Maker", "Author", "Photographer")


def normalize(record: dict[str, Any], institution: str | None = None) -> dict[str, Any]:
    """Convert a denormalized TMS record into a canonical envelope.

    ``institution`` names the organisation the records came from. It is
    recorded in ``source`` because nothing else in the file identifies
    it: an export that omits it cannot be attributed later without
    going back to whoever produced it. Omitted from the envelope when
    not supplied, per the usual absence rule.
    """
    source_id = str(record["ObjectID"])
    object_number = record.get("ObjectNumber") or f"TMS-{source_id}"

    # Primary title from ObjTitles, falling back to ObjectName
    primary_title = None
    for t in record.get("titles", []):
        if t.get("title"):
            primary_title = t["title"]
            break
    if not primary_title:
        primary_title = record.get("ObjectName") or record.get("Title") or f"Object {object_number}"

    creators = [
        {"name": c.get("display_name"), "role": c.get("role"), "date": c.get("display_date")}
        for c in record.get("constituents", [])
        if c.get("role") in CREATOR_ROLES
    ]

    current_location = None
    for comp in record.get("components", []):
        if comp.get("current_location"):
            current_location = comp["current_location"]
            break

    properties = {
        "object_number": object_number,
        "object_name": record.get("ObjectName"),
        "classification": record.get("Classification"),
        "department": record.get("Department"),
        "object_status": record.get("ObjectStatus"),
        "dated": record.get("Dated"),
        "date_begin": record.get("DateBegin"),
        "date_end": record.get("DateEnd"),
        "medium": record.get("Medium"),
        "credit_line": record.get("CreditLine"),
        "description": record.get("Description"),
        "provenance": record.get("Provenance"),
        "signed": record.get("Signed"),
        "inscribed": record.get("Inscribed"),
        "markings": record.get("Markings"),
        "dimensions_text": record.get("Dimensions_text"),
        "notes": record.get("Notes"),
        "curatorial_remarks": record.get("CuratorialRemarks"),
        "public_access": record.get("PublicAccess"),
        "on_view": record.get("OnView"),
        "current_location": current_location,
        "titles": record.get("titles", []),
        "creators": creators,
        "all_constituents": record.get("constituents", []),
        "context": record.get("context"),
        "accession": record.get("accession"),
        "dates": record.get("dates", []),
        "alt_numbers": record.get("alt_numbers", []),
        "components": record.get("components", []),
        "dimensions": record.get("dimensions", []),
        "media": record.get("media", []),
        "exhibitions": record.get("exhibitions", []),
        "conditions": record.get("conditions", []),
        "insurance": record.get("insurance", []),
        "text_entries": record.get("text_entries", []),
        "object_names": record.get("object_names", []),
        "vocabulary_terms": record.get("vocabulary_terms", []),
        "user_fields": record.get("user_fields", []),
        "geography": record.get("geography", []),
        "references": record.get("references", []),
        "related_objects": record.get("related_objects", []),
        "sites": record.get("sites", []),
    }

    # TMS uses 0 for "no date"
    for date_key in ("date_begin", "date_end"):
        if properties.get(date_key) == 0:
            del properties[date_key]

    # Strip None values, empty lists, and all-null dicts
    properties = {
        k: v
        for k, v in properties.items()
        if v is not None
        and not (isinstance(v, list) and len(v) == 0)
        and not (isinstance(v, dict) and all(val is None for val in v.values()))
    }

    canonical_media = [
        {
            "id": f"tms-media:{m.get('media_master_id', '')}",
            "type": "image",
            "label": m["file_name"],
            "role": "primary" if m.get("primary_display") else "alternate",
        }
        for m in record.get("media", [])
        if m.get("file_name")
    ]

    source = {"system": "tms", "id": source_id}
    if institution:
        source["institution"] = institution

    envelope: dict[str, Any] = {
        "id": f"tms:{source_id}",
        "type": "Object",
        "label": primary_title,
        "object_number": object_number,
        "source": source,
        "properties": properties,
    }
    if canonical_media:
        envelope["media"] = canonical_media
    envelope["raw"] = record
    return envelope
