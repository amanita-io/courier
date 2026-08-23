# Contributing to Courier

## Contributing institutions

The most valuable contribution to Courier is not code — it is access.
Every collections management system deployment drifts: versions,
schema customizations, flex-field conventions, local vocabularies. A
solution is only trustworthy after it has run against real systems.

A contributing institution provides any of:

- a **read-only connection** to a test or reporting copy of their CMS,
- a **database backup** we restore into a private validation harness,
- or an hour of a registrar's time reviewing whether an export
  preserved what matters.

In return, the institution gets:

- a free, permanent, rerunnable export of its own collection in the
  open canonical format (media manifest included),
- credit as a contributing institution on the solution it validated,
- direct influence on the canonical format while it is still forming.

**What never happens:** contributed databases, backups, or exports are
never committed to this repository, never published, and never shared
between institutions. Validation harnesses are private; only the
tooling and generalized schema knowledge they produce are open.

## Code contributions

Standard flow — issues and pull requests welcome. Ground rules:

- Extractors are **read-only** against source systems (the TMS media
  sync writes only via plain DML, per-record transactions, and is the
  documented exception).
- No solution may require installing anything into the source
  database — no tables, procedures, triggers, or jobs.
- No vendor-proprietary material in the repo: no schema documentation
  PDFs, no vendor SQL, no sample databases, no code derived from a
  vendor's or another company's private work.
- Pure-Python (or otherwise dependency-light) driver options are
  preferred, so museum IT can run tools without system installs.

## Independence

Courier is independent software, not endorsed by or affiliated with
any collections management system vendor. It reads data museums
already own, with credentials they already hold.
