# Legacy

Original artifacts preserved for reference:

- `database/` — the T-SQL media pipeline this project began as. It required
  installing custom tables, procedures, functions, and a SQL Agent job into
  the museum's TMS database. It has been fully translated into client-side
  Python (`courier_tms/media/`), which needs only plain DML permissions and
  never creates objects in the TMS database. Kept as the reference
  implementation; do not install.
- `api.py`, `routes/`, `config.py`, `log.py`, `utilities.py` — early Flask
  API scaffolding, superseded by the `courier-tms` CLI. Retained as roadmap
  reference for a future read API.
