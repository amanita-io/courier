"""courier-tms command-line interface.

    courier-tms export --host localhost --database tms --username sa --password ...
    courier-tms export ... --out collection.ndjson --media-manifest media.csv
    courier-tms probe  ...   # connect, count objects, list schema coverage

Exports are newline-delimited JSON (one canonical record per line) so
that collections of any size stream without memory pressure, plus an
optional CSV manifest of every media file referenced by the export.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from . import __version__
from .canonical import normalize
from .db import TMSConnection
from .extract import TMSExtractor

logger = logging.getLogger(__name__)


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


def _add_connection_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", required=True, help="SQL Server host")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--database", required=True, help="TMS database name")
    p.add_argument("--username", default="sa")
    p.add_argument("--password", required=True)
    p.add_argument(
        "--driver",
        choices=["pyodbc", "pytds"],
        default=None,
        help="Force a specific driver (default: try pyodbc, then pytds)",
    )
    p.add_argument(
        "--thes-database",
        default=None,
        help="Name of the TMS thesaurus database for term labels "
        "(default: auto-detect a database named like %%thes%%)",
    )
    p.add_argument(
        "--odbc-driver",
        default="ODBC Driver 18 for SQL Server",
        help="ODBC driver name when using pyodbc",
    )


def _connect(args: argparse.Namespace) -> TMSConnection:
    conn = TMSConnection(
        host=args.host,
        port=args.port,
        database=args.database,
        username=args.username,
        password=args.password,
        driver=args.driver,
        odbc_driver=args.odbc_driver,
    )
    conn.connect()
    return conn


def cmd_probe(args: argparse.Namespace) -> int:
    conn = _connect(args)
    try:
        extractor = TMSExtractor(conn, thes_database=args.thes_database)
        n = extractor.count_objects()
        lookups = extractor.load_lookups()
        print(f"Connected to {args.host}/{args.database}")
        print(f"Objects: {n}")
        print(f"Thesaurus database: {extractor.thes_database or 'not found (term labels unavailable, TermIDs still exported)'}")
        for name, table in sorted(lookups.items()):
            print(f"  {name}: {len(table)} entries")
        return 0
    finally:
        conn.close()


def cmd_export(args: argparse.Namespace) -> int:
    conn = _connect(args)
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    media_rows: list[dict] = []
    count = 0
    try:
        extractor = TMSExtractor(conn, thes_database=args.thes_database)
        total = extractor.count_objects()
        logger.info("Exporting %s objects from %s/%s", total, args.host, args.database)
        if not args.institution:
            logger.warning(
                "No --institution given: this export will not record which "
                "organisation it came from, and that cannot be added later."
            )
        for record in extractor.extract(limit=args.limit):
            envelope = normalize(record, institution=args.institution)
            if not args.raw:
                envelope.pop("raw", None)
            out.write(json.dumps(envelope, default=_json_default, ensure_ascii=False))
            out.write("\n")
            count += 1
            if args.media_manifest:
                for m in record.get("media", []):
                    if m.get("file_name"):
                        media_rows.append({
                            "object_id": record["ObjectID"],
                            "object_number": record.get("ObjectNumber") or "",
                            "media_master_id": m.get("media_master_id") or "",
                            "file_name": m.get("file_name") or "",
                            "file_path": m.get("file_path") or "",
                            "pixel_w": m.get("pixel_w") or "",
                            "pixel_h": m.get("pixel_h") or "",
                            "file_size": m.get("file_size") or "",
                            "primary": 1 if m.get("primary_display") else 0,
                        })
            if count % 1000 == 0:
                logger.info("  %s/%s", count, total)
    finally:
        if out is not sys.stdout:
            out.close()
        conn.close()

    if args.media_manifest and media_rows:
        with open(args.media_manifest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(media_rows[0].keys()))
            writer.writeheader()
            writer.writerows(media_rows)
        logger.info("Media manifest: %s (%s files)", args.media_manifest, len(media_rows))

    logger.info("Exported %s records", count)
    return 0


def cmd_media_sync(args: argparse.Namespace) -> int:
    from .media import MediaAsset, MediaSync, SyncState

    with open(args.input, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("assets", [raw])
    assets = [MediaAsset.from_json(o) for o in raw]

    conn = _connect(args)
    state = SyncState(args.state)
    try:
        sync = MediaSync(conn, state, login_id=args.login_id)
        result = sync.process(assets, dry_run=args.dry_run)
    finally:
        state.close()
        conn.close()

    print(
        f"total={result.total} inserted={result.inserted} updated={result.updated} "
        f"deleted={result.deleted} ignored={result.ignored} failed={result.failed}"
        + (" (dry run)" if args.dry_run else "")
    )
    for e in result.errors:
        print(f"  ERROR {e}", file=sys.stderr)
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="courier-tms",
        description="Independent open tooling for TMS databases. Read-only. "
        "Not endorsed by or affiliated with any collections management system vendor.",
    )
    parser.add_argument("--version", action="version", version=f"courier-tms {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="Connect and report object count + lookup coverage")
    _add_connection_args(p_probe)
    p_probe.set_defaults(func=cmd_probe)

    p_export = sub.add_parser("export", help="Export all objects as canonical NDJSON")
    _add_connection_args(p_export)
    p_export.add_argument(
        "--institution",
        help="Name of the organisation these records belong to, recorded in "
        "each envelope. Strongly recommended: nothing else in the file "
        "identifies the source, and it cannot be added after the fact.",
    )
    p_export.add_argument("--out", help="Output file (default: stdout)")
    p_export.add_argument("--limit", type=int, default=None, help="Export at most N objects")
    p_export.add_argument(
        "--raw",
        action="store_true",
        help="Include the full raw TMS record in each envelope (larger files, lossless)",
    )
    p_export.add_argument(
        "--media-manifest",
        help="Also write a CSV manifest of every media file referenced by the export",
    )
    p_export.set_defaults(func=cmd_export)

    p_media = sub.add_parser("media", help="Media operations")
    media_sub = p_media.add_subparsers(dest="media_command", required=True)
    p_sync = media_sub.add_parser(
        "sync",
        help="Sync media metadata from a JSON feed into TMS media tables (writes to TMS)",
    )
    _add_connection_args(p_sync)
    p_sync.add_argument("--input", required=True, help="JSON file: array of asset objects")
    p_sync.add_argument(
        "--state",
        default="courier-tms-state.sqlite",
        help="Local SQLite sync-state file (default: ./courier-tms-state.sqlite)",
    )
    p_sync.add_argument(
        "--login-id",
        default="courier",
        help="LoginID stamped on rows this tool creates in TMS (default: courier)",
    )
    p_sync.add_argument(
        "--dry-run", action="store_true", help="Report planned actions without writing"
    )
    p_sync.set_defaults(func=cmd_media_sync)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if not args.verbose:
        logging.getLogger("pytds").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
