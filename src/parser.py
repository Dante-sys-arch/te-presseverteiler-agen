"""Parser module for transforming snapshots into structured contact records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
from typing import Any


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
EDITORIAL_TERMS = {
    "redaktion",
    "chefredaktion",
    "presse",
    "kommunikation",
    "newsroom",
    "editorial",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_CANDIDATE_RE = re.compile(r"(?:\+|\(?\d)[\d\s\-/().]{5,}\d")
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
    "allgemeine",
    "zeitung",
    "unternehmen",
    "entdecken",
    "technische",
    "betreuung",
    "geschätzte",
    "lesezeit",
    "silicon",
    "valley",
    "york",
    "alles",
    "wichtige",
    "picture",
    "press",
    "handelsgericht",
    "wien",
    "ihre",
    "daten",
    "ihnen",
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
    "silicon",
    "valley",
    "new",
    "york",
    "wien",
    "handelsgericht",
    "picture",
    "press",
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

    def _is_plausible_email(self, email: str) -> bool:
        if not email:
            return False
        email = email.strip().lower()
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

    def _is_editorial_context(self, text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in EDITORIAL_TERMS)

    def _email_matches_person_or_editorial(self, email: str, first: str, last: str, role: str, window: str) -> bool:
        if not self._is_plausible_email(email):
            return False
        local = email.partition("@")[0].lower()
        local_tokens = [token for token in re.split(r"[._+-]", local) if token]
        first_l = first.lower()
        last_l = last.lower()

        has_person_match = (
            first_l in local_tokens
            or last_l in local_tokens
            or f"{first_l}{last_l}" in local
            or f"{last_l}{first_l}" in local
            or (first_l[:1] and f"{first_l[:1]}{last_l}" in local)
        )
        if has_person_match:
            return True

        local_root = local_tokens[0] if local_tokens else ""
        is_generic_mailbox = local_root in GENERIC_MAILBOX_TERMS
        if not is_generic_mailbox:
            return False

        return self._is_editorial_context(role) or self._is_editorial_context(window)

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return False
        normalized = phone.strip()
        if normalized.count("/") > 1:
            return False
        if normalized.endswith("/"):
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return False
        if re.fullmatch(r"(?:\d\s+){5,}\d", normalized):
            return False
        if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", normalized):
            return False
        if "." in normalized and re.search(r"\d\s+\d\s+\d\s+\d", normalized):
            return False
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7 or len(digits) > 15:
            return False
        unique_digits = len(set(digits))
        if unique_digits <= 2:
            return False
        if re.fullmatch(r"(\d)\1{6,}", digits):
            return False
        return True

    def _normalize_phone(self, phone: str) -> str:
        compact = re.sub(r"\s+", " ", phone).strip(" ,;.")
        return compact

    def _is_plausible_name(self, vorname: str, nachname: str) -> bool:
        first = vorname.strip().lower()
        last = nachname.strip().lower()
        full = f"{first} {last}".strip()
        if not first or not last:
            return False
        if full in BLOCKED_FULL_NAMES:
            return False
        if first in BLOCKED_NAME_TERMS or last in BLOCKED_NAME_TERMS:
            return False
        if len(first) < 2 or len(last) < 2:
            return False
        if first == last:
            return False
        if not first.isalpha() or not last.isalpha():
            return False
        if first in BLOCKED_CONTEXT_TERMS or last in BLOCKED_CONTEXT_TERMS:
            return False
        return True

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
            if self._is_plausible_name(first, last) and not self._is_blocked_name_context(window, first, last):
                return first, last
        return "", ""

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
        emails = [email for email in EMAIL_RE.findall(text) if self._is_plausible_email(email)]
        phones = [self._normalize_phone(p) for p in PHONE_CANDIDATE_RE.findall(text)]
        phones = [phone for phone in phones if self._is_plausible_phone(phone)]

        contacts: list[ParsedContact] = []
        for idx, email in enumerate(emails):
            pos = text.find(email)
            window_start = max(0, pos - 250)
            window_end = min(len(text), pos + 250)
            window = text[window_start:window_end]

            anrede = next((s for s in SALUTATIONS if s in window), "")
            rolle = next((r for r in ROLE_KEYWORDS if r.lower() in window.lower()), "")
            vorname, nachname = self._pick_name(window)

            if not self._is_plausible_name(vorname, nachname):
                continue
            if not self._is_plausible_role(rolle):
                continue
            if not self._email_matches_person_or_editorial(email, vorname, nachname, rolle, window):
                continue

            window_phone_matches = [
                self._normalize_phone(p)
                for p in PHONE_CANDIDATE_RE.findall(window)
                if self._is_plausible_phone(p)
            ]
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
            if contact.email and not self._is_plausible_email(contact.email):
                continue
            merged_context = f"{contact.rolle} {contact.email}"
            if contact.email and not self._email_matches_person_or_editorial(
                contact.email,
                contact.vorname,
                contact.nachname,
                contact.rolle,
                merged_context,
            ):
                continue
            sanitized.append(
                ParsedContact(
                    anrede=contact.anrede.strip(),
                    vorname=contact.vorname.strip(),
                    nachname=contact.nachname.strip(),
                    rolle=contact.rolle.strip(),
                    email=contact.email.strip(),
                    telefon=contact.telefon.strip() if self._is_plausible_phone(contact.telefon) else "",
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
            regex_contacts = self._regex_parse(text)
            parsed[medium] = regex_contacts if regex_contacts else self._openai_fallback(text)
        return parsed


def as_records(parsed_data: dict[str, list[ParsedContact]]) -> dict[str, list[dict[str, str]]]:
    """Convert dataclass contacts into dictionaries."""
    return {medium: [asdict(c) for c in contacts] for medium, contacts in parsed_data.items()}
