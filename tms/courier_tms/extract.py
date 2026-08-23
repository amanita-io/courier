"""TMS object extraction.

Reads a TMS 2018+ SQL Server database and yields fully denormalized
object records — one dict per object, with nested lists for titles,
constituents, locations, media, exhibitions, conditions, and so on.

Handles the TMS relational graph:

    Objects → ObjTitles, ObjContext, ObjAccession, ObjComponents,
              ObjLocations, ObjDates, AltNums, Dimensions,
              ConXrefs/ConXrefDetails → Constituents,
              MediaXrefs → MediaRenditions → MediaFiles,
              ExhObjXrefs → Exhibitions,
              Conditions, ObjInsurance, TextEntries

TMS architecture notes:
- Location tracking goes through ObjComponents (ComponentID), not
  directly through Objects. Every object has at least one component.
- Cross-reference tables (ConXrefs, AltNums, MediaXrefs, Conditions,
  TextEntries) are polymorphic via TableID; Objects is TableID 108.
- Objects are processed in batches to stay under SQL Server's
  ~2100-parameter limit on IN clauses.

All queries are read-only SELECTs.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator

from .db import TMSConnection

logger = logging.getLogger(__name__)

# TMS TableID constants (used in polymorphic cross-reference tables)
TABLE_ID_OBJECTS = 108
TABLE_ID_CONSTITUENTS = 23
TABLE_ID_EXHIBITIONS = 47
TABLE_ID_LOANS = 54

# ConstituentTypeID
CONSTITUENT_TYPE_PERSON = 1
CONSTITUENT_TYPE_INSTITUTION = 2

# Batch size for related-data queries. SQL Server allows ~2100 params
# per statement; stay well under it.
BATCH_SIZE = 500


def _safe_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f != 0 else None
    except (ValueError, TypeError):
        return None


def _iso(val: Any) -> str | None:
    return str(val) if val else None


import re as _re


class TMSExtractor:
    """Extracts denormalized object records from a TMS database."""

    def __init__(self, conn: TMSConnection, thes_database: str | None = None):
        self.db = conn
        self._lookups: dict[str, dict] | None = None
        # The TMS thesaurus (Terms/TermMaster) lives in a SEPARATE
        # database (conventionally named *TMSThes). Cross-database
        # joins resolve term labels when it is present; otherwise
        # vocabulary links are exported with raw TermIDs.
        self._thes_database = thes_database
        self._thes_checked = thes_database is not None

    @property
    def thes_database(self) -> str | None:
        if not self._thes_checked:
            self._thes_checked = True
            rows = self.db.query(
                "SELECT name FROM sys.databases WHERE LOWER(name) LIKE '%thes%'"
            )
            if rows:
                self._thes_database = rows[0]["name"]
                logger.info("Thesaurus database detected: %s", self._thes_database)
        if self._thes_database and not _re.fullmatch(r"[A-Za-z0-9_]+", self._thes_database):
            raise ValueError(f"Unsafe thesaurus database name: {self._thes_database!r}")
        return self._thes_database

    # ── Lookup tables (cached once) ──────────────────────────────────

    def load_lookups(self) -> dict[str, dict]:
        if self._lookups is not None:
            return self._lookups
        lookups: dict[str, dict] = {}

        rows = self.db.query("SELECT ClassificationID, Classification FROM Classifications")
        lookups["classifications"] = {r["ClassificationID"]: r["Classification"] for r in rows}

        rows = self.db.query("SELECT DepartmentID, Department FROM Departments")
        lookups["departments"] = {r["DepartmentID"]: r["Department"] for r in rows}

        rows = self.db.query("SELECT ObjectStatusID, ObjectStatus FROM ObjectStatuses")
        lookups["object_statuses"] = {r["ObjectStatusID"]: r["ObjectStatus"] for r in rows}

        rows = self.db.query("SELECT RoleID, Role FROM Roles")
        lookups["roles"] = {r["RoleID"]: r["Role"] for r in rows}

        rows = self.db.query("SELECT TitleTypeID, TitleType FROM TitleTypes")
        lookups["title_types"] = {r["TitleTypeID"]: r["TitleType"] for r in rows}

        rows = self.db.query(
            "SELECT LocationID, Site, Room, UnitType, UnitNumber, UnitPosition FROM Locations"
        )
        lookups["locations"] = {}
        for r in rows:
            parts = [_safe_str(r[k]) for k in ("Site", "Room", "UnitType", "UnitNumber", "UnitPosition")]
            lookups["locations"][r["LocationID"]] = " > ".join(p for p in parts if p)

        rows = self.db.query("SELECT DimensionTypeID, DimensionType FROM DimensionTypes")
        lookups["dimension_types"] = {r["DimensionTypeID"]: r["DimensionType"] for r in rows}

        rows = self.db.query("SELECT UnitID, UnitName FROM DimensionUnits")
        lookups["dimension_units"] = {r["UnitID"]: r["UnitName"] for r in rows}

        rows = self.db.query("SELECT AccessionMethodID, AccessionMethod FROM AccessionMethods")
        lookups["accession_methods"] = {r["AccessionMethodID"]: r["AccessionMethod"] for r in rows}

        rows = self.db.query(
            "SELECT ConstituentID, DisplayName, FirstName, LastName, "
            "Institution, ConstituentTypeID, Active FROM Constituents"
        )
        lookups["constituents"] = {r["ConstituentID"]: r for r in rows}

        rows = self.db.query("SELECT ObjectNameTypeID, ObjectNameType FROM ObjectNameTypes")
        lookups["object_name_types"] = {r["ObjectNameTypeID"]: r["ObjectNameType"] for r in rows}

        self._lookups = lookups
        return lookups

    # ── Extraction ───────────────────────────────────────────────────

    def count_objects(self) -> int:
        rows = self.db.query("SELECT COUNT(*) AS n FROM Objects WHERE ObjectID > 0")
        return rows[0]["n"]

    def extract(self, limit: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield one denormalized record per object."""
        lookups = self.load_lookups()

        limit_clause = f"TOP {int(limit)} " if limit else ""
        objects = self.db.query(
            f"SELECT {limit_clause}* FROM Objects WHERE ObjectID > 0 ORDER BY ObjectID"
        )
        if not objects:
            return

        for start in range(0, len(objects), BATCH_SIZE):
            batch = objects[start : start + BATCH_SIZE]
            yield from self._extract_batch(batch, lookups)

    def _extract_batch(
        self, objects: list[dict], lookups: dict[str, dict]
    ) -> Iterable[dict[str, Any]]:
        object_ids = [o["ObjectID"] for o in objects]
        q = self.db.query_grouped

        titles = q(
            "SELECT ObjectID, TitleID, Title, TitleTypeID, DisplayOrder, Active "
            "FROM ObjTitles WHERE ObjectID IN ({IDS}) AND Active=1 ORDER BY ObjectID, DisplayOrder",
            object_ids,
        )
        con_xrefs = q(
            "SELECT cx.ID as ObjectID, cxd.ConstituentID, cxd.RoleTypeID as DetailRoleTypeID, "
            "cx.RoleID, cx.DisplayOrder, cxd.DisplayDate, cxd.Prefix, cxd.Suffix "
            "FROM ConXrefs cx "
            "JOIN ConXrefDetails cxd ON cx.ConXrefID = cxd.ConXrefID "
            "WHERE cx.TableID = 108 AND cx.ID IN ({IDS}) AND cxd.UnMasked = 1 "
            "ORDER BY cx.ID, cx.DisplayOrder",
            object_ids,
        )
        contexts = q(
            "SELECT ObjectID, Culture, Style, Period, Dynasty, Movement, Nationality, School "
            "FROM ObjContext WHERE ObjectID IN ({IDS})",
            object_ids,
        )
        accessions = q(
            "SELECT ObjectID, AccessionMethodID, AccessionValue, CurrencyID, Source, "
            "AccessionISODate, AcquisitionLot, AcqJustification, Authorizer, AuthDate, "
            "DeedOfGiftSentISO, DeedOfGiftReceivedISO "
            "FROM ObjAccession WHERE ObjectID IN ({IDS})",
            object_ids,
        )
        obj_dates = q(
            "SELECT ObjectID, EventType, DateText, DateBegSearch, DateEndSearch, Remarks "
            "FROM ObjDates WHERE ObjectID IN ({IDS}) AND Active=1",
            object_ids,
        )
        alt_nums = q(
            "SELECT ID as ObjectID, AltNum, Description, Remarks "
            "FROM AltNums WHERE TableID=108 AND ID IN ({IDS})",
            object_ids,
        )
        components = q(
            "SELECT oc.ObjectID, oc.ComponentID, oc.ComponentName, oc.ComponentNumber, "
            "oc.HomeLocationID, oc.PhysDesc, oc.InstallComments, oc.StorageComments, "
            "ol.LocationID, ol.TransDate, ol.Handler "
            "FROM ObjComponents oc "
            "LEFT JOIN ObjLocations ol ON oc.CurrentObjLocID = ol.ObjLocationID "
            "WHERE oc.ObjectID IN ({IDS})",
            object_ids,
        )
        dimensions = q(
            "SELECT die.ID as ObjectID, die.DisplayDimensions, die.Description as DimDescription, "
            "d.DimensionTypeID, d.Dimension, d.PrimaryUnitID "
            "FROM DimItemElemXrefs die "
            "JOIN Dimensions d ON die.DimItemElemXrefID = d.DimItemElemXrefID "
            "WHERE die.TableID=108 AND die.ID IN ({IDS})",
            object_ids,
        )
        media = q(
            "SELECT mx.ID as ObjectID, mx.MediaMasterID, mx.Rank, mx.PrimaryDisplay, "
            "mm.Description as MediaDescription, mm.PublicAccess, mm.Copyright, "
            "mr.RenditionID, mr.MediaTypeID, "
            "mf.FileName, mf.PixelH, mf.PixelW, mf.FileSize, "
            "mp.Path as FilePath "
            "FROM MediaXrefs mx "
            "JOIN MediaMaster mm ON mx.MediaMasterID = mm.MediaMasterID "
            "LEFT JOIN MediaRenditions mr ON mm.PrimaryRendID = mr.RenditionID "
            "LEFT JOIN MediaFiles mf ON mr.PrimaryFileID = mf.FileID "
            "LEFT JOIN MediaPaths mp ON mf.PathID = mp.PathID "
            "WHERE mx.TableID=108 AND mx.ID IN ({IDS}) "
            "ORDER BY mx.ID, mx.Rank",
            object_ids,
        )
        exhibitions = q(
            "SELECT eox.ObjectID, eox.ExhibitionID, eox.Section, eox.CatalogueNumber, "
            "e.ExhTitle, e.BeginISODate, e.EndISODate, e.DisplayDate as ExhDisplayDate "
            "FROM ExhObjXrefs eox "
            "JOIN Exhibitions e ON eox.ExhibitionID = e.ExhibitionID "
            "WHERE eox.ObjectID IN ({IDS})",
            object_ids,
        )
        conditions = q(
            "SELECT ID as ObjectID, ConditionID, ExaminerID, OverallConditionID, "
            "SurveyISODate, ReportISODate, OverallAnalysis, Remarks as CondRemarks, "
            "SurveyTypeID, TreatmentPriorityID "
            "FROM Conditions WHERE TableID=108 AND ID IN ({IDS}) "
            "ORDER BY ID, SurveyISODate DESC",
            object_ids,
        )
        insurance = q(
            "SELECT ObjectID, Value, ValueISODate, ValuationPurposeID, "
            "CurrencyID, AppraiserID "
            "FROM ObjInsurance WHERE ObjectID IN ({IDS})",
            object_ids,
        )
        text_entries = q(
            "SELECT ID as ObjectID, TextTypeID, TextStatusID, "
            "CONVERT(VARCHAR(MAX), TextEntry) as TextEntry "
            "FROM TextEntries WHERE TableID=108 AND ID IN ({IDS})",
            object_ids,
        )

        # ── Coverage informed by public prior art (see PRIOR_ART.md):
        # tmssync mirrors the thesaurus + flex-field tables; Harvard's
        # Digital Giza queries UserFields, ObjGeography, RefXRefs,
        # Associations, and cross-database Terms. ──────────────────────

        object_names = q(
            "SELECT ObjectID, ObjectNameID, ObjectName, ObjectNameTypeID, DisplayOrder "
            "FROM ObjectNames WHERE ObjectID IN ({IDS}) AND Active=1 "
            "ORDER BY ObjectID, DisplayOrder",
            object_ids,
        )

        thes_db = self.thes_database
        if thes_db:
            term_cols = "t.Term, t.TermMasterID, "
            term_join = f"LEFT JOIN [{thes_db}].dbo.Terms t ON tx.TermID = t.TermID "
        else:
            term_cols = "NULL AS Term, NULL AS TermMasterID, "
            term_join = ""
        thes_xrefs = q(
            "SELECT tx.ID as ObjectID, tx.ThesXrefID, tx.TermID, " + term_cols +
            "txt.ThesXrefType, tx.DisplayOrder, tx.Remarks "
            "FROM ThesXrefs tx "
            "LEFT JOIN ThesXrefTypes txt ON tx.ThesXrefTypeID = txt.ThesXrefTypeID "
            + term_join +
            "WHERE tx.TableID=108 AND tx.Active=1 AND tx.ID IN ({IDS}) "
            "ORDER BY tx.ID, tx.DisplayOrder",
            object_ids,
        )

        user_fields = q(
            "SELECT ufx.ID as ObjectID, uf.UserFieldName, ufg.GroupName, "
            "ufx.FieldValue, ufx.ValueDate, ufx.ValueRemarks, ufx.ContextID "
            "FROM UserFieldXrefs ufx "
            "JOIN UserFields uf ON ufx.UserFieldID = uf.UserFieldID "
            "LEFT JOIN UserFieldGroups ufg ON ufx.UserFieldGroupID = ufg.UserFieldGroupID "
            "WHERE ufx.ID IN ({IDS})",
            object_ids,
        )

        geography = q(
            "SELECT og.ObjectID, gc.GeoCode, og.Continent, og.SubContinent, og.Country, "
            "og.Region, og.State, og.County, og.City, og.Locale, og.River, og.Building, "
            "og.Excavation, og.Locus, og.Notes "
            "FROM ObjGeography og LEFT JOIN GeoCodes gc ON og.GeoCodeID = gc.GeoCodeID "
            "WHERE og.ObjectID IN ({IDS})",
            object_ids,
        )

        references = q(
            "SELECT rx.ID as ObjectID, rm.ReferenceID, rm.Heading, rm.Title, "
            "CONVERT(VARCHAR(MAX), rm.BoilerText) as BoilerText, rm.YearPublished, "
            "rx.PageNumber, rx.CatalogueNumber, rx.FigureNumber, rx.Remarks "
            "FROM RefXRefs rx JOIN ReferenceMaster rm ON rx.ReferenceID = rm.ReferenceID "
            "WHERE rx.TableID=108 AND rx.ID IN ({IDS})",
            object_ids,
        )

        # Associations are bidirectional: this object may be ID1 or ID2.
        assoc_1 = q(
            "SELECT a.ID1 as ObjectID, a.ID2 as RelatedID, r.Relation1 as Relation, "
            "a.Remarks, a.DisplayDate "
            "FROM Associations a "
            "LEFT JOIN Relationships r ON a.RelationshipID = r.RelationshipID "
            "WHERE a.TableID=108 AND a.ID1 IN ({IDS})",
            object_ids,
        )
        assoc_2 = q(
            "SELECT a.ID2 as ObjectID, a.ID1 as RelatedID, r.Relation2 as Relation, "
            "a.Remarks, a.DisplayDate "
            "FROM Associations a "
            "LEFT JOIN Relationships r ON a.RelationshipID = r.RelationshipID "
            "WHERE a.TableID=108 AND a.ID2 IN ({IDS})",
            object_ids,
        )
        related_ids = sorted({row["RelatedID"] for rows in (assoc_1, assoc_2)
                              for lst in rows.values() for row in lst
                              if row.get("RelatedID") is not None})
        related_objects: dict[int, dict] = {}
        if related_ids:
            for start in range(0, len(related_ids), BATCH_SIZE):
                chunk = related_ids[start : start + BATCH_SIZE]
                placeholders = ",".join("?" * len(chunk))
                for r in self.db.query(
                    f"SELECT ObjectID, ObjectNumber, ObjectName FROM Objects "
                    f"WHERE ObjectID IN ({placeholders})", chunk,
                ):
                    related_objects[r["ObjectID"]] = r

        sites = q(
            "SELECT sx.ObjectID, s.SiteName, s.SiteNumber, sx.Remarks, "
            "sx.Subsite1, sx.Subsite2, sx.Subsite3 "
            "FROM SiteObjXrefs sx JOIN Sites s ON sx.SiteID = s.SiteID "
            "WHERE sx.ObjectID IN ({IDS})",
            object_ids,
        )

        for obj in objects:
            oid = obj["ObjectID"]
            record = {
                "ObjectID": oid,
                "ObjectNumber": _safe_str(obj.get("ObjectNumber")),
                "ObjectName": _safe_str(obj.get("ObjectName")),
                "Title": _safe_str(obj.get("Title")),  # legacy — prefer ObjTitles
                "Dated": _safe_str(obj.get("Dated")),
                "DateBegin": obj.get("DateBegin"),
                "DateEnd": obj.get("DateEnd"),
                "Medium": _safe_str(obj.get("Medium")),
                "CreditLine": _safe_str(obj.get("CreditLine")),
                "Description": _safe_str(obj.get("Description")),
                "Provenance": _safe_str(obj.get("Provenance")),
                "Signed": _safe_str(obj.get("Signed")),
                "Inscribed": _safe_str(obj.get("Inscribed")),
                "Markings": _safe_str(obj.get("Markings")),
                "Dimensions_text": _safe_str(obj.get("Dimensions")),
                "Notes": _safe_str(obj.get("Notes")),
                "CuratorialRemarks": _safe_str(obj.get("CuratorialRemarks")),
                "Chat": _safe_str(obj.get("Chat")),
                "PublicAccess": obj.get("PublicAccess"),
                "OnView": obj.get("OnView"),
                "Classification": lookups["classifications"].get(obj.get("ClassificationID")),
                "Department": lookups["departments"].get(obj.get("DepartmentID")),
                "ObjectStatus": lookups["object_statuses"].get(obj.get("ObjectStatusID")),
                "titles": [],
                "constituents": [],
                "context": None,
                "accession": None,
                "dates": [],
                "alt_numbers": [],
                "components": [],
                "dimensions": [],
                "media": [],
                "exhibitions": [],
                "conditions": [],
                "insurance": [],
                "text_entries": [],
                "object_names": [],
                "vocabulary_terms": [],
                "user_fields": [],
                "geography": [],
                "references": [],
                "related_objects": [],
                "sites": [],
            }

            for t in titles.get(oid, []):
                record["titles"].append({
                    "title": _safe_str(t["Title"]),
                    "title_type": lookups["title_types"].get(t["TitleTypeID"], "Primary"),
                    "display_order": t["DisplayOrder"],
                })

            for cx in con_xrefs.get(oid, []):
                con_id = cx.get("ConstituentID")
                con = lookups["constituents"].get(con_id, {})
                record["constituents"].append({
                    "constituent_id": con_id,
                    "display_name": con.get("DisplayName"),
                    "first_name": con.get("FirstName"),
                    "last_name": con.get("LastName"),
                    "institution": con.get("Institution"),
                    "type": "person" if con.get("ConstituentTypeID") == CONSTITUENT_TYPE_PERSON else "institution",
                    "role": lookups["roles"].get(cx.get("RoleID"), "Unknown"),
                    "display_date": _safe_str(cx.get("DisplayDate")),
                    "prefix": _safe_str(cx.get("Prefix")),
                    "suffix": _safe_str(cx.get("Suffix")),
                    "display_order": cx.get("DisplayOrder"),
                })

            ctx_list = contexts.get(oid, [])
            if ctx_list:
                c = ctx_list[0]
                record["context"] = {
                    "culture": _safe_str(c.get("Culture")),
                    "style": _safe_str(c.get("Style")),
                    "period": _safe_str(c.get("Period")),
                    "dynasty": _safe_str(c.get("Dynasty")),
                    "movement": _safe_str(c.get("Movement")),
                    "nationality": _safe_str(c.get("Nationality")),
                    "school": _safe_str(c.get("School")),
                }

            acc_list = accessions.get(oid, [])
            if acc_list:
                a = acc_list[0]
                record["accession"] = {
                    "method": lookups["accession_methods"].get(a.get("AccessionMethodID")),
                    "value": _safe_float(a.get("AccessionValue")),
                    "source": _safe_str(a.get("Source")),
                    "date": _iso(a.get("AccessionISODate")),
                    "lot": _safe_str(a.get("AcquisitionLot")),
                    "justification": _safe_str(a.get("AcqJustification")),
                    "authorizer": _safe_str(a.get("Authorizer")),
                    "deed_sent": _iso(a.get("DeedOfGiftSentISO")),
                    "deed_received": _iso(a.get("DeedOfGiftReceivedISO")),
                }

            for d in obj_dates.get(oid, []):
                record["dates"].append({
                    "event_type": _safe_str(d.get("EventType")),
                    "date_text": _safe_str(d.get("DateText")),
                    "date_begin": d.get("DateBegSearch"),
                    "date_end": d.get("DateEndSearch"),
                    "remarks": _safe_str(d.get("Remarks")),
                })

            for an in alt_nums.get(oid, []):
                record["alt_numbers"].append({
                    "number": _safe_str(an.get("AltNum")),
                    "description": _safe_str(an.get("Description")),
                    "remarks": _safe_str(an.get("Remarks")),
                })

            for comp in components.get(oid, []):
                loc_id = comp.get("LocationID")
                record["components"].append({
                    "component_id": comp.get("ComponentID"),
                    "name": _safe_str(comp.get("ComponentName")),
                    "number": _safe_str(comp.get("ComponentNumber")),
                    "current_location": lookups["locations"].get(loc_id) if loc_id else None,
                    "home_location": lookups["locations"].get(comp.get("HomeLocationID")),
                    "trans_date": _iso(comp.get("TransDate")),
                    "handler": _safe_str(comp.get("Handler")),
                    "physical_description": _safe_str(comp.get("PhysDesc")),
                    "install_comments": _safe_str(comp.get("InstallComments")),
                    "storage_comments": _safe_str(comp.get("StorageComments")),
                })

            for d in dimensions.get(oid, []):
                record["dimensions"].append({
                    "type": lookups["dimension_types"].get(d.get("DimensionTypeID"), ""),
                    "value": _safe_float(d.get("Dimension")),
                    "unit": lookups["dimension_units"].get(d.get("PrimaryUnitID"), "cm"),
                    "display": _safe_str(d.get("DisplayDimensions")),
                    "description": _safe_str(d.get("DimDescription")),
                })

            for m in media.get(oid, []):
                file_path = None
                if m.get("FilePath") and m.get("FileName"):
                    file_path = f"{m['FilePath']}/{m['FileName']}".replace("\\", "/")
                record["media"].append({
                    "media_master_id": m.get("MediaMasterID"),
                    "rank": m.get("Rank"),
                    "primary_display": m.get("PrimaryDisplay"),
                    "description": _safe_str(m.get("MediaDescription")),
                    "copyright": _safe_str(m.get("Copyright")),
                    "public_access": m.get("PublicAccess"),
                    "file_name": _safe_str(m.get("FileName")),
                    "file_path": file_path,
                    "pixel_h": m.get("PixelH"),
                    "pixel_w": m.get("PixelW"),
                    "file_size": m.get("FileSize"),
                })

            for ex in exhibitions.get(oid, []):
                record["exhibitions"].append({
                    "exhibition_id": ex.get("ExhibitionID"),
                    "title": _safe_str(ex.get("ExhTitle")),
                    "begin_date": _iso(ex.get("BeginISODate")),
                    "end_date": _iso(ex.get("EndISODate")),
                    "section": _safe_str(ex.get("Section")),
                    "catalogue_number": _safe_str(ex.get("CatalogueNumber")),
                })

            for cond in conditions.get(oid, []):
                examiner = lookups["constituents"].get(cond.get("ExaminerID"), {})
                record["conditions"].append({
                    "condition_id": cond.get("ConditionID"),
                    "examiner": examiner.get("DisplayName"),
                    "survey_date": _iso(cond.get("SurveyISODate")),
                    "report_date": _iso(cond.get("ReportISODate")),
                    "overall_analysis": _safe_str(cond.get("OverallAnalysis")),
                    "remarks": _safe_str(cond.get("CondRemarks")),
                })

            for ins in insurance.get(oid, []):
                appraiser = lookups["constituents"].get(ins.get("AppraiserID"), {})
                record["insurance"].append({
                    "value": _safe_float(ins.get("Value")),
                    "date": _iso(ins.get("ValueISODate")),
                    "appraiser": appraiser.get("DisplayName"),
                })

            for te in text_entries.get(oid, []):
                record["text_entries"].append({
                    "text_type_id": te.get("TextTypeID"),
                    "text": _safe_str(te.get("TextEntry")),
                })

            for on in object_names.get(oid, []):
                record["object_names"].append({
                    "name": _safe_str(on.get("ObjectName")),
                    "name_type": lookups["object_name_types"].get(on.get("ObjectNameTypeID")),
                    "display_order": on.get("DisplayOrder"),
                })

            for tx in thes_xrefs.get(oid, []):
                record["vocabulary_terms"].append({
                    "xref_type": _safe_str(tx.get("ThesXrefType")),
                    "term": _safe_str(tx.get("Term")),
                    "term_id": tx.get("TermID"),
                    "term_master_id": tx.get("TermMasterID"),
                    "display_order": tx.get("DisplayOrder"),
                    "remarks": _safe_str(tx.get("Remarks")),
                })

            for uf in user_fields.get(oid, []):
                record["user_fields"].append({
                    "field": _safe_str(uf.get("UserFieldName")),
                    "group": _safe_str(uf.get("GroupName")),
                    "value": _safe_str(uf.get("FieldValue")),
                    "value_date": _safe_str(uf.get("ValueDate")),
                    "remarks": _safe_str(uf.get("ValueRemarks")),
                    "context_id": uf.get("ContextID"),
                })

            for g in geography.get(oid, []):
                entry = {k.lower(): _safe_str(g.get(k)) for k in (
                    "GeoCode", "Continent", "SubContinent", "Country", "Region",
                    "State", "County", "City", "Locale", "River", "Building",
                    "Excavation", "Locus", "Notes")}
                if any(entry.values()):
                    record["geography"].append(entry)

            for rf in references.get(oid, []):
                record["references"].append({
                    "reference_id": rf.get("ReferenceID"),
                    "heading": _safe_str(rf.get("Heading")),
                    "title": _safe_str(rf.get("Title")),
                    "citation": _safe_str(rf.get("BoilerText")),
                    "year_published": _safe_str(rf.get("YearPublished")),
                    "page_number": _safe_str(rf.get("PageNumber")),
                    "catalogue_number": _safe_str(rf.get("CatalogueNumber")),
                    "figure_number": _safe_str(rf.get("FigureNumber")),
                    "remarks": _safe_str(rf.get("Remarks")),
                })

            for side in (assoc_1, assoc_2):
                for a in side.get(oid, []):
                    rel = related_objects.get(a.get("RelatedID"), {})
                    record["related_objects"].append({
                        "relation": _safe_str(a.get("Relation")),
                        "related_object_id": a.get("RelatedID"),
                        "related_object_number": _safe_str(rel.get("ObjectNumber")),
                        "related_object_name": _safe_str(rel.get("ObjectName")),
                        "display_date": _safe_str(a.get("DisplayDate")),
                        "remarks": _safe_str(a.get("Remarks")),
                    })

            for st in sites.get(oid, []):
                record["sites"].append({
                    "site_name": _safe_str(st.get("SiteName")),
                    "site_number": _safe_str(st.get("SiteNumber")),
                    "subsites": [x for x in (_safe_str(st.get("Subsite1")),
                                             _safe_str(st.get("Subsite2")),
                                             _safe_str(st.get("Subsite3"))) if x],
                    "remarks": _safe_str(st.get("Remarks")),
                })

            yield record
