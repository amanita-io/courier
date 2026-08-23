# courier-tms tests

```bash
pip install -e ".[dev]"
pytest                                  # unit tests only; live tests skip
COURIER_TMS_PASSWORD='...' pytest       # + live tests against a TMS database
```

## Two kinds of test

**Unit tests** — `test_db`, `test_models`, `test_state`, `test_lookups`,
`test_canonical` — need no database and run in well under a second. They
pin the behaviour ported from the legacy T-SQL: the 5th-slash path rule,
`'true'`/`'false'` string booleans, the INSERT/UPDATE/DELETE/IGNORE
decision, rendition numbering, pytds `%`-escaping and `Binary` wrapping.
Several of them pin quirks rather than ideals — where the original did
something surprising, the comment says so, because a "fix" would put
courier's writes out of step with a museum's existing media records.

**Live tests** — `test_extract`, `test_media_sync` — run against a real
TMS database and are skipped unless connection details are supplied, so
a bare `pytest` on a fresh clone passes.

## Pointing the live tests at a database

Either environment variables or command-line options:

| Option | Environment variable | Default |
| --- | --- | --- |
| `--tms-host` | `COURIER_TMS_HOST` | `localhost` |
| `--tms-port` | `COURIER_TMS_PORT` | `1433` |
| `--tms-database` | `COURIER_TMS_DATABASE` | `tms` |
| `--tms-username` | `COURIER_TMS_USERNAME` | `sa` |
| `--tms-password` | `COURIER_TMS_PASSWORD` | *(none — live tests skip)* |
| `--tms-driver` | `COURIER_TMS_DRIVER` | `pytds` |
| `--tms-allow-create-db` | `COURIER_TMS_ALLOW_CREATE_DB` | off |

There is no default password on purpose: without one the live tests skip
rather than connecting to something you did not mean to write to.

Against the docker validation harness:

```bash
COURIER_TMS_PASSWORD='...' pytest --tms-allow-create-db
```

## What makes running these against a real database safe

The live tests write. Four things keep that safe, and each is asserted
rather than assumed — these tests are the evidence for the promise that
courier does not disturb the database it runs against.

1. **Everything is marked.** Every row the suite writes carries
   `LoginID='courier-test'`, and is deleted by that marker before and
   after each test. Residue is then verified with a per-table `COUNT(*)`
   — never with a driver rowcount.
2. **The museum's own data is fingerprinted.** Before the session, every
   row *not* carrying the marker is snapshotted, along with every table,
   view, procedure, function and trigger in the database. Both are
   compared again at the end. A single flipped flag on one row out of
   hundreds fails the run, and so does any DDL.
3. **Test media hangs off a canary object.** Media sync demotes other
   primaries on insert and promotes a survivor on delete. Both are
   scoped to one object, so the suite picks — at runtime — an object
   that has no media of its own, putting those statements out of reach
   of any pre-existing row.
4. **No DDL against TMS.** One test needs a second database to exercise
   the cross-database thesaurus join. It creates and drops a scratch one
   outside TMS, and only when `--tms-allow-create-db` is given.

Nothing here hard-codes an identifier from any real collection. The
canary object, the objects the extract tests read, and the related
object used for associations are all discovered at runtime, and the
assertions are on shape and invariants rather than content — so the
suite passes against any TMS database, and no museum's data ends up in
this repository.

## Markers

| Marker | Meaning |
| --- | --- |
| `live` | needs a TMS database |
| `slow` | full-collection pass; minutes on a large collection |
| `creates_database` | creates and drops a scratch database |

```bash
pytest -m "not slow"      # skip the full-collection pass
pytest -m "not live"      # unit tests only, even with a database configured
```

## Coverage gaps worth knowing about

- **`MediaMaster.DepartmentID`.** TMS 9.1 does not have the column;
  later versions do. The sync detects which, and
  `test_department_handling_matches_this_databases_schema` asserts the
  detection agrees with `INFORMATION_SCHEMA` — but only one branch runs
  on any given database. The other needs a fixture from a TMS version
  that has the column.
- **Auxiliary tables.** `ThesXrefs`, `UserFieldXrefs`, `ObjGeography`,
  `RefXRefs`, `Associations`, `SiteObjXrefs` and `TextEntries` are
  usually empty in a restored backup, so `test_auxiliary_tables_are_mapped`
  seeds a marked row into each to make the mapping code run. That proves
  the mapping, not that it matches what a real, populated TMS holds.
- **pyodbc.** The live tests default to `pytds`. Passing
  `--tms-driver pyodbc` runs the same tests through the ODBC path,
  wherever the Microsoft driver is installed.
