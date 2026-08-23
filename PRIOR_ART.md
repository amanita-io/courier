# Prior art

Courier documents and automates interoperability practices that the
museum community has performed and published openly for over a decade.
The table structures, field names, and query patterns used by Courier's
extractors describe factual characteristics of systems museums own and
operate, and have long been public knowledge, as the projects below
demonstrate. None of Courier's code is derived from these projects;
they are cited as evidence of established, open practice.

## TMS (The Museum System)

- **VlaamseKunstcollectie/tmssync** (Flemish Art Collection, 2016–2019)
  — synchronises a TMS SQL Server database to MySQL; publishes a
  configuration enumerating 35+ TMS tables including the thesaurus
  stack (Terms, TermMaster, ThesXrefs) and flex fields
  (UserFieldXrefs). https://github.com/VlaamseKunstcollectie/tmssync
- **artshumrc/giza** (Harvard University, Digital Giza — active) —
  extraction SQL over Objects, ConXrefs, UserFields, ObjGeography,
  RefXRefs, Associations, MediaRenditions, and cross-database Terms
  joins, feeding a public JSON API and IIIF manifests.
  https://github.com/artshumrc/giza
- **thedatahub/Datahub-Factory-Arthub** (KMSKA / Flemish Datahub) —
  production pipeline converting mirrored TMS data to LIDO XML for
  aggregation. https://github.com/thedatahub/Datahub-Factory-Arthub
- **Guggenheim/gugg-web_api-collection-db** (2013, MIT) — the
  Guggenheim's public collections-API data layer over TMS tables.
  https://github.com/seanredmond/gugg-web_api-collection-db
- **JeremieBastienMCQ/tms-api** (Musée de la civilisation, Québec,
  2016) — an independent REST API for The Museum System.
  https://github.com/JeremieBastienMCQ/tms-api
- **Shoilee/Bronbeek_Data_Conversion** (Museum Bronbeek / VU Amsterdam)
  — converts a TMS database backup to CSV and CIDOC-CRM/Linked Art RDF.
  https://github.com/Shoilee/Bronbeek_Data_Conversion
- **american-art/PUAM** (Princeton University Art Museum, American Art
  Collaborative) — semantic mappings from TMS data to CIDOC-CRM.
  https://github.com/american-art/PUAM
- **lyrasis/kiba-extend** (LYRASIS) — open migration ETL tooling whose
  documentation discusses TMS table structures in the context of
  CollectionSpace migrations. https://github.com/lyrasis/kiba-extend

This project's own lineage is continuous with that practice: its
predecessor `tms-api` was announced publicly to the Museum Computer
Network (MCN) community in January 2024, and descends from TMS
integration work running in museums since 2012.
