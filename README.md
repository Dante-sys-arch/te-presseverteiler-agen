# TE Presseverteiler Agent

Automatisiertes Monitoring, Qualitätssicherung und Update der MASTER DACH/LI/LUX Kontaktliste.

## Ziel
Der Agent scannt stündlich 104 Medien-Impressumsseiten, erkennt Änderungen, gleicht sie mit der Master-Excel (data/master/MASTER_DACHLILUX.xlsx) ab und erstellt einen Excel-Report zur menschlichen Freigabe.

## Kernprinzip
Keine automatische Änderung der Master-Datei ohne explizite Freigabe.
Vor jedem Update wird ein Backup erstellt.

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
