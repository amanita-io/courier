# courier-tms

Courier solution for **TMS (The Museum System)** SQL Server databases.
Runs on your side, against your own licensed TMS, with your credentials.
Never installs anything into the TMS database; never phones home.

_Independent software — not endorsed by or affiliated with any collections
management system vendor._

## Install

```
pip install -e ".[tds]"    # pure-Python driver, works anywhere pip works
pip install -e ".[odbc]"   # or: pyodbc, requires MS ODBC Driver 17/18
```

## Commands

### `courier-tms probe` — connect and report
Object count and lookup-table coverage. Read-only.

### `courier-tms export` — your collection as open data
Walks the full object graph (titles, constituents, context, accession,
dates, alternate numbers, components + locations, dimensions, media,
exhibitions, conditions, insurance, text entries) and writes one
canonical JSON record per line. Read-only, batched for collections of
any size.

```
courier-tms export --host ... --database ... --username ... --password ... \
    --out collection.ndjson --media-manifest media.csv [--raw] [--limit N]
```

`--raw` embeds the untouched TMS source record in each envelope so the
export is lossless. `--media-manifest` writes a CSV of every media file
referenced, for copying the files themselves.

### `courier-tms media sync` — DAMS feed → TMS media records
Faithful Python translation of a production T-SQL pipeline (see
`legacy/database/`). Takes a JSON feed of assets and creates/updates/
deletes TMS media records (MediaMaster → renditions → files → xrefs),
with sync state kept in a local SQLite file — nothing is ever staged
inside TMS. Writes via plain DML only; per-asset transactions.

```
courier-tms media sync --host ... --database ... --password ... \
    --input assets.json [--state courier-tms-state.sqlite] [--dry-run]
```

## Tests

```bash
pip install -e ".[dev]"
pytest                                  # unit tests; live tests skip
COURIER_TMS_PASSWORD='...' pytest       # + live tests against a TMS database
```

Live tests write to the database they are pointed at, and prove they
left it as they found it: every row they create is marked and deleted
by that marker, and the museum's own rows — plus the entire schema —
are fingerprinted before and after the run. See `tests/README.md`.

## History

This code descends from TMS integration work running in museums since
2012. The `legacy/` directory preserves the original T-SQL pipeline and
early API scaffolding for reference.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
