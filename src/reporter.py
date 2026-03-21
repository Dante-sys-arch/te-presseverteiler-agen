"""Reporter module for writing simple user-facing Excel delta reports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


MAIN_COLUMNS = [
    "Medium",
    "Journalist",
    "Im_Master",
    "Im_Web_gefunden",
    "Externer_Hinweis",
    "Was_ist_anders",
    "Alter_Stand",
    "Neuer_Stand",
    "Quelle",
    "Webrecherche_durchgefuehrt",
    "LinkedIn_Hinweis",
    "Neues_Medium_Hinweis",
    "Gefunden_bei",
    "Quellenbasis",
    "Empfohlene_Aktion",
    "Pruefen",
    "Kommentar",
]


class Reporter:
    """Writes timestamped Excel reports."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir

    def write(
        self,
        delta_rows: list[dict],
        crawl_info: dict[str, list[object]],
        technical_rows: list[dict] | None = None,
        unscanned_rows: list[dict] | None = None,
        medium_recherche_detail_rows: list[dict] | None = None,
        matching_detail_rows: list[dict] | None = None,
        web_research_detail_rows: list[dict] | None = None,
    ) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.reports_dir / f"scan_{timestamp}.xlsx"

        with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
            main_df = pd.DataFrame(delta_rows)
            if main_df.empty:
                main_df = pd.DataFrame(columns=MAIN_COLUMNS)
            else:
                main_df = main_df.reindex(columns=MAIN_COLUMNS)
            main_df.to_excel(writer, sheet_name="Aenderungen_Master_vs_Web", index=False)

            optional_unscanned = unscanned_rows or []
            if optional_unscanned:
                unscanned_df = pd.DataFrame(optional_unscanned).reindex(columns=MAIN_COLUMNS)
                unscanned_df.to_excel(writer, sheet_name="Nicht_gepruefte_Master_Medien", index=False)

            tech_rows = technical_rows or []
            if not tech_rows:
                for medium, infos in crawl_info.items():
                    for info in infos:
                        tech_rows.append(
                            {
                                "medium": medium,
                                "source_type": getattr(info, "source_type", ""),
                                "source_name": getattr(info, "source_name", ""),
                                "url": getattr(info, "url", ""),
                                "status_code": getattr(info, "status_code", ""),
                                "snapshot_path": getattr(info, "snapshot_path", ""),
                                "error": getattr(info, "error", ""),
                            }
                        )
            pd.DataFrame(tech_rows).to_excel(writer, sheet_name="Technik", index=False)
            pd.DataFrame(tech_rows).to_excel(writer, sheet_name="Quellenpruefung", index=False)

            detail_columns = [
                "Medium",
                "Source_Type",
                "Source_Label",
                "URL",
                "Status_Code",
                "Erfolgreich_geladen",
                "Kontakte_extrahiert",
                "Anzahl_Kontakte",
                "Extraktionsart",
                "Bewertung_Quelle",
                "Fehler",
                "Kommentar",
            ]
            detail_df = pd.DataFrame(medium_recherche_detail_rows or []).reindex(columns=detail_columns)
            detail_df.to_excel(writer, sheet_name="Medium_Recherche_Detail", index=False)

            matching_detail_columns = [
                "Medium",
                "Master_Journalist",
                "Web_Treffer",
                "Match_Art",
                "Match_Staerke",
                "Grund",
                "Entscheidung",
            ]
            matching_detail_df = pd.DataFrame(matching_detail_rows or []).reindex(columns=matching_detail_columns)
            matching_detail_df.to_excel(writer, sheet_name="Matching_Detail", index=False)

            web_research_columns = [
                "Medium",
                "Journalist",
                "Suchstufe",
                "Quelle",
                "Treffer_ja_nein",
                "Treffertext_kurz",
                "Bewertung",
                "Kommentar",
            ]
            web_research_df = pd.DataFrame(web_research_detail_rows or []).reindex(columns=web_research_columns)
            web_research_df.to_excel(writer, sheet_name="Webrecherche_Detail", index=False)

        return report_path
