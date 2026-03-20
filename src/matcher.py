"""Matcher module for master-vs-web delta assessments with source weighting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
from collections import defaultdict
import re
import unicodedata

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

    def _normalize_name(self, value: str) -> str:
        txt = unicodedata.normalize("NFKD", str(value or "").strip().lower())
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        txt = re.sub(r"\b(dr|prof|dipl|mag)\.?\b", "", txt)
        txt = re.sub(r"[^a-z0-9\s-]", " ", txt)
        txt = txt.replace("ae", "a").replace("oe", "o").replace("ue", "u")
        return re.sub(r"\s+", " ", txt).strip()

    def _name_variants(self, full_name: str) -> set[str]:
        normalized = self._normalize_name(full_name)
        parts = [p for p in re.split(r"[\s\-]+", normalized) if p]
        if len(parts) < 2:
            return {normalized}
        first, last = parts[0], parts[-1]
        variants = {
            normalized,
            normalized.replace("-", " "),
            f"{first} {last}",
            f"{first}-{last}",
            f"{first[0]} {last}",
            f"{first[0]}. {last}",
            f"{last}, {first}",
        }
        replace_map = {"ae": "ä", "oe": "ö", "ue": "ü", "ss": "ß"}
        for candidate in list(variants):
            remapped = candidate
            for src, dest in replace_map.items():
                remapped = remapped.replace(src, dest)
            variants.add(self._normalize_name(remapped))
        return {v for v in variants if v}

    def _official_evidence(self, master_name: str, documents: list[dict]) -> dict[str, list[str]]:
        variants = self._name_variants(master_name)
        evidence: dict[str, list[str]] = defaultdict(list)
        for doc in documents:
            haystack = self._normalize_name(doc.get("text", ""))
            if not haystack:
                continue
            for variant in variants:
                if variant and variant in haystack:
                    page_type = doc.get("page_type", "") or "other"
                    evidence[page_type].append(doc.get("url", ""))
                    break
        return evidence

    def _industry_mentions(self, master_name: str, documents: list[dict]) -> list[dict]:
        variants = self._name_variants(master_name)
        found: list[dict] = []
        for doc in documents:
            haystack = self._normalize_name(doc.get("text", ""))
            if any(variant in haystack for variant in variants if variant):
                found.append(doc)
        return found

    def _recommended_action(self, change_flags: list[str]) -> str:
        if "Medium nicht erreichbar" in change_flags:
            return "spaeter erneut pruefen oder URL korrigieren"
        if "bei anderem Medium gefunden" in change_flags:
            return "zuordnung auf neues Medium pruefen"
        if "auf offiziellen Seiten nicht bestaetigt" in change_flags:
            return "deaktivieren oder manuell pruefen"
        if "Wechsel in Branchenquelle gemeldet" in change_flags:
            return "manuell pruefen"
        if "nur schwacher Webhinweis" in change_flags:
            return "weitere Quelle pruefen"
        if "weitere Pruefung noetig" in change_flags:
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
        master_by_medium: dict[str, list[dict]] = defaultdict(list)
        for contact in master_contacts:
            master_by_medium[contact.get("medium", "")].append(contact)

        all_industry_hints: list[dict] = []
        for payload in parsed_structured.values():
            all_industry_hints.extend(payload.get("industry_hints", []))

        scoped_media = scan_scope_media
        rows: list[dict] = []
        not_scanned_rows: list[dict] = []
        for medium, medium_master_contacts in master_by_medium.items():
            if scoped_media is not None and medium not in scoped_media:
                for master in medium_master_contacts:
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
            official_documents = payload.get("official_documents", [])
            medium_status = payload.get("medium_status", "ok")
            official_source_stats = payload.get("official_source_stats", {})
            has_expected_contact_source = int(official_source_stats.get("contact_expected_sources", 0) or 0) > 0

            coverage = self.coverage_assessor.assess(
                medium_status=medium_status,
                source_stats=official_source_stats,
                has_external_hint=False,
            )
            aggregated_hints = [
                hint
                for master in medium_master_contacts
                for hint in self._find_industry_hints(master, all_industry_hints)
            ]
            weak_medium_only = coverage.level == "niedrig" or (
                coverage.level == "mittel" and not has_expected_contact_source
            )

            if medium_status == "technisch_nicht_erreichbar":
                rows.append(
                    {
                        "medium": medium,
                        "journalist": "(Medium-Ebene)",
                        "im_master": "Ja",
                        "im_web_gefunden": "Unbekannt",
                        "externer_hinweis": self._compose_external_hint(aggregated_hints),
                        "was_ist_anders": "Medium nicht erreichbar",
                        "alter_stand": "",
                        "neuer_stand": "",
                        "quelle": "offizielle Mediumsquelle",
                        "empfohlene_aktion": "spaeter erneut pruefen oder URL korrigieren",
                        "pruefen": "Ja",
                        "kommentar": "Quelle technisch nicht erreichbar",
                    }
                )
                continue

            if medium_status == "wahrscheinlich_nicht_mehr_aktiv":
                rows.append(
                    {
                        "medium": medium,
                        "journalist": "(Medium-Ebene)",
                        "im_master": "Ja",
                        "im_web_gefunden": "Unbekannt",
                        "externer_hinweis": self._compose_external_hint(aggregated_hints),
                        "was_ist_anders": "weitere Pruefung noetig",
                        "alter_stand": "",
                        "neuer_stand": "",
                        "quelle": "offizielle Mediumsquelle",
                        "empfohlene_aktion": "Mediumstatus manuell pruefen",
                        "pruefen": "Ja",
                        "kommentar": "keine Team-/Autorenseite vorhanden",
                    }
                )
                continue

            if weak_medium_only and not official and not aggregated_hints:
                rows.append(
                    {
                        "medium": medium,
                        "journalist": "(Medium-Ebene)",
                        "im_master": "Ja",
                        "im_web_gefunden": "Nein",
                        "externer_hinweis": "kein externer Hinweis",
                        "was_ist_anders": "nur schwacher Webhinweis",
                        "alter_stand": "",
                        "neuer_stand": "",
                        "quelle": "offizielle Mediumsquelle",
                        "empfohlene_aktion": "weitere Quelle pruefen",
                        "pruefen": "Ja",
                        "kommentar": "nur Impressum oder interne Suchseite ohne Treffer",
                    }
                )
                continue

            for master in medium_master_contacts:
                hint_matches = self._find_industry_hints(master, all_industry_hints)
                official_match = self._find_official_match(master, official)
                official_evidence = self._official_evidence(self._name(master), official_documents)
                coverage = self.coverage_assessor.assess(
                    medium_status=medium_status,
                    source_stats=official_source_stats,
                    has_external_hint=bool(hint_matches),
                )
                industry_mentions = self._industry_mentions(self._name(master), payload.get("industry_documents", []))

                change_flags: list[str] = []
                externer_hinweis = self._compose_external_hint(hint_matches)
                pruefen = "Nein"
                im_web = "Nein"
                neuer_stand = ""
                kommentar = coverage.comment

                if official_match or official_evidence:
                    im_web = "Ja"
                    change_flags.append("offiziell bestaetigt")
                    for field, label in (("email", "E-Mail geaendert"), ("telefon", "Telefon geaendert"), ("rolle", "Ressort geaendert")):
                        old = (master.get(field if field != "rolle" else "ressort", "") or "").strip().lower()
                        new = (official_match or {}).get(field, "").strip().lower()
                        if old and new and old != new:
                            change_flags.append(label)
                    if official_match:
                        neuer_stand = f"{official_match.get('email', '')} | {official_match.get('telefon', '')} | {official_match.get('rolle', '')}"
                    if official_evidence.get("team") or official_evidence.get("editorial"):
                        kommentar = "offizielle Teamseite"
                    elif official_evidence.get("autorenseite"):
                        kommentar = "Autorenseite"
                    elif official_evidence.get("impressum"):
                        kommentar = "nur Impressum"
                    elif official_evidence.get("interne_suche"):
                        kommentar = "interne Suchseite mit Treffer"
                    else:
                        kommentar = "offizielle Quelle bestaetigt Kontakt"
                else:
                    if hint_matches and coverage.level != "hoch":
                        change_flags.append("Wechsel in Branchenquelle gemeldet")
                        pruefen = "Ja"
                        kommentar = "Branchenquelle meldet Wechsel"
                    elif coverage.level == "hoch":
                        change_flags.append("auf offiziellen Seiten nicht bestaetigt")
                        pruefen = "Ja"
                        kommentar = "offizielle Team-/Autorenseite ohne Treffer"
                    elif has_expected_contact_source and coverage.level == "mittel":
                        change_flags.append("auf offiziellen Seiten nicht bestaetigt")
                        pruefen = "Ja"
                        kommentar = "Kontakt auf belastbarer Quelle nicht sichtbar"
                    else:
                        change_flags.append("nur schwacher Webhinweis")
                        pruefen = "Ja"
                        kommentar = "interne Suchseite ohne Treffer"

                if industry_mentions and not hint_matches and not official_match:
                    change_flags.append("nur schwacher Webhinweis")
                    pruefen = "Ja"
                    externer_hinweis = "Namensnennung in Branchenquelle"
                    kommentar = "Branchenquelle"

                if hint_matches and (official_match or official_evidence):
                    change_flags.append("weitere Pruefung noetig")
                    pruefen = "Ja"
                    kommentar = "offizielle Quelle bestaetigt Kontakt, externer Hinweis abweichend"

                other_medium_found = False
                for other_medium, other_payload in parsed_structured.items():
                    if other_medium == medium:
                        continue
                    other_docs = other_payload.get("official_documents", [])
                    if self._official_evidence(self._name(master), other_docs):
                        other_medium_found = True
                        kommentar = f"bei anderem Medium gefunden: {other_medium}"
                        break
                if other_medium_found and not (official_match or official_evidence):
                    change_flags = ["bei anderem Medium gefunden"]
                    pruefen = "Ja"

                source_parts = ["offizielle Mediumsquelle"]
                if hint_matches:
                    source_parts.extend(sorted({h.get("source", "") for h in hint_matches if h.get("source")}))
                if industry_mentions and not hint_matches:
                    source_parts.append("Branchenquelle")
                source = ", ".join(dict.fromkeys(source_parts))
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
