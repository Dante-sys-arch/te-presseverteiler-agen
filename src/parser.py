"""Parser module for structured contact and change-hint extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import re
from typing import Any

from validator import SourceCoverageAssessor
from validator import Validator


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß'\-]{1,})\s+([A-ZÄÖÜ][a-zäöüß'\-]{1,})\b")
PHONE_RE = re.compile(r"(?:\+|\(?\d)[\d\s\-/().]{5,}\d")
ROLE_HINT_RE = re.compile(r"(Redaktion|Chefredaktion|Ressort|Wirtschaft|Politik|Finanzen|Editor|Team)", re.IGNORECASE)
CHANGE_HINT_RE = re.compile(r"(wechselt|neu bei|verl[äa]sst|geht zu|heuert an|beruft|verl[äa]sst das medium)", re.IGNORECASE)
INACTIVE_HINT_RE = re.compile(r"(eingestellt|nicht mehr aktiv|insolvenz|geschlossen)", re.IGNORECASE)
NEW_EMPLOYER_RE = re.compile(r"(neu bei|geht zu|wechselt zu|neuer arbeitgeber)\s+([A-ZÄÖÜ][^,.;:]{2,80})", re.IGNORECASE)
EMPLOYER_CONTEXT_RE = re.compile(
    r"(?:bei|arbeitet bei|taetig bei|jetzt bei|ist bei|joined|joining)\s+([A-ZÄÖÜ][^,.;:]{2,80})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedContact:
    anrede: str = ""
    vorname: str = ""
    nachname: str = ""
    rolle: str = ""
    email: str = ""
    telefon: str = ""


@dataclass(frozen=True)
class IndustryHint:
    journalist: str = ""
    source: str = ""
    hint_type: str = ""
    detail: str = ""
    new_medium_hint: str = ""


class Parser:
    """Performs structured extraction for configured official and industry sources."""

    def __init__(self) -> None:
        self.validator = Validator()
        self.coverage_assessor = SourceCoverageAssessor()

    def _classify_official_page(self, url: str) -> str:
        target = str(url or "").lower()
        if "impressum" in target:
            return "impressum"
        if any(token in target for token in ("suche", "search", "?q=", "?query=")):
            return "interne_suche"
        if any(token in target for token in ("autor", "author")):
            return "autorenseite"
        if any(token in target for token in ("ressort", "rubrik", "section")):
            return "ressortseite"
        if any(token in target for token in ("redaktion", "team", "editorial")):
            return "team"
        if "kontakt" in target:
            return "kontakt"
        return "other"

    def _clean_text(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = cleaned.replace("\\u003e", ">").replace("\\u003c", "<")
        cleaned = cleaned.replace("\\/", "/")
        return html.unescape(cleaned)

    def _extract_contacts(self, text: str, page_type: str = "other") -> list[ParsedContact]:
        contacts: list[ParsedContact] = []
        emails = [self.validator.clean_email(e) for e in EMAIL_RE.findall(text)]
        phones = [self.validator.normalize_phone(p) for p in PHONE_RE.findall(text)]
        phones = [p for p in phones if p]

        for email in emails:
            local = email.split("@", 1)[0]
            parts = [part for part in re.split(r"[._-]", local) if part]
            first = parts[0].title() if parts else ""
            last = parts[1].title() if len(parts) > 1 else ""
            if not first or not last:
                for maybe_first, maybe_last in NAME_RE.findall(text):
                    first, last = maybe_first, maybe_last
                    break
            role_match = ROLE_HINT_RE.search(text)
            contacts.append(
                ParsedContact(
                    vorname=first,
                    nachname=last,
                    rolle=role_match.group(1) if role_match else page_type,
                    email=email,
                    telefon=phones[0] if phones else "",
                )
            )

        # Additional extraction for team/author/profile pages without visible email.
        if page_type in {"team", "autorenseite", "ressortseite", "redaktion"}:
            for first, last in NAME_RE.findall(text):
                if len(first) <= 1 or len(last) <= 1:
                    continue
                contacts.append(
                    ParsedContact(
                        vorname=first,
                        nachname=last,
                        rolle=page_type,
                        email="",
                        telefon="",
                    )
                )
            # parse author links such as /autor/max-mustermann and map to names
            for slug in re.findall(r"/(?:autor|author|autoren)/([a-z0-9\-]{3,})", text.lower()):
                parts = [part for part in slug.split("-") if part]
                if len(parts) >= 2:
                    contacts.append(
                        ParsedContact(
                            vorname=parts[0].title(),
                            nachname=parts[-1].title(),
                            rolle=page_type,
                            email="",
                            telefon="",
                        )
                    )

        dedup: dict[tuple[str, str, str], ParsedContact] = {}
        for contact in contacts:
            key = (
                contact.email.lower(),
                contact.vorname.lower(),
                contact.nachname.lower(),
            )
            if key not in dedup:
                dedup[key] = contact
        return list(dedup.values())

    def _extract_industry_hints(self, text: str, source: str, source_type: str = "", journalist: str = "") -> list[IndustryHint]:
        hints: list[IndustryHint] = []
        lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
        fallback_journalist = str(journalist or "").strip()
        for line in lines:
            if CHANGE_HINT_RE.search(line):
                names = [f"{f} {l}" for f, l in NAME_RE.findall(line)]
                for name in names or [""]:
                    employer_match = NEW_EMPLOYER_RE.search(line)
                    hints.append(
                        IndustryHint(
                            journalist=name,
                            source=source,
                            hint_type="Branchenquelle meldet Wechsel",
                            detail=line[:300],
                            new_medium_hint=(employer_match.group(2).strip() if employer_match else ""),
                        )
                    )
            elif source_type in {"linkedin_source", "open_web"}:
                names = [f"{f} {l}" for f, l in NAME_RE.findall(line)]
                journalist_name = names[0] if names else fallback_journalist
                employer_match = EMPLOYER_CONTEXT_RE.search(line)
                if journalist_name and employer_match:
                    hints.append(
                        IndustryHint(
                            journalist=journalist_name,
                            source=source,
                            hint_type="Externes Profil mit Arbeitgeber-Hinweis",
                            detail=line[:300],
                            new_medium_hint=employer_match.group(1).strip(),
                        )
                    )
            elif INACTIVE_HINT_RE.search(line):
                hints.append(
                    IndustryHint(
                        journalist="",
                        source=source,
                        hint_type="Mediumstatus in Branchenquelle auffaellig",
                        detail=line[:300],
                    )
                )
        return hints

    def parse_structured(self, raw_snapshots: dict[str, list[Any]]) -> dict[str, dict[str, Any]]:
        structured: dict[str, dict[str, Any]] = {}
        for key, snapshots in raw_snapshots.items():
            official_contacts: list[ParsedContact] = []
            official_status = "ok"
            industry_hints: list[IndustryHint] = []
            official_documents: list[dict[str, str]] = []
            industry_documents: list[dict[str, str]] = []
            official_total_sources = 0
            official_reachable_sources = 0
            official_page_types: set[str] = set()
            official_source_categories: list[str] = []
            contact_expected_sources = 0
            not_found_sources = 0
            technical_failures = 0
            source_diagnostics: list[dict[str, Any]] = []

            for snapshot in snapshots:
                text = self._clean_text(getattr(snapshot, "content", ""))
                source_type = getattr(snapshot, "source_type", "official_medium")
                status_code = getattr(snapshot, "status_code", None)
                error = getattr(snapshot, "error", None)

                if source_type == "official_medium":
                    official_total_sources += 1
                    page_type = self._classify_official_page(getattr(snapshot, "url", ""))
                    official_page_types.add(page_type)
                    expectation = self.coverage_assessor.classify_source_expectation(
                        getattr(snapshot, "source_name", ""),
                        getattr(snapshot, "url", ""),
                    )
                    official_source_categories.append(expectation.source_category)
                    if expectation.contact_expected:
                        contact_expected_sources += 1
                    extracted_contacts = self._extract_contacts(text, page_type=page_type)
                    official_contacts.extend(extracted_contacts)
                    if error or (status_code is not None and status_code >= 500):
                        technical_failures += 1
                    else:
                        official_reachable_sources += 1
                        if status_code in (404, 410):
                            not_found_sources += 1
                    official_documents.append(
                        {
                            "url": getattr(snapshot, "url", ""),
                            "source_name": getattr(snapshot, "source_name", ""),
                            "page_type": page_type,
                            "text": text[:15000],
                        }
                    )
                    source_diagnostics.append(
                        {
                            "Medium": key,
                            "Source_Type": page_type,
                            "Source_Label": getattr(snapshot, "source_name", ""),
                            "URL": getattr(snapshot, "url", ""),
                            "Status_Code": status_code,
                            "Erfolgreich_geladen": "Ja" if not error and (status_code is None or status_code < 500) else "Nein",
                            "Kontakte_extrahiert": "Ja" if extracted_contacts else "Nein",
                            "Anzahl_Kontakte": len(extracted_contacts),
                            "Extraktionsart": "struktur+regex" if extracted_contacts else "keine",
                            "Bewertung_Quelle": getattr(snapshot, "source_priority", "") or expectation.source_category,
                            "Fehler": error or "",
                            "Kommentar": "nur Impressum geprueft" if page_type == "impressum" and len(snapshots) == 1 else "",
                        }
                    )
                elif source_type == "industry_source":
                    industry_hints.extend(
                        self._extract_industry_hints(
                            text,
                            getattr(snapshot, "source_name", key),
                            source_type=source_type,
                            journalist=getattr(snapshot, "journalist", ""),
                        )
                    )
                    industry_documents.append(
                        {
                            "url": getattr(snapshot, "url", ""),
                            "source_name": getattr(snapshot, "source_name", key),
                            "text": text[:15000],
                        }
                    )
                elif source_type in {"linkedin_source", "open_web"}:
                    industry_hints.extend(
                        self._extract_industry_hints(
                            text,
                            getattr(snapshot, "source_name", source_type),
                            source_type=source_type,
                            journalist=getattr(snapshot, "journalist", ""),
                        )
                    )
                    industry_documents.append(
                        {
                            "url": getattr(snapshot, "url", ""),
                            "source_name": getattr(snapshot, "source_name", source_type),
                            "source_type": source_type,
                            "journalist": getattr(snapshot, "journalist", ""),
                            "text": text[:15000],
                        }
                    )

            if official_total_sources and official_reachable_sources == 0:
                official_status = "technisch_nicht_erreichbar"
            elif official_total_sources and not_found_sources == official_total_sources:
                official_status = "wahrscheinlich_nicht_mehr_aktiv"
            elif technical_failures and official_reachable_sources == 0:
                official_status = "technisch_nicht_erreichbar"

            cascaded_stages = {
                "offizielle_mediumsseiten": False,
                "domain_interne_suche": False,
                "branchenquelle": False,
                "linkedin": False,
                "allgemeine_websuche": False,
            }
            for snapshot in snapshots:
                source_type = getattr(snapshot, "source_type", "")
                source_name = str(getattr(snapshot, "source_name", "")).lower()
                search_stage = getattr(snapshot, "search_stage", "")
                if source_type == "official_medium":
                    cascaded_stages["offizielle_mediumsseiten"] = True
                    if source_name in {"interne_suche", "autorenseite", "autorenseiten", "ressortseite", "ressortseiten", "team", "redaktion"}:
                        cascaded_stages["domain_interne_suche"] = True
                if source_type == "industry_source":
                    cascaded_stages["branchenquelle"] = True
                if source_type == "linkedin_source":
                    cascaded_stages["linkedin"] = True
                if source_type == "open_web":
                    cascaded_stages["allgemeine_websuche"] = True
                if search_stage in cascaded_stages:
                    cascaded_stages[search_stage] = True

            structured[key] = {
                "official_contacts": [asdict(c) for c in official_contacts],
                "industry_hints": [asdict(h) for h in industry_hints],
                "official_documents": official_documents,
                "industry_documents": industry_documents,
                "medium_status": official_status,
                "official_source_stats": {
                    "total_sources": official_total_sources,
                    "reachable_sources": official_reachable_sources,
                    "has_impressum": "impressum" in official_page_types,
                    "has_editorial_pages": bool(official_page_types & {"team", "autorenseite", "ressortseite"}),
                    "source_categories": official_source_categories,
                    "contact_expected_sources": contact_expected_sources,
                },
                "source_diagnostics": source_diagnostics,
                "research_stages": cascaded_stages,
            }
        return structured

    def parse(self, raw_snapshots: dict[str, Any]) -> dict[str, list[ParsedContact]]:
        if raw_snapshots and isinstance(next(iter(raw_snapshots.values())), list):
            structured = self.parse_structured(raw_snapshots)
            return {
                medium: [ParsedContact(**record) for record in payload.get("official_contacts", [])]
                for medium, payload in structured.items()
                if payload.get("official_contacts")
            }

        parsed: dict[str, list[ParsedContact]] = {}
        for medium, snapshot in raw_snapshots.items():
            text = self._clean_text(getattr(snapshot, "content", "") if not isinstance(snapshot, str) else snapshot)
            parsed[medium] = self._extract_contacts(text)
        return parsed


def as_records(parsed_data: dict[str, list[ParsedContact]]) -> dict[str, list[dict[str, str]]]:
    return {medium: [asdict(c) for c in contacts] for medium, contacts in parsed_data.items()}
