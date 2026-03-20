"""Parser module for structured contact and change-hint extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import re
from typing import Any

from validator import Validator


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß'\-]{1,})\s+([A-ZÄÖÜ][a-zäöüß'\-]{1,})\b")
PHONE_RE = re.compile(r"(?:\+|\(?\d)[\d\s\-/().]{5,}\d")
ROLE_HINT_RE = re.compile(r"(Redaktion|Chefredaktion|Ressort|Wirtschaft|Politik|Finanzen|Editor)", re.IGNORECASE)
CHANGE_HINT_RE = re.compile(r"(wechselt|neu bei|verl[äa]sst|geht zu|heuert an|beruft|verl[äa]sst das medium)", re.IGNORECASE)
INACTIVE_HINT_RE = re.compile(r"(eingestellt|nicht mehr aktiv|insolvenz|geschlossen)", re.IGNORECASE)


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


class Parser:
    """Performs structured extraction for configured official and industry sources."""

    def __init__(self) -> None:
        self.validator = Validator()

    def _classify_official_page(self, url: str) -> str:
        target = str(url or "").lower()
        if "impressum" in target:
            return "impressum"
        if any(token in target for token in ("redaktion", "team", "autor", "ressort")):
            return "editorial"
        if "kontakt" in target:
            return "kontakt"
        return "other"

    def _clean_text(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = cleaned.replace("\\u003e", ">").replace("\\u003c", "<")
        cleaned = cleaned.replace("\\/", "/")
        cleaned = html.unescape(cleaned)
        return cleaned

    def _extract_contacts(self, text: str) -> list[ParsedContact]:
        emails = [self.validator.clean_email(e) for e in EMAIL_RE.findall(text)]
        phones = [self.validator.normalize_phone(p) for p in PHONE_RE.findall(text)]
        phones = [p for p in phones if p]

        contacts: list[ParsedContact] = []
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
                    rolle=role_match.group(1) if role_match else "",
                    email=email,
                    telefon=phones[0] if phones else "",
                )
            )

        # Deduplicate by email
        dedup: dict[str, ParsedContact] = {}
        for contact in contacts:
            if contact.email and contact.email not in dedup:
                dedup[contact.email] = contact
        return list(dedup.values())

    def _extract_industry_hints(self, text: str, source: str) -> list[IndustryHint]:
        hints: list[IndustryHint] = []
        lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
        for line in lines:
            if CHANGE_HINT_RE.search(line):
                names = [f"{f} {l}" for f, l in NAME_RE.findall(line)]
                for name in names or [""]:
                    hints.append(
                        IndustryHint(
                            journalist=name,
                            source=source,
                            hint_type="Branchenquelle meldet Wechsel",
                            detail=line[:300],
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
        """Parse crawl output into structured records per source type."""
        structured: dict[str, dict[str, Any]] = {}
        for key, snapshots in raw_snapshots.items():
            official_contacts: list[ParsedContact] = []
            official_status = "ok"
            industry_hints: list[IndustryHint] = []
            official_total_sources = 0
            official_reachable_sources = 0
            official_page_types: set[str] = set()

            for snapshot in snapshots:
                text = self._clean_text(getattr(snapshot, "content", ""))
                source_type = getattr(snapshot, "source_type", "official_medium")
                status_code = getattr(snapshot, "status_code", None)
                error = getattr(snapshot, "error", None)

                if source_type == "official_medium":
                    official_total_sources += 1
                    official_page_types.add(self._classify_official_page(getattr(snapshot, "url", "")))
                    official_contacts.extend(self._extract_contacts(text))
                    if error or (status_code is not None and status_code >= 500):
                        official_status = "technisch_nicht_erreichbar"
                    else:
                        official_reachable_sources += 1
                        if status_code in (404, 410):
                            official_status = "wahrscheinlich_nicht_mehr_aktiv"
                elif source_type == "industry_source":
                    industry_hints.extend(self._extract_industry_hints(text, getattr(snapshot, "source_name", key)))

            structured[key] = {
                "official_contacts": [asdict(c) for c in official_contacts],
                "industry_hints": [asdict(h) for h in industry_hints],
                "medium_status": official_status,
                "official_source_stats": {
                    "total_sources": official_total_sources,
                    "reachable_sources": official_reachable_sources,
                    "has_impressum": "impressum" in official_page_types,
                    "has_editorial_pages": "editorial" in official_page_types,
                },
            }
        return structured

    def parse(self, raw_snapshots: dict[str, Any]) -> dict[str, list[ParsedContact]]:
        """Backward compatible contact-only parse for scoring layer."""
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
