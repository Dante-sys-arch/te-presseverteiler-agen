"""Matcher module for master-vs-web delta assessments with source weighting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv

import pandas as pd
from rapidfuzz import fuzz


@dataclass(frozen=True)
class MandateRule:
    ressort_tag: str
    mandat: str


class Matcher:
    """Matches parsed official contacts + industry hints against the master file."""

    def __init__(self, mapping_file: Path, master_file: Path) -> None:
        self.mapping_file = mapping_file
        self.master_file = master_file

    def load_rules(self) -> list[MandateRule]:
        if not self.mapping_file.exists():
            return []
        with self.mapping_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [MandateRule(ressort_tag=(r.get("ressort_tag") or "").strip(), mandat=(r.get("mandat") or "").strip()) for r in reader if (r.get("ressort_tag") or "").strip()]

    def load_master_contacts(self) -> list[dict[str, str]]:
        if not self.master_file.exists():
            return []
        sheets = pd.read_excel(self.master_file, sheet_name=None, header=None)
        contacts: list[dict[str, str]] = []
        for _, frame in sheets.items():
            for _, row in frame.iterrows():
                values = ["" if pd.isna(v) else str(v).strip() for v in row.tolist()]
                if len(values) < 5 or "@" not in values[4]:
                    continue
                contacts.append(
                    {
                        "medium": values[0],
                        "anrede": values[1],
                        "vorname": values[2],
                        "nachname": values[3],
                        "email": values[4].lower(),
                        "ressort": values[7] if len(values) > 7 else "",
                        "telefon": values[8] if len(values) > 8 else "",
                    }
                )
        return contacts

    def _name(self, row: dict) -> str:
        return f"{row.get('vorname', '')} {row.get('nachname', '')}".strip()

    def _find_official_match(self, master: dict, official_contacts: list[dict]) -> dict | None:
        email = master.get("email", "").lower()
        name = self._name(master).lower()
        for record in official_contacts:
            if record.get("email", "").lower() == email:
                return record

        best_score = 0
        best: dict | None = None
        for record in official_contacts:
            candidate = self._name(record).lower()
            score = fuzz.ratio(name, candidate)
            if score > best_score:
                best_score = score
                best = record
        return best if best_score >= 92 else None

    def _find_industry_hints(self, master: dict, industry_hints: list[dict]) -> list[dict]:
        name = self._name(master).lower()
        out: list[dict] = []
        for hint in industry_hints:
            journalist = str(hint.get("journalist", "")).lower()
            if not journalist:
                continue
            if fuzz.ratio(name, journalist) >= 92:
                out.append(hint)
        return out

    def build_delta_inputs(
        self,
        parsed_structured: dict[str, dict],
        scan_scope_media: set[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        master_contacts = self.load_master_contacts()
        all_industry_hints: list[dict] = []
        for payload in parsed_structured.values():
            all_industry_hints.extend(payload.get("industry_hints", []))

        scoped_media = scan_scope_media
        rows: list[dict] = []
        not_scanned_rows: list[dict] = []
        for master in master_contacts:
            medium = master.get("medium", "")
            if scoped_media is not None and medium not in scoped_media:
                not_scanned_rows.append(
                    {
                        "medium": medium,
                        "journalist": self._name(master),
                        "im_master": "Ja",
                        "im_web_gefunden": "",
                        "externer_hinweis": "",
                        "was_ist_anders": "Nicht im aktuellen Scan-Scope",
                        "alter_stand": f"{master.get('email', '')} | {master.get('telefon', '')} | {master.get('ressort', '')}",
                        "neuer_stand": "",
                        "quelle": "",
                        "empfohlene_aktion": "Keine Aktion",
                        "pruefen": "Nein",
                        "kommentar": "",
                    }
                )
                continue
            payload = parsed_structured.get(medium, {})
            official = payload.get("official_contacts", [])
            medium_status = payload.get("medium_status", "ok")
            hint_matches = self._find_industry_hints(master, all_industry_hints)
            official_match = self._find_official_match(master, official)

            change_flags: list[str] = []
            externer_hinweis = "kein externer Hinweis"
            pruefen = "Nein"
            empfehlung = "Keine Aktion"
            im_web = "Nein"
            neuer_stand = ""

            if medium_status == "technisch_nicht_erreichbar":
                change_flags.append("Medium_nicht_erreichbar")
                empfehlung = "Erneut prüfen"
                pruefen = "Ja"
                im_web = "Unbekannt"
            elif official_match:
                im_web = "Ja"
                change_flags.append("Journalist bei Medium bestätigt")
                for field, label in (("email", "E-Mail geändert"), ("telefon", "Telefon geändert"), ("rolle", "Ressort geändert")):
                    old = (master.get(field if field != "rolle" else "ressort", "") or "").strip().lower()
                    new = (official_match.get(field, "") or "").strip().lower()
                    if old and new and old != new:
                        change_flags.append(label)
                neuer_stand = f"{official_match.get('email', '')} | {official_match.get('telefon', '')} | {official_match.get('rolle', '')}"
            else:
                change_flags.append("Journalist bei Medium nicht mehr gefunden")
                empfehlung = "Prüfen"
                pruefen = "Ja"

            if medium_status == "wahrscheinlich_nicht_mehr_aktiv":
                change_flags.append("Medium wahrscheinlich nicht mehr aktiv")
                empfehlung = "Mediumstatus prüfen"
                pruefen = "Ja"

            if hint_matches:
                externer_hinweis = "Wechsel in Branchenquelle gemeldet"
                hint_sources = sorted({h.get("source", "") for h in hint_matches if h.get("source")})
                if official_match:
                    externer_hinweis = "Widerspruch zwischen offizieller Quelle und Branchenquelle"
                    pruefen = "Ja"
                    empfehlung = "Widerspruch klären"
                elif not official_match:
                    change_flags.append("wahrscheinlicher Medienwechsel")
                    empfehlung = "Kontakt manuell verifizieren"
                    pruefen = "Ja"
                source = ", ".join(hint_sources)
            else:
                source = "offizielle Mediumsquelle"

            rows.append(
                {
                    "medium": medium,
                    "journalist": self._name(master),
                    "im_master": "Ja",
                    "im_web_gefunden": im_web,
                    "externer_hinweis": externer_hinweis,
                    "was_ist_anders": "; ".join(dict.fromkeys(change_flags)),
                    "alter_stand": f"{master.get('email', '')} | {master.get('telefon', '')} | {master.get('ressort', '')}",
                    "neuer_stand": neuer_stand,
                    "quelle": source,
                    "empfohlene_aktion": empfehlung,
                    "pruefen": pruefen,
                    "kommentar": "",
                }
            )
        return rows, not_scanned_rows

    # Backward-compatible method used by older flow/tests.
    def match(self, parsed_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        return parsed_data
