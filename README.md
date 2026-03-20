# TE Presseverteiler Agent

Automatisiertes Monitoring, Qualitätssicherung und Update der MASTER DACH/LI/LUX Kontaktliste.

## Ziel
Der Agent scannt stündlich 104 Medien-Impressumsseiten, erkennt Änderungen, gleicht sie mit der Master-Excel (data/master/MASTER_DACHLILUX.xlsx) ab und erstellt einen Excel-Report zur menschlichen Freigabe.

## Kernprinzip
Keine automatische Änderung der Master-Datei ohne explizite Freigabe.
Vor jedem Update wird ein Backup erstellt.

## Intelligente Recherche (neu)
- Pro Medium werden mehrere offizielle Quellentypen geprüft (Impressum, Redaktion, Team, Kontakt, Autoren-, Ressort- und interne Suchseiten) via `config/medium_profiles.yaml` (`known_paths`, `team_patterns`, `author_patterns`, `search_patterns`).
- Pro Journalist werden Namensvarianten (Umlaute, Bindestriche, Initialen) per domain-interner Recherche geprüft.
- Branchenquellen werden in `config/secondary_sources.yaml` konfiguriert und als zweite Ebene gewichtet (offiziell > Branchenquelle > schwacher Hinweis).

## Hauptbausteine
- Crawler
- Parser
- Diff-Engine
- Matcher
- Scorer
- Reporter
- Updater
- Pipeline

## Ausgabe
- Änderungsreport unter /reports
- Optionaler E-Mail-Versand
- Update der Master-Datei erst nach Approve/Reject
