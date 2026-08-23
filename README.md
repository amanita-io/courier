# Courier

**Your data was always yours.**

Courier is open tooling for GLAM (galleries, libraries, archives, museums)
collections data. Each solution reads a proprietary collections management
system and hands your data back in an open, documented format — run by you,
on your own machines, against databases and APIs you already own.

Courier is independent software. It is not endorsed by, supported by, or
affiliated with any collections management system vendor.

## Solutions

| Directory | System | Status |
|-----------|--------|--------|
| [`tms/`](tms/) | TMS (The Museum System) — SQL Server | Export + media sync, tested against a real collection |
| `emu/` | Axiell EMu — via the IMu API | Planned |
| `adlib/` | Axiell Collections / Adlib — via the Adlib REST API | Planned |
| `mimsy/` | Axiell Mimsy XG — SQL | Planned |

## Contributing institutions

Each solution is built and validated with **contributing institutions** —
museums that provide read-only access to (or a backup of) their real
system, so the tooling is proven against production data, version drift,
and local customizations, not just vendor documentation.

The covenant:

- **Institutions contribute access and knowledge; everything built from
  it is open.** The extractors, schema notes, and format mappings that
  result are published for everyone.
- **Contributed data itself is never published.** Databases, backups,
  and exports stay in private validation harnesses; nothing from an
  institution's collection appears in this repository or its history.
- **Contributors are credited** on the solution they helped validate,
  and shape the canonical format while it is still forming.

If your institution runs one of the systems below — including the
planned ones — and wants a permanent, rerunnable export of your own
collection in the bargain, open an issue or write to
jonathan@amanita.io. See [CONTRIBUTING.md](CONTRIBUTING.md).

Courier continues a long, open tradition of museum-community
interoperability work — see [PRIOR_ART.md](PRIOR_ART.md).

Every solution follows the same rules:

- **Runs on your side.** You run it, with your credentials, against your
  own licensed system. Your data never passes through anyone else.
- **No vendor-schema modification.** Nothing is ever installed into the
  source database — no tables, no procedures, no jobs. Plain read (and,
  where a solution writes, plain DML) permissions only.
- **Open output.** Exports are documented NDJSON/CSV any system can import.

## Quick start (TMS)

```
cd tms
pip install -e ".[tds]"     # pure-Python driver, no ODBC install needed
courier-tms probe  --host <sql-server> --database <tms-db> --username ... --password ...
courier-tms export --host ... --database ... --password ... \
    --out collection.ndjson --media-manifest media.csv
```

See [`tms/`](tms/) for the full command reference.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Courier is independent software, not endorsed by or affiliated with any
collections management system vendor. Product names are used only to
identify the systems Courier's solutions read.
