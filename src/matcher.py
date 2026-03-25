"""Matcher module for master-vs-web delta assessments with source weighting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import csv
import re
import unicodedata

import pandas as pd
from rapidfuzz import fuzz

from hit_evaluator import evaluate_hit
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
        self._matching_detail_rows: list[dict] = []
        self._web_research_detail_rows: list[dict] = []
        self._treffer_auswertung_detail_rows: list[dict] = []
        self._benchmark_media = {
            self._normalize_name(name)
            for name in (
                "Boersen-Zeitung",
                "Börsen-Zeitung",
                "Handelsblatt",
                "WiWo",
                "Wirtschaftswoche",
                "The Market",
                "NZZ",
                "AssCompact",
            )
        }
        self._title_tokens = {"dr", "prof", "dipl", "mag", "mba", "msc", "llm"}
        self._nickname_map = {
            "alex": {"alexander", "alexandra"},
            "andi": {"andreas"},
            "franz": {"franziska"},
            "max": {"maximilian"},
            "susi": {"susanne"},
            "tom": {"thomas"},
            "tobi": {"tobias"},
        }
        self._function_tokens = {
            "redaktion",
            "service",
            "kontakt",
            "presse",
            "desk",
            "ressort",
            "postfach",
            "leserservice",
            "kundenservice",
            "team",
            "info",
        }
        self._generic_email_locals = {
            "feedback",
            "redaktion",
            "info",
            "service",
            "kontakt",
            "leserservice",
            "office",
            "presse",
            "newsroom",
            "desk",
            "ressort",
            "politik",
            "wirtschaft",
            "sport",
            "kultur",
        }
        self._central_phone_tokens = {
            "zentrale",
            "zentral",
            "hotline",
            "service",
            "switchboard",
            "vermittlung",
        }
        self._central_phone_text_tokens = {
            "zentrale",
            "hotline",
            "service",
            "switchboard",
            "vermittlung",
            "telefonzentrale",
        }

    def _compose_external_hint(self, hint_matches: list[dict]) -> str:
        if not hint_matches:
            return "kein externer Hinweis"
        sources = sorted({str(h.get("source", "")).strip() for h in hint_matches if str(h.get("source", "")).strip()})
        source_label = ", ".join(sources) if sources else "Branchenquelle"
        return f"Plausibler Wechselhinweis aus {source_label}"

    def _infer_medium_from_text(self, text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return ""
        patterns = [
            r"(?:bei|arbeitet bei|taetig bei|jetzt bei|ist bei|joined|joining)\s+([A-ZÄÖÜ][\wÄÖÜäöüß&()./\- ]{2,80})",
            r"(?:von|wechselte von)\s+[A-ZÄÖÜ][\wÄÖÜäöüß&()./\- ]{2,80}\s+(?:zu|to)\s+([A-ZÄÖÜ][\wÄÖÜäöüß&()./\- ]{2,80})",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                candidate = re.split(r"[,;|]", match.group(1))[0].strip()
                candidate = re.sub(r"\s{2,}", " ", candidate)
                if len(candidate) >= 3:
                    return candidate
        return ""

    def _infer_medium_from_document(self, doc: dict) -> str:
        return self._infer_medium_from_text(doc.get("text", ""))

    def _same_medium(self, left: str, right: str) -> bool:
        a = self._normalize_name(left)
        b = self._normalize_name(right)
        if not a or not b:
            return False
        return a == b or a in b or b in a or fuzz.ratio(a, b) >= 90

    def _derive_new_medium_hint(
        self,
        hint_matches: list[dict],
        linkedin_mentions: list[dict],
        open_web_mentions: list[dict],
        current_medium: str,
    ) -> str:
        candidates: list[str] = []
        for hint in hint_matches:
            value = str(hint.get("erkanntes_medium", "")).strip() or str(hint.get("new_medium_hint", "")).strip()
            if value:
                candidates.append(value)
        for doc in linkedin_mentions + open_web_mentions:
            value = self._infer_medium_from_document(doc)
            if value:
                candidates.append(value)
        for candidate in candidates:
            if not self._same_medium(candidate, current_medium):
                return candidate
        return candidates[0] if candidates else ""

    def _find_documents_with_name(self, master_name: str, documents: list[dict], source_type: str) -> list[dict]:
        variants = self._name_variants(master_name)
        found: list[dict] = []
        for doc in documents:
            if str(doc.get("source_type", "")) != source_type:
                continue
            haystack = self._normalize_name(doc.get("text", ""))
            if any(variant in haystack for variant in variants if variant):
                found.append(doc)
        return found

    def _normalize_name(self, value: str) -> str:
        txt = unicodedata.normalize("NFKD", str(value or "").strip().lower())
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        txt = re.sub(r"\b(dr|prof|dipl|mag|mba|msc|llm)\.?\b", "", txt)
        txt = re.sub(r"[^a-z0-9\s-]", " ", txt)
        txt = txt.replace("ae", "a").replace("oe", "o").replace("ue", "u").replace("ss", "s")
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
            f"{last} {first}",
            f"{last} {first[0]}",
        }
        replace_map = {"ae": "ä", "oe": "ö", "ue": "ü", "ss": "ß"}
        for candidate in list(variants):
            remapped = candidate
            for src, dest in replace_map.items():
                remapped = remapped.replace(src, dest)
            variants.add(self._normalize_name(remapped))
        return {v for v in variants if v}

    def _split_name(self, full_name: str) -> tuple[str, str]:
        normalized = self._normalize_name(full_name)
        parts = [p for p in re.split(r"[\s\-]+", normalized) if p and p not in self._title_tokens]
        if not parts:
            return "", ""
        return parts[0], parts[-1] if len(parts) > 1 else ""

    def _is_initial_match(self, left: str, right: str) -> bool:
        return bool(left and right and len(left) == 1 and right.startswith(left))

    def _is_nickname_match(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        if self._is_initial_match(left, right) or self._is_initial_match(right, left):
            return True
        return right in self._nickname_map.get(left, set()) or left in self._nickname_map.get(right, set())

    def _is_function_address(self, row: dict) -> bool:
        name = self._normalize_name(self._name(row))
        local = str(row.get("email", "")).lower().split("@", 1)[0]
        role = self._normalize_name(str(row.get("rolle", row.get("ressort", ""))))
        haystack = f"{name} {local} {role}"
        return any(token in haystack for token in self._function_tokens)

    def _is_generic_email(self, email: str) -> bool:
        local = str(email or "").strip().lower().split("@", 1)[0]
        if not local:
            return False
        local_tokens = [part for part in re.split(r"[._\-+]", local) if part]
        return any(token in self._generic_email_locals for token in local_tokens)

    def _email_looks_personal(self, email: str, row: dict) -> bool:
        normalized_email = str(email or "").strip().lower()
        if not normalized_email or "@" not in normalized_email:
            return False
        if self._is_generic_email(normalized_email):
            return False
        local = normalized_email.split("@", 1)[0]
        first, last = self._split_name(self._name(row))
        personal_tokens = {token for token in {first, last} if token}
        normalized_local = re.sub(r"[^a-z0-9]", "", local)
        if any(token and token in normalized_local for token in personal_tokens):
            return True
        if "." in local and len(local.split(".")) >= 2:
            return True
        return bool(re.search(r"[a-z]{2,}[._\-][a-z]{2,}", local))

    def _is_central_phone(self, phone: str, row: dict) -> bool:
        raw_phone = str(phone or "").lower()
        if any(token in raw_phone for token in self._central_phone_text_tokens):
            return True
        normalized_phone = re.sub(r"\D+", "", str(phone or ""))
        if not normalized_phone:
            return False
        if self._is_function_address(row):
            return True
        role = self._normalize_name(str(row.get("rolle", row.get("ressort", ""))))
        name = self._normalize_name(self._name(row))
        haystack = f"{role} {name}"
        return any(token in haystack for token in self._central_phone_tokens)

    def _email_pattern_score(self, master_email: str, candidate_email: str) -> int:
        if not master_email or not candidate_email:
            return 0
        m_local = master_email.split("@", 1)[0].lower()
        c_local = candidate_email.split("@", 1)[0].lower()
        if m_local == c_local:
            return 30
        if m_local.replace(".", "") == c_local.replace(".", ""):
            return 22
        if m_local.split(".")[-1] == c_local.split(".")[-1]:
            return 12
        return 0

    def _score_official_candidate(self, master: dict, candidate: dict, medium: str) -> tuple[int, str, list[str]]:
        master_name = self._name(master)
        candidate_name = self._name(candidate)
        m_first, m_last = self._split_name(master_name)
        c_first, c_last = self._split_name(candidate_name)
        reason_parts: list[str] = []
        score = 0
        match_art = "name_fuzzy"

        if m_last and c_last and m_last == c_last:
            score += 42
            reason_parts.append("Nachname identisch")
        elif m_last and c_last and fuzz.ratio(m_last, c_last) >= 88:
            score += 28
            reason_parts.append("Nachname sehr aehnlich")

        if self._is_nickname_match(m_first, c_first):
            score += 25
            reason_parts.append("Vorname kompatibel")
        elif m_first and c_first and fuzz.ratio(m_first, c_first) >= 82:
            score += 15
            reason_parts.append("Vorname aehnlich")

        full_ratio = fuzz.ratio(self._normalize_name(master_name), self._normalize_name(candidate_name))
        score += int(full_ratio // 4)

        email_bonus = self._email_pattern_score(master.get("email", ""), candidate.get("email", ""))
        if email_bonus:
            score += email_bonus
            match_art = "email_pattern"
            reason_parts.append("E-Mail-Muster passt")

        master_ressort = self._normalize_name(master.get("ressort", ""))
        candidate_ressort = self._normalize_name(candidate.get("rolle", ""))
        if master_ressort and candidate_ressort and (master_ressort in candidate_ressort or candidate_ressort in master_ressort):
            score += 10
            reason_parts.append("Ressort passend")

        if self._normalize_name(medium) in self._benchmark_media and full_ratio >= 70:
            score += 8
            reason_parts.append("Benchmark-Medium-Bonus")

        return min(score, 100), match_art, reason_parts

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
        if "Keine automatische Uebernahme" in change_flags:
            return "Keine automatische Uebernahme"
        if "Medium nicht erreichbar" in change_flags:
            return "spaeter erneut pruefen oder URL korrigieren"
        if "Medium wahrscheinlich nicht mehr aktiv" in change_flags:
            return "Mediumstatus manuell pruefen"
        if "Journalist bei Medium nicht mehr gefunden" in change_flags:
            return "deaktivieren oder manuell pruefen"
        if "Manuell pruefen" in change_flags:
            return "manuell pruefen"
        if "Wahrscheinlicher Treffer" in change_flags:
            return "manuell pruefen"
        if "Auf offiziellen Seiten nicht bestaetigt" in change_flags:
            return "weitere Quelle pruefen"
        if "Wahrscheinlicher Medienwechsel" in change_flags:
            return "manuell pruefen"
        if "Bei anderem Medium gefunden" in change_flags:
            return "Medium im Master aktualisieren"
        if "Nur schwacher Hinweis" in change_flags:
            return "manuell pruefen"
        if "Nichts Belastbares gefunden" in change_flags:
            return "manuell pruefen"
        if "Weitere Quelle pruefen" in change_flags:
            return "weitere Quelle pruefen"
        return "Keine Aktion"

    def _has_reliable_official_evidence(self, evidence: dict[str, list[str]], official_match: dict | None) -> bool:
        reliable_types = {"team", "redaktion", "autorenseite", "ressortseite", "interne_suche"}
        if any(evidence.get(page_type) for page_type in reliable_types):
            return True
        if official_match:
            role = str(official_match.get("rolle", "")).lower()
            if role in reliable_types:
                return True
        return False

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

    def _find_official_match(self, master: dict, official_contacts: list[dict], medium: str) -> tuple[dict | None, int, str, str]:
        email = master.get("email", "").lower()
        for record in official_contacts:
            if record.get("email", "").lower() == email and email:
                return record, 100, "email_exact", "E-Mail exakt gleich"

        best_score = -1
        best: dict | None = None
        best_match_art = ""
        best_reason = ""
        for record in official_contacts:
            score, match_art, reason_parts = self._score_official_candidate(master, record, medium)
            if score > best_score:
                best_score = score
                best = record
                best_match_art = match_art
                best_reason = ", ".join(reason_parts)
        return best, max(best_score, 0), best_match_art, (best_reason or "unspezifischer Namensabgleich")

    def _find_industry_hints(self, master: dict, industry_hints: list[dict]) -> list[dict]:
        name = self._name(master).lower()
        out: list[dict] = []
        for hint in industry_hints:
            journalist = str(hint.get("journalist", "")).lower()
            if journalist and fuzz.ratio(name, journalist) >= 92:
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
        self._matching_detail_rows = []
        self._web_research_detail_rows = []
        self._treffer_auswertung_detail_rows = []
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
            industry_documents = payload.get("industry_documents", [])
            medium_status = payload.get("medium_status", "ok")
            official_source_stats = payload.get("official_source_stats", {})
            research_stages = payload.get("research_stages", {}) or {}
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
            weak_medium_only = coverage.level == "niedrig" or (coverage.level == "mittel" and not has_expected_contact_source)
            has_any_external_document = bool(industry_documents)

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
                        "quelle": "official_medium",
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
                        "was_ist_anders": "Medium wahrscheinlich nicht mehr aktiv",
                        "alter_stand": "",
                        "neuer_stand": "",
                        "quelle": "official_medium",
                        "empfohlene_aktion": "Mediumstatus manuell pruefen",
                        "pruefen": "Ja",
                        "kommentar": "keine belastbare offizielle Quelle",
                    }
                )
                continue

            if weak_medium_only and not official and not aggregated_hints and not has_any_external_document:
                rows.append(
                    {
                        "medium": medium,
                        "journalist": "(Medium-Ebene)",
                        "im_master": "Ja",
                        "im_web_gefunden": "Nein",
                        "externer_hinweis": "kein externer Hinweis",
                        "was_ist_anders": "Weitere Quelle pruefen",
                        "alter_stand": "",
                        "neuer_stand": "",
                        "quelle": "official_medium",
                        "empfohlene_aktion": "weitere Quelle pruefen",
                        "pruefen": "Ja",
                        "kommentar": "nur Impressum vorhanden",
                    }
                )
                continue

            for master in medium_master_contacts:
                hint_matches = self._find_industry_hints(master, all_industry_hints)
                official_match, match_score, match_art, match_reason = self._find_official_match(master, official, medium)
                official_evidence = self._official_evidence(self._name(master), official_documents)
                reliable_evidence = self._has_reliable_official_evidence(official_evidence, official_match)
                is_function_master = self._is_function_address(master)
                is_function_match = self._is_function_address(official_match or {})
                coverage = self.coverage_assessor.assess(
                    medium_status=medium_status,
                    source_stats=official_source_stats,
                    has_external_hint=bool(hint_matches),
                )
                industry_mentions = self._industry_mentions(self._name(master), industry_documents)
                linkedin_mentions = self._find_documents_with_name(self._name(master), industry_documents, "linkedin_source")
                open_web_mentions = self._find_documents_with_name(self._name(master), industry_documents, "open_web")
                evaluated_linkedin = [
                    evaluate_hit(
                        text=str(doc.get("text", "")),
                        source_type="linkedin_source",
                        source_name=str(doc.get("source_name", "LinkedIn")),
                        current_medium=medium,
                    )
                    for doc in linkedin_mentions
                ]
                evaluated_web = [
                    evaluate_hit(
                        text=str(doc.get("text", "")),
                        source_type="open_web",
                        source_name=str(doc.get("source_name", "open_web")),
                        current_medium=medium,
                    )
                    for doc in open_web_mentions
                ]
                evaluated_industry = [
                    evaluate_hit(
                        text=str(hint.get("detail", "")),
                        source_type="industry_source",
                        source_name=str(hint.get("source", "Branchenquelle")),
                        current_medium=medium,
                    )
                    for hint in hint_matches
                ]
                required_stages = (
                    "offizielle_mediumsseiten",
                    "domain_interne_suche",
                    "branchenquelle",
                    "linkedin",
                    "allgemeine_websuche",
                )
                cascade_state_known = bool(research_stages)
                cascade_complete = cascade_state_known and all(bool(research_stages.get(stage, False)) for stage in required_stages)

                change_flags: list[str] = []
                externer_hinweis = self._compose_external_hint(hint_matches)
                pruefen = "Nein"
                im_web = "Nein"
                neuer_stand = ""
                kommentar = coverage.comment
                email_typ_bewertung = "unbekannt"
                telefon_typ_bewertung = "unbekannt"
                linkedin_hinweis = "Ja" if linkedin_mentions else "Nein"
                neues_medium_hinweis = self._derive_new_medium_hint(hint_matches, linkedin_mentions, open_web_mentions, medium)
                gefunden_bei = medium if (reliable_evidence or match_score >= 65) else ""
                quellenbasis = "offizielle Mediumseiten" if (reliable_evidence or match_score >= 65) else "kaskadierte Webrecherche"

                if reliable_evidence or match_score >= 85:
                    im_web = "Ja"
                    change_flags.append("Journalist bestaetigt")
                    contact_type_notes: list[str] = []
                    for field, label in (("email", "E-Mail geaendert"), ("telefon", "Telefon geaendert"), ("rolle", "Ressort geaendert")):
                        old = (master.get(field if field != "rolle" else "ressort", "") or "").strip().lower()
                        new = (official_match or {}).get(field, "").strip().lower()
                        if not (old and new and old != new):
                            continue
                        if field == "email":
                            master_email_personal = self._email_looks_personal(old, master)
                            new_email_personal = self._email_looks_personal(new, official_match or {})
                            if master_email_personal and self._is_generic_email(new):
                                change_flags = [flag for flag in change_flags if flag != "Journalist bestaetigt"]
                                change_flags.append("Journalist bestaetigt; persoenliche E-Mail nicht bestaetigt")
                                change_flags.append("Allgemeine Kontaktadresse gefunden")
                                change_flags.append("Keine automatische Uebernahme")
                                email_typ_bewertung = "Master: personengebunden | Web: allgemein"
                                contact_type_notes.append("E-Mail-Typ: allgemein")
                                continue
                            if new_email_personal and (reliable_evidence or match_score >= 85):
                                change_flags.append(label)
                                email_typ_bewertung = "Master: personengebunden | Web: personengebunden"
                                contact_type_notes.append("E-Mail-Typ: personengebunden")
                            elif self._is_generic_email(new):
                                email_typ_bewertung = "Master: unbekannt | Web: allgemein"
                            continue
                        if field == "telefon":
                            master_phone_personal = not self._is_central_phone(old, master)
                            new_phone_central = self._is_central_phone(new, official_match or {})
                            if master_phone_personal and new_phone_central:
                                change_flags.append("Allgemeine Kontaktadresse gefunden")
                                change_flags.append("Keine automatische Uebernahme")
                                telefon_typ_bewertung = "Master: personengebunden | Web: zentral"
                                contact_type_notes.append("Telefon-Typ: zentral")
                                continue
                            if not new_phone_central:
                                change_flags.append(label)
                                telefon_typ_bewertung = "Master: personengebunden | Web: personengebunden"
                                contact_type_notes.append("Telefon-Typ: personengebunden")
                            else:
                                telefon_typ_bewertung = "Master: unbekannt | Web: zentral"
                            continue
                        change_flags.append(label)
                    if official_match:
                        neuer_stand = f"{official_match.get('email', '')} | {official_match.get('telefon', '')} | {official_match.get('rolle', '')}"
                    if official_evidence.get("team") or official_evidence.get("redaktion"):
                        kommentar = "offizielle Teamseite bestaetigt"
                    elif official_evidence.get("autorenseite"):
                        kommentar = "Autorenprofil gefunden"
                    elif official_evidence.get("interne_suche"):
                        kommentar = "interne Suchseite mit Treffer"
                    else:
                        kommentar = "offizielle Quelle bestaetigt"
                    if contact_type_notes:
                        kommentar = f"{kommentar}; {'; '.join(dict.fromkeys(contact_type_notes))}"
                else:
                    if match_score >= 65:
                        change_flags.append("Wahrscheinlicher Treffer")
                        pruefen = "Ja"
                        kommentar = f"plausibler Match ({match_score}) - {match_reason}"
                        im_web = "Ja"
                        if official_match:
                            neuer_stand = f"{official_match.get('email', '')} | {official_match.get('telefon', '')} | {official_match.get('rolle', '')}"
                    elif match_score >= 50:
                        change_flags.append("Manuell pruefen")
                        pruefen = "Ja"
                        kommentar = f"teilweise passend ({match_score}) - {match_reason}"
                    elif hint_matches:
                        change_flags.append("Wahrscheinlicher Medienwechsel")
                        pruefen = "Ja"
                        kommentar = "Branchenquelle meldet Wechsel"
                        if neues_medium_hinweis:
                            change_flags.append("Bei anderem Medium gefunden")
                            gefunden_bei = neues_medium_hinweis
                        if any(e.entscheidung == "Wahrscheinlicher Medienwechsel" for e in evaluated_industry):
                            externer_hinweis = self._compose_external_hint(hint_matches)
                            quellenbasis = "Branchenquelle"
                    elif coverage.level == "hoch":
                        if cascade_complete or not cascade_state_known:
                            change_flags.append("Journalist bei Medium nicht mehr gefunden")
                            pruefen = "Ja"
                            kommentar = "komplette Recherche-Kaskade ohne Treffer" if cascade_complete else "mehrere relevante offizielle Seiten ohne Treffer"
                        else:
                            change_flags.append("Auf offiziellen Seiten nicht bestaetigt")
                            pruefen = "Ja"
                            kommentar = "Kaskade unvollstaendig"
                    elif has_expected_contact_source and coverage.level == "mittel":
                        change_flags.append("Auf offiziellen Seiten nicht bestaetigt")
                        pruefen = "Ja"
                        kommentar = "keine belastbare offizielle Quelle"
                    elif int(official_source_stats.get("reachable_sources", 0) or 0) >= 3:
                        change_flags.append("Weitere Quelle pruefen")
                        pruefen = "Ja"
                        kommentar = "mehrere offizielle Seiten geprueft, keine belastbare Aussage"
                    else:
                        change_flags.append("Nur schwacher Hinweis")
                        pruefen = "Ja"
                        kommentar = "nur schwacher Hinweis aus offizieller Recherche"

                if industry_mentions and not hint_matches and not official_match:
                    if "Weitere Quelle pruefen" not in change_flags:
                        change_flags.append("Weitere Quelle pruefen")
                    pruefen = "Ja"
                    externer_hinweis = "Namensnennung in Branchenquelle"
                    kommentar = "Branchenquelle meldet Wechsel"

                if linkedin_mentions and not reliable_evidence and not hint_matches:
                    pruefen = "Ja"
                    quellenbasis = "LinkedIn + Websuche"
                    linkedin_strong = next((e for e in evaluated_linkedin if e.entscheidung == "LinkedIn bestaetigt neues Medium"), None)
                    if linkedin_strong:
                        change_flags = [flag for flag in change_flags if flag not in {"Nur schwacher Hinweis", "Weitere Quelle pruefen"}]
                        if "LinkedIn bestaetigt neues Medium" not in change_flags:
                            change_flags.append("LinkedIn bestaetigt neues Medium")
                        if "Bei anderem Medium gefunden" not in change_flags:
                            change_flags.append("Bei anderem Medium gefunden")
                        gefunden_bei = linkedin_strong.erkanntes_medium or neues_medium_hinweis
                        neues_medium_hinweis = linkedin_strong.erkanntes_medium or neues_medium_hinweis
                        kommentar = linkedin_strong.kommentar
                    elif neues_medium_hinweis and not self._same_medium(neues_medium_hinweis, medium):
                        change_flags = [flag for flag in change_flags if flag != "Nur schwacher Hinweis"]
                        if "Wahrscheinlicher Medienwechsel" not in change_flags:
                            change_flags.append("Wahrscheinlicher Medienwechsel")
                        if "Bei anderem Medium gefunden" not in change_flags:
                            change_flags.append("Bei anderem Medium gefunden")
                        gefunden_bei = neues_medium_hinweis
                        kommentar = "LinkedIn-Hinweis mit abweichendem Medium"
                    else:
                        if "Weitere Quelle pruefen" not in change_flags:
                            change_flags.append("Weitere Quelle pruefen")
                        kommentar = "LinkedIn-Hinweis ohne eindeutigen Medienwechsel"

                if open_web_mentions and not reliable_evidence and not hint_matches and not linkedin_mentions:
                    web_strong = next((e for e in evaluated_web if e.entscheidung == "Bei anderem Medium gefunden"), None)
                    if web_strong:
                        change_flags = [flag for flag in change_flags if flag != "Nur schwacher Hinweis"]
                        if "Bei anderem Medium gefunden" not in change_flags:
                            change_flags.append("Bei anderem Medium gefunden")
                        if "Wahrscheinlicher Medienwechsel" not in change_flags:
                            change_flags.append("Wahrscheinlicher Medienwechsel")
                        gefunden_bei = web_strong.erkanntes_medium or neues_medium_hinweis
                        neues_medium_hinweis = web_strong.erkanntes_medium or neues_medium_hinweis
                        kommentar = web_strong.kommentar
                    elif neues_medium_hinweis and not self._same_medium(neues_medium_hinweis, medium):
                        change_flags = [flag for flag in change_flags if flag != "Nur schwacher Hinweis"]
                        if "Wahrscheinlicher Medienwechsel" not in change_flags:
                            change_flags.append("Wahrscheinlicher Medienwechsel")
                        if "Bei anderem Medium gefunden" not in change_flags:
                            change_flags.append("Bei anderem Medium gefunden")
                        gefunden_bei = neues_medium_hinweis
                    elif "Nur schwacher Hinweis" not in change_flags:
                        change_flags.append("Nur schwacher Hinweis")
                    pruefen = "Ja"
                    kommentar = "Nur allgemeine Websuche mit Namensnennung" if not gefunden_bei else "Web-Treffer mit abweichendem Medium"

                if not reliable_evidence and not hint_matches and not linkedin_mentions and not open_web_mentions and cascade_complete:
                    change_flags = ["Nichts Belastbares gefunden"]
                    pruefen = "Ja"
                    kommentar = "Komplette Recherche-Kaskade ohne plausiblen Treffer"
                    quellenbasis = "vollstaendige Recherche-Kaskade"

                if hint_matches and reliable_evidence:
                    if "Weitere Quelle pruefen" not in change_flags:
                        change_flags.append("Weitere Quelle pruefen")
                    pruefen = "Ja"
                    kommentar = "offizielle Teamseite bestaetigt, Branchenhinweis abweichend"

                if is_function_master or is_function_match:
                    kommentar = f"{kommentar}; Funktionsadresse getrennt bewertet"

                self._matching_detail_rows.append(
                    {
                        "Medium": medium,
                        "Master_Journalist": self._name(master),
                        "Web_Treffer": self._name(official_match or {}),
                        "Match_Art": match_art or ("evidence_only" if reliable_evidence else "kein_match"),
                        "Match_Staerke": match_score,
                        "Grund": match_reason,
                        "Entscheidung": "; ".join(dict.fromkeys(change_flags)),
                    }
                )

                source_parts = ["official_medium"]
                if hint_matches or industry_mentions:
                    source_parts.append("industry_source")
                if linkedin_mentions:
                    source_parts.append("linkedin")
                if open_web_mentions:
                    source_parts.append("open_web")
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
                        "webrecherche_durchgefuehrt": "Ja" if cascade_complete else "Teilweise",
                        "linkedin_hinweis": linkedin_hinweis,
                        "neues_medium_hinweis": neues_medium_hinweis,
                        "gefunden_bei": gefunden_bei,
                        "quellenbasis": quellenbasis,
                        "email_typ_bewertung": email_typ_bewertung,
                        "telefon_typ_bewertung": telefon_typ_bewertung,
                    }
                )
                for stage, details in (
                    ("offizielle_mediumsseiten", official_documents),
                    ("branchenquelle", industry_mentions),
                    ("linkedin", linkedin_mentions),
                    ("allgemeine_websuche", open_web_mentions),
                ):
                    hit = bool(details)
                    source_label = ", ".join(sorted({str(d.get("source_name", "")) for d in details if str(d.get("source_name", ""))})) or stage
                    self._web_research_detail_rows.append(
                        {
                            "Medium": medium,
                            "Journalist": self._name(master),
                            "Suchstufe": stage,
                            "Quelle": source_label,
                            "Treffer_ja_nein": "Ja" if hit else "Nein",
                            "Treffertext_kurz": (str(details[0].get("text", ""))[:180] if hit else ""),
                            "Bewertung": "hoch" if stage == "offizielle_mediumsseiten" else ("mittel" if stage in {"branchenquelle", "linkedin"} else "niedrig"),
                            "Kommentar": kommentar if not hit else "",
                        }
                    )
                for doc, evaluated in list(zip(linkedin_mentions, evaluated_linkedin)) + list(zip(open_web_mentions, evaluated_web)):
                    self._treffer_auswertung_detail_rows.append(
                        {
                            "Medium_alt": medium,
                            "Journalist": self._name(master),
                            "Trefferquelle": doc.get("source_name", ""),
                            "Treffertext_kurz": str(doc.get("text", ""))[:220],
                            "Erkannter_Arbeitgeber": evaluated.erkannter_arbeitgeber,
                            "Erkanntes_Medium": evaluated.erkanntes_medium,
                            "Erkannte_Rolle": evaluated.erkannte_rolle,
                            "Bewertungsstufe": evaluated.bewertungsstufe,
                            "Entscheidung": evaluated.entscheidung,
                            "Kommentar": evaluated.kommentar,
                        }
                    )
                for hint, evaluated in zip(hint_matches, evaluated_industry):
                    self._treffer_auswertung_detail_rows.append(
                        {
                            "Medium_alt": medium,
                            "Journalist": self._name(master),
                            "Trefferquelle": hint.get("source", ""),
                            "Treffertext_kurz": str(hint.get("detail", ""))[:220],
                            "Erkannter_Arbeitgeber": evaluated.erkannter_arbeitgeber or hint.get("erkanntes_medium", ""),
                            "Erkanntes_Medium": evaluated.erkanntes_medium or hint.get("new_medium_hint", ""),
                            "Erkannte_Rolle": evaluated.erkannte_rolle or hint.get("erkannte_rolle", ""),
                            "Bewertungsstufe": evaluated.bewertungsstufe or hint.get("belastbarkeit", ""),
                            "Entscheidung": evaluated.entscheidung or hint.get("entscheidung", ""),
                            "Kommentar": evaluated.kommentar or hint.get("kommentar", ""),
                        }
                    )
        return rows, not_scanned_rows

    def get_matching_detail_rows(self) -> list[dict]:
        return list(self._matching_detail_rows)

    def get_web_research_detail_rows(self) -> list[dict]:
        return list(self._web_research_detail_rows)

    def get_treffer_auswertung_detail_rows(self) -> list[dict]:
        return list(self._treffer_auswertung_detail_rows)

    def match(self, parsed_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        return parsed_data
