"""Parser module for transforming snapshots into structured contact records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import os
import re
from typing import Any

from validator import Validator


SALUTATIONS = ("Herr", "Frau", "Mr", "Mrs", "Ms", "Dr")
ROLE_KEYWORDS = (
    "Redaktion",
    "Chefredaktion",
    "Presse",
    "Kommunikation",
    "Editor",
    "Journalist",
    "Ressort",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_CANDIDATE_RE = re.compile(r"(?:\+|\(?\d)[\d\s\-/().]{5,}\d")
DATE_LIKE_PHONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b")
BLOCKED_NAME_TERMS = {
    "source",
    "sans",
    "serif",
    "arial",
    "helvetica",
    "font",
    "redaktion",
    "kommunikation",
    "presse",
    "editor",
    "journalist",
    "ressort",
    "kontakt",
    "service",
    "digital",
    "vertrieb",
    "datenschutz",
    "recht",
    "formular",
    "frankfurter",
}
BLOCKED_FULL_NAMES = {"source sans", "source serif"}
GENERIC_MAILBOX_TERMS = {"info", "kontakt", "contact", "hello", "office", "redaktion", "kommunikation", "presse", "newsroom", "service", "support", "admin", "mail"}
BLOCKED_ROLE_PHRASES = {
    "zentrale kontaktstelle",
    "digital service",
    "kundenservice",
    "vertrieb",
    "rechtsabteilung",
    "datenschutz",
    "impressum",
}
BLOCKED_CONTEXT_TERMS = {
    "agentur",
    "amtsgericht",
    "abteilung",
    "behörde",
    "bundes",
    "department",
    "kontakt",
    "service",
    "support",
    "team",
    "formular",
    "redaktion",
    "verlag",
    "gmbh",
    "ag",
    "kg",
    "mbh",
    "stadt",
    "land",
    "frankfurter",
    "digital",
}
ORGANIZATION_TERMS = {
    "media",
    "google",
    "press",
    "verlag",
    "redaktion",
    "service",
    "kontakt",
    "gmbh",
    "ag",
    "ltd",
    "inc",
    "holding",
}

@dataclass(frozen=True)
class ParsedContact:
    """Represents one parsed contact candidate."""

    anrede: str = ""
    vorname: str = ""
    nachname: str = ""
    rolle: str = ""
    email: str = ""
    telefon: str = ""


class Parser:
    """Regex-first parser with optional OpenAI fallback."""

    def __init__(self) -> None:
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.validator = Validator()

    def _is_plausible_email(self, email: str) -> bool:
        if not email:
            return False
        email = self._clean_email_candidate(email)
        if not EMAIL_RE.fullmatch(email):
            return False
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain:
            return False
        if local.startswith("noreply") or local.startswith("no-reply"):
            return False
        local_root = re.split(r"[._+-]", local)[0]
        if local_root in GENERIC_MAILBOX_TERMS:
            return False
        return True

    def _clean_email_candidate(self, email: str) -> str:
        return self.validator.clean_email(self._clean_text(email))

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return False
        normalized = str(phone).strip()
        if DATE_LIKE_PHONE_RE.fullmatch(normalized):
            return False
        if re.fullmatch(r"\d{3}-\d{4,}", normalized):
            return False
        if normalized.count("/") > 1:
            return False
        if normalized.endswith("/"):
            return False
        return bool(self.validator.normalize_phone(normalized))

    def _normalize_phone(self, phone: str) -> str:
        compact = re.sub(r"\s+", " ", phone).strip(" ,;.")
        return compact

    def _extract_tel_href_phones(self, text: str) -> set[str]:
        cleaned = self._clean_text(text)
        matches = re.findall(r"tel:([^\"'\s>]+)", cleaned, flags=re.IGNORECASE)
        normalized: set[str] = set()
        for match in matches:
            candidate = re.sub(r"[%;,].*$", "", match).strip()
            candidate = candidate.replace("%2B", "+").replace("%20", " ")
            phone = self.validator.normalize_phone(candidate, from_tel_link=True)
            if phone:
                normalized.add(phone)
        return normalized

    def _pick_reliable_phone(self, value: str, *, tel_phones: set[str]) -> str:
        normalized = self.validator.normalize_phone(value)
        if normalized and normalized in tel_phones:
            return normalized
        if normalized and str(value).strip().startswith(("+", "00")):
            return normalized
        return ""

    def _is_plausible_name(self, vorname: str, nachname: str) -> bool:
        first_raw = vorname.strip()
        last_raw = nachname.strip()
        first = first_raw.lower()
        last = last_raw.lower()
        full = f"{first} {last}".strip()
        if not first or not last:
            return False
        if any(t in {first, last} for t in ORGANIZATION_TERMS):
            return False
        if full in BLOCKED_FULL_NAMES:
            return False
        if first in BLOCKED_NAME_TERMS or last in BLOCKED_NAME_TERMS:
            return False
        if len(first) < 2 or len(last) < 2:
            return False
        if first in BLOCKED_CONTEXT_TERMS or last in BLOCKED_CONTEXT_TERMS:
            return False
        if not re.fullmatch(r"[A-ZÄÖÜ][a-zäöüß'\-]{1,}", first_raw):
            return False
        if not re.fullmatch(r"[A-ZÄÖÜ][a-zäöüß'\-]{1,}", last_raw):
            return False
        return True

    def _looks_like_organization_name(self, first: str, last: str) -> bool:
        tokens = {first.strip().lower(), last.strip().lower()}
        return any(token in ORGANIZATION_TERMS for token in tokens)

    def _is_plausible_role(self, role: str) -> bool:
        if not role:
            return True
        lower = role.strip().lower()
        return lower not in BLOCKED_ROLE_PHRASES

    def _is_blocked_name_context(self, window: str, first: str, last: str) -> bool:
        lowered = re.sub(r"\s+", " ", window.lower())
        pair = f"{first.lower()} {last.lower()}"
        if pair in BLOCKED_FULL_NAMES:
            return True
        for term in BLOCKED_CONTEXT_TERMS:
            if f"{term} {last.lower()}" in lowered or f"{first.lower()} {term}" in lowered:
                return True
        return False

    def _pick_name(self, window: str) -> tuple[str, str]:
        for first, last in NAME_RE.findall(window):
            if self._looks_like_organization_name(first, last):
                continue
            if self._is_plausible_name(first, last) and not self._is_blocked_name_context(window, first, last):
                return first, last
        return "", ""

    def _clean_text(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = cleaned.replace("\\u003e", ">")
        cleaned = cleaned.replace("u003e", ">")
        cleaned = cleaned.replace("\\u003c", "<")
        cleaned = cleaned.replace("u003c", "<")
        cleaned = cleaned.replace("\\/", "/")
        cleaned = cleaned.replace("\\", "")
        cleaned = html.unescape(cleaned)
        return cleaned

    def _deduplicate(self, contacts: list[ParsedContact]) -> list[ParsedContact]:
        deduped: dict[tuple[str, str, str], ParsedContact] = {}
        for contact in contacts:
            key = (
                contact.email.strip().lower(),
                contact.vorname.strip().lower(),
                contact.nachname.strip().lower(),
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = contact
                continue
            # prefer richer record
            current_score = int(bool(existing.telefon)) + int(bool(existing.rolle)) + int(bool(existing.anrede))
            next_score = int(bool(contact.telefon)) + int(bool(contact.rolle)) + int(bool(contact.anrede))
            if next_score > current_score:
                deduped[key] = contact
        return list(deduped.values())

    def _regex_parse(self, text: str) -> list[ParsedContact]:
        normalized_text = self._clean_text(text)
        emails = [self._clean_email_candidate(email) for email in EMAIL_RE.findall(normalized_text)]
        emails = [email for email in emails if self._is_plausible_email(email)]
        tel_phones = self._extract_tel_href_phones(text)
        phones = [self._normalize_phone(p) for p in PHONE_CANDIDATE_RE.findall(normalized_text)]
        phones = [self._pick_reliable_phone(phone, tel_phones=tel_phones) for phone in phones]
        phones = [phone for phone in phones if phone]

        contacts: list[ParsedContact] = []
        for idx, email in enumerate(emails):
            pos = normalized_text.find(email)
            window_start = max(0, pos - 250)
            window_end = min(len(normalized_text), pos + 250)
            window = normalized_text[window_start:window_end]

            anrede = next((s for s in SALUTATIONS if s in window), "")
            rolle = next((r for r in ROLE_KEYWORDS if r.lower() in window.lower()), "")
            vorname, nachname = self._pick_name(window)

            if not self._is_plausible_name(vorname, nachname):
                continue
            if self._looks_like_organization_name(vorname, nachname):
                continue
            if not self._is_plausible_role(rolle):
                continue

            window_phone_matches = [
                self._pick_reliable_phone(self._normalize_phone(p), tel_phones=tel_phones)
                for p in PHONE_CANDIDATE_RE.findall(window)
            ]
            window_phone_matches = [phone for phone in window_phone_matches if phone]
            telefon = window_phone_matches[0] if window_phone_matches else (phones[idx] if idx < len(phones) else "")
            contacts.append(
                ParsedContact(
                    anrede=anrede,
                    vorname=vorname,
                    nachname=nachname,
                    rolle=rolle,
                    email=email,
                    telefon=telefon.strip(),
                )
            )

        return self._deduplicate(contacts)

    def _sanitize_contacts(self, contacts: list[ParsedContact]) -> list[ParsedContact]:
        sanitized: list[ParsedContact] = []
        for contact in contacts:
            if not self._is_plausible_name(contact.vorname, contact.nachname):
                continue
            if self._looks_like_organization_name(contact.vorname, contact.nachname):
                continue
            cleaned_email = self._clean_email_candidate(contact.email)
            if cleaned_email and not self._is_plausible_email(cleaned_email):
                continue
            sanitized.append(
                ParsedContact(
                    anrede=contact.anrede.strip(),
                    vorname=contact.vorname.strip(),
                    nachname=contact.nachname.strip(),
                    rolle=contact.rolle.strip(),
                    email=cleaned_email,
                    telefon=self._pick_reliable_phone(contact.telefon.strip(), tel_phones=set()),
                )
            )
        return self._deduplicate(sanitized)

    def _openai_fallback(self, text: str) -> list[ParsedContact]:
        if not self.openai_api_key:
            return []

        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.openai_api_key)
            prompt = (
                "Extrahiere Kontaktfelder aus folgendem Impressumstext. "
                "Liefere ausschließlich JSON-Liste mit Objekten: "
                "anrede, vorname, nachname, rolle, email, telefon.\n\n"
                f"Text:\n{text[:6000]}"
            )
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=prompt,
                temperature=0,
            )
            raw = getattr(response, "output_text", "")
            if not raw:
                return []

            import json

            data = json.loads(raw)
            contacts: list[ParsedContact] = []
            for item in data if isinstance(data, list) else []:
                contacts.append(
                    ParsedContact(
                        anrede=str(item.get("anrede", "")),
                        vorname=str(item.get("vorname", "")),
                        nachname=str(item.get("nachname", "")),
                        rolle=str(item.get("rolle", "")),
                        email=str(item.get("email", "")),
                        telefon=str(item.get("telefon", "")),
                    )
                )
            return self._sanitize_contacts(contacts)
        except Exception:
            return []

    def parse(self, raw_snapshots: dict[str, Any]) -> dict[str, list[ParsedContact]]:
        """Parse snapshots to contact candidates using regex-first strategy."""
        parsed: dict[str, list[ParsedContact]] = {}
        for medium, snapshot in raw_snapshots.items():
            text = getattr(snapshot, "content", "") if not isinstance(snapshot, str) else snapshot
            text = self._clean_text(text)
            regex_contacts = self._sanitize_contacts(self._regex_parse(text))
            contacts = regex_contacts if regex_contacts else self._sanitize_contacts(self._openai_fallback(text))
            parsed[medium] = contacts
        return parsed


def as_records(parsed_data: dict[str, list[ParsedContact]]) -> dict[str, list[dict[str, str]]]:
    """Convert dataclass contacts into dictionaries."""
    return {medium: [asdict(c) for c in contacts] for medium, contacts in parsed_data.items()}
