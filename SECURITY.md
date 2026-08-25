# Security

## Reporting a vulnerability

Email **jonathan@amanita.io**, or open a private security advisory through
GitHub on this repository. Please do not open a public issue for a
vulnerability.

Include what you found, how to reproduce it, and what it exposes. You will
get an acknowledgement within a week. If the report is valid, you will be
told what the fix is and when it ships, and credited in the release notes
unless you would rather not be.

Courier is pre-1.0. Fixes land on `main`; there are no backported patch
branches yet.

## What Courier does with your data

Courier connects to a database you already own, using credentials you
already hold, and writes files to a directory you choose. These properties
are load-bearing, and the test suite asserts them rather than assuming
them:

- **Extractors are read-only.** Every query the export path issues is a
  `SELECT`.
- **Media sync writes plain DML only** — `INSERT`, `UPDATE`, `DELETE`
  against the standard TMS media tables, one transaction per asset, rolled
  back on failure.
- **Nothing is installed into the source database.** No tables, no
  procedures, no triggers, no jobs, no schema changes of any kind. The
  live test suite fingerprints every table, view, procedure, function and
  trigger before and after a run and fails if any of them change.
- **No network egress.** Courier makes no outbound connections other than
  to the database you point it at. No telemetry, no update checks, no
  analytics, no phoning home. The package has zero required dependencies;
  the only optional ones are the two SQL Server drivers.
- **Sync state stays local**, in a SQLite file next to wherever you run
  the tool. The source database is never used to store tool state.

## Known limitations

**Credentials are passed on the command line.** `--password` is currently
the only way to supply one, which means it is visible in `ps` output and
lands in shell history. On a shared machine, treat that as an exposure:
prefer a dedicated read-only account, rotate it after a migration project,
and clear your history. Environment-variable and prompt-based credential
input are planned.

**Exports contain your collection in the clear.** NDJSON and CSV output
carry everything the extractor read, which for most institutions includes
non-public fields — valuations, donor details, locations, curatorial
notes. Treat output files with the same care as a database backup. The
repository's `.gitignore` blocks `*.ndjson`, `*.csv`, `*.sqlite` and
`*.bak` so they cannot be committed by accident; keep it that way.

**Use a read-only account for export.** Courier will work with an
over-privileged login, but nothing in the export path needs more than
`SELECT`. Media sync additionally needs `INSERT`, `UPDATE` and `DELETE` on
the media tables — and nothing beyond that.

## Dependencies

Courier depends on the Python standard library plus, optionally, one SQL
Server driver: `pyodbc` or `python-tds`. Report vulnerabilities in those
to their maintainers; report anything about how Courier uses them here.
