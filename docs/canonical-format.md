# The Courier canonical record

Courier reads a proprietary collections management system and writes one
**canonical record** per object. This document is the specification for that
record. It is what "open, documented format" means in the README.

## What this is, and what it is not

The canonical record is **Courier's own format**. It is not LIDO, not
CIDOC-CRM, not Linked Art, not Dublin Core, and not Schema.org. It borrows the
*shape* of linked data — `id`, `type`, `label` — but it carries no `@context`
and binds to no vocabulary. **It is not JSON-LD**, and should not be fed to a
JSON-LD processor.

That is deliberate. Standards are projections: LIDO and CIDOC-CRM describe
what the aggregator community agreed to exchange, which is less than what a
museum's database holds. A record that starts life as LIDO has already thrown
away the fields LIDO has no home for, and no later mapping can get them back.

So Courier's intermediate is **lossless first, standard second**. The
canonical record keeps everything, and standard serialisations are generated
*from* it. Those exporters — LIDO XML, IIIF manifests, Linked Art, CSVW — are
on the roadmap and are not built yet. Until they are, this format is the only
output, and you should treat it as an archival intermediate rather than
something to hand an aggregator.

## Framing

Exports are **newline-delimited JSON**: one complete JSON object per line,
UTF-8, no wrapping array, no trailing commas. Files stream, so a collection of
any size can be read without loading it into memory:

```python
import json
with open("collection.ndjson", encoding="utf-8") as f:
    for line in f:
        record = json.loads(line)
```

## The envelope

```json
{
  "id": "tms:1042",
  "type": "Object",
  "label": "Sunrise over the Harbour",
  "object_number": "1998.1.1",
  "source": { "system": "tms", "id": "1042" },
  "properties": { },
  "media": [ ],
  "raw": { }
}
```

| Key | Type | Always present | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | `<system>:<source id>`. Unique within an export, stable across re-runs as long as the source id is stable. |
| `type` | string | yes | Currently always `"Object"`. Reserved for future record kinds. |
| `label` | string | yes | Best human-readable name. First non-empty `ObjTitles` entry, else the object name, else the legacy title column, else `"Object <object_number>"`. Never empty. |
| `object_number` | string | yes | The accession number. Synthesised as `<SYSTEM>-<id>` when the source has none, so it is never empty. |
| `source` | object | yes | `{"system": "tms", "id": "<source id>"}` — provenance of the record. |
| `properties` | object | yes | Cleaned, resolved values. See below. |
| `media` | array | **no** | Canonical media references. Omitted entirely when the object has no media with a filename. |
| `raw` | object | **no** | The complete source record, untouched. Present only when the export was run with `--raw`. |

### Absence is meaningful

**`properties` omits keys rather than emitting nulls.** A key is dropped when
its value is `null`, an empty array, or an object whose every value is `null`.
Consumers must therefore use presence-tolerant access (`record["properties"].get("medium")`)
and must not assume any given key exists.

Two consequences worth stating plainly:

- **Absent means "the source had nothing here."** It does not mean the field
  is unsupported.
- **Falsy is not absent.** `public_access: 0` and `on_view: 0` are meaningful
  values and are preserved. Only `null` and empty containers are stripped.

One special case: this system records "no date" as `0`. `date_begin` and
`date_end` are dropped when they are `0`, rather than exported as year zero.

### Lossless only with `--raw`

`raw` carries the complete denormalised source record — every column the
extractor read, under its original names. It is the guarantee against lock-in:
an export made today can be remapped years from now without going back to the
museum's database.

**It is off by default.** `courier-tms export` omits `raw` unless you pass
`--raw`. A default export is a lossy projection; a `--raw` export is not. If
you are exporting for preservation or migration rather than for a one-off
downstream load, use `--raw`.

## `properties`

Scalars, all optional:

| Key | Type | Notes |
| --- | --- | --- |
| `object_number` | string | Same value as the envelope's. |
| `object_name` | string | |
| `classification` | string | Resolved from the source's own vocabulary. |
| `department` | string | Resolved. |
| `object_status` | string | Resolved. |
| `dated` | string | Display date as catalogued, e.g. `"c. 1890–95"`. |
| `date_begin`, `date_end` | integer | Search years. Dropped when `0`. |
| `medium` | string | |
| `credit_line` | string | |
| `description` | string | |
| `provenance` | string | |
| `signed`, `inscribed`, `markings` | string | |
| `dimensions_text` | string | The catalogued display dimensions. |
| `notes` | string | |
| `curatorial_remarks` | string | Frequently non-public. |
| `public_access` | integer | `0`/`1`. Preserved when `0`. |
| `on_view` | integer | `0`/`1`. Preserved when `0`. |
| `current_location` | string | First component location, formatted `Site > Room > Unit > Number > Position`. |

Collections, all optional and all arrays of objects unless noted:

| Key | Fields |
| --- | --- |
| `titles` | `title`, `title_type`, `display_order` |
| `creators` | `name`, `role`, `date` — the subset of constituents whose role is Artist, Maker, Author or Photographer |
| `all_constituents` | `constituent_id`, `display_name`, `first_name`, `last_name`, `institution`, `type`, `role`, `display_date`, `prefix`, `suffix`, `display_order`. `type` is the source's own term (`Individual`, `Institution`, `(not entered)`, or a local one), or `null` if unlisted. |
| `context` | *object, not array*: `culture`, `style`, `period`, `dynasty`, `movement`, `nationality`, `school` |
| `accession` | *object, not array*: `method`, `value`, `source`, `date`, `lot`, `justification`, `authorizer`, `deed_sent`, `deed_received` |
| `dates` | `event_type`, `date_text`, `date_begin`, `date_end`, `remarks` |
| `alt_numbers` | `number`, `description`, `remarks` |
| `components` | `component_id`, `name`, `number`, `current_location`, `home_location`, `trans_date`, `handler`, `physical_description`, `install_comments`, `storage_comments` |
| `dimensions` | `type`, `value`, `unit`, `display`, `description` |
| `media` | `media_master_id`, `rank`, `primary_display`, `description`, `copyright`, `public_access`, `file_name`, `file_path`, `pixel_h`, `pixel_w`, `file_size` |
| `exhibitions` | `exhibition_id`, `title`, `begin_date`, `end_date`, `section`, `catalogue_number` |
| `conditions` | `condition_id`, `examiner`, `survey_date`, `report_date`, `overall_analysis`, `remarks` |
| `insurance` | `value`, `date`, `appraiser` — **non-public in most institutions** |
| `text_entries` | `text_type_id`, `text` |
| `object_names` | `name`, `name_type`, `display_order` |
| `vocabulary_terms` | `xref_type`, `term`, `term_id`, `term_master_id`, `display_order`, `remarks`. `term` is `null` when the thesaurus database is unavailable; `term_id` is always exported so labels can be resolved later. |
| `user_fields` | `field`, `group`, `value`, `value_date`, `remarks`, `context_id` — local flex fields, names vary per institution |
| `geography` | `geocode`, `continent`, `subcontinent`, `country`, `region`, `state`, `county`, `city`, `locale`, `river`, `building`, `excavation`, `locus`, `notes` |
| `references` | `reference_id`, `heading`, `title`, `citation`, `year_published`, `page_number`, `catalogue_number`, `figure_number`, `remarks` |
| `related_objects` | `relation`, `related_object_id`, `related_object_number`, `related_object_name`, `display_date`, `remarks`. Both directions of a relationship are exported, each from the perspective of the record carrying it. |
| `sites` | `site_name`, `site_number`, `subsites` (array of strings), `remarks` |

## `media` (envelope)

A short reference list, distinct from the fuller `properties.media`:

```json
{ "id": "tms-media:88", "type": "image", "label": "1998.1.1_a.jpg", "role": "primary" }
```

`role` is `"primary"` or `"alternate"`. Entries with no filename are excluded,
because a media record with no file behind it is a broken link rather than an
image; those remain visible in `properties.media` and in `raw`.

## Value conventions

- **Strings** are trimmed; empty strings become `null` and are then stripped.
- **Dates** are ISO 8601 strings as the source stored them. They are not
  reformatted, validated, or timezone-adjusted.
- **Numbers** that the source uses `0` for to mean "unset" are stripped where
  the meaning is unambiguous (`date_begin`, `date_end`, and numeric dimension
  values).
- **Binary** values, should any appear, are hex-encoded strings.

## The media manifest

`--media-manifest` writes a CSV alongside the export listing every media file
the export references, for copying the files themselves:

`object_id`, `object_number`, `media_master_id`, `file_name`, `file_path`,
`pixel_w`, `pixel_h`, `file_size`, `primary`

`file_path` joins the stored path and filename with forward slashes. It is the
path as the source recorded it, which may be a UNC or drive-letter path that
means nothing on the machine you are running on.

## Handle exports carefully

A canonical record contains whatever the extractor could read, which for most
institutions includes fields never meant to be public: valuations, donor
identities, storage locations, curatorial notes. Treat an export with the same
care as a database backup. See [SECURITY.md](../SECURITY.md).

## Stability

Courier is pre-1.0 and **this format will change**. Field names, nesting and
omission rules are not yet frozen, and the contributing-institution programme
exists partly to shape them. Pin a version if you build against it.

The machine-readable form of this document is
[`canonical-record.schema.json`](canonical-record.schema.json).
