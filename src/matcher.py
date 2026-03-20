"""Matcher module for master-vs-web delta assessments with source weighting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv

import pandas as pd
from rapidfuzz import fuzz

from validator import SourceCoverageAssessor


@dataclass(frozen=True)
class MandateRule:
    ressort_tag: str
    mandat: str


class Matcher:
    """Matches parsed official contacts + industry hints against the master file."""

    def __init__(self, mapping_file: Path, master_file: Path) -> None:
        self.mapping_file = mapping_file
        self.master_file = master_file
        self.coverage_assessor = SourceCoverageAssessor()

    def _compose_external_hint(self, hint_matches: list[dict]) -> str:
        if not hint_matches:
            return "kein externer Hinweis"
        sources = sorted({str(h.get("source", "")).strip() for h in hint_matches if str(h.get("source", "")).strip()})
        source_label = ", ".join(sources) if sources else "Branchenquelle"
        return f"Plausibler Wechselhinweis aus {source_label}"

    def _recommended_action(self, change_flags: list[str]) -> str:
        if "Medium nicht erreichbar" in change_flags:
            return "spaeter erneut pruefen oder URL korrigieren"
        if "Journalist bei Medium nicht mehr gefunden" in change_flags:
            return "deaktivieren oder manuell pruefen"
        if "Auf aktueller Quelle nicht belegt" in change_flags:
            return "manuell pruefen"
        if "Weitere Quelle pruefen" in change_flags:
            return "weitere Quelle pruefen"
        if "Wahrscheinlicher Medienwechsel" in change_flags:
            return "weitere Quelle pruefen"
        return "Keine Aktion"

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
            official_source_stats = payload.get("official_source_stats", {})
            hint_matches = self._find_industry_hints(master, all_industry_hints)
            official_match = self._find_official_match(master, official)
            coverage = self.coverage_assessor.assess(
                medium_status=medium_status,
                source_stats=official_source_stats,
                has_external_hint=bool(hint_matches),
            )

            change_flags: list[str] = []
            externer_hinweis = self._compose_external_hint(hint_matches)
            pruefen = "Nein"
            im_web = "Nein"
            neuer_stand = ""
            kommentar = coverage.comment

            if medium_status == "technisch_nicht_erreichbar":
                change_flags.append("Medium nicht erreichbar")
                pruefen = "Ja"
                im_web = "Unbekannt"
                kommentar = "Quelle technisch nicht erreichbar"
            elif official_match:
                im_web = "Ja"
                change_flags.append("Journalist bestaetigt")
                for field, label in (("email", "E-Mail geaendert"), ("telefon", "Telefon geaendert"), ("rolle", "Ressort geaendert")):
                    old = (master.get(field if field != "rolle" else "ressort", "") or "").strip().lower()
                    new = (official_match.get(field, "") or "").strip().lower()
                    if old and new and old != new:
                        change_flags.append(label)
                neuer_stand = f"{official_match.get('email', '')} | {official_match.get('telefon', '')} | {official_match.get('rolle', '')}"
                kommentar = "offizielle Quelle bestaetigt Abweichung" if len(change_flags) > 1 else "offizielle Quelle bestaetigt Kontakt"
            else:
                if hint_matches and coverage.level != "hoch":
                    change_flags.append("Wahrscheinlicher Medienwechsel")
                    pruefen = "Ja"
                elif coverage.level == "hoch":
                    change_flags.append("Journalist bei Medium nicht mehr gefunden")
                    pruefen = "Ja"
                    kommentar = "offizielle Quelle bestaetigt Abweichung"
                elif coverage.level == "mittel":
                    change_flags.append("Auf aktueller Quelle nicht belegt")
                    pruefen = "Ja"
                else:
                    change_flags.append("Weitere Quelle pruefen")
                    pruefen = "Ja"

            if hint_matches and official_match:
                change_flags.append("Weitere Quelle pruefen")
                pruefen = "Ja"
                kommentar = "offizielle Quelle bestaetigt Kontakt, externer Hinweis abweichend"

            source = "offizielle Mediumsquelle" if not hint_matches else ", ".join(sorted({h.get("source", "") for h in hint_matches if h.get("source")}))
            empfehlung = self._recommended_action(change_flags)

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
                    "kommentar": kommentar,
                }
            )
        return rows, not_scanned_rows

    # Backward-compatible method used by older flow/tests.
    def match(self, parsed_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        return parsed_data
