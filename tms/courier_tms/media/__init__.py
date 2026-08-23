"""Media sync: push DAMS/media metadata into TMS media tables.

Python translation of the legacy T-SQL pipeline (legacy/database/).
The original required installing four custom tables, six stored
procedures, four functions, and a SQL Agent job INTO the museum's TMS
database — i.e. DDL rights and vendor-schema modification. This
package keeps all of that logic client-side:

- JSON parsing:        json.loads          (was parse_json.sql, 331 lines)
- staging + sync state: local SQLite file  (was media_staging + 3 log tables)
- lookups/renditions:  lookups.py          (was 4 functions + 3 procedures)
- process loop:        sync.py             (was process_media.sql cursor)

The only thing that touches the TMS database is plain DML against the
standard TMS media tables (MediaMaster, MediaRenditions, MediaFiles,
MediaXrefs, plus auto-created rows in MediaPaths/MediaStatuses/
Departments) — the same writes the original procedures performed, with
per-record transactions. No objects are ever created in the TMS
database.
"""

from .models import MediaAsset
from .state import SyncState
from .sync import MediaSync

__all__ = ["MediaAsset", "SyncState", "MediaSync"]
