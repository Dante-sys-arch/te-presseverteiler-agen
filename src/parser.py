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
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s\-]?)?(?:\(?\d+\)?[\s\-/]?){5,}")
NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]+)\s+([A-ZÄÖÜ][a-zäöüß-]+)\b")
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
}
BLOCKED_FULL_NAMES = {"source sans", "source serif"}
GENERIC_MAILBOX_TERMS = {"info", "kontakt", "contact", "hello", "office", "redaktion", "kommunikation", "presse", "newsroom", "service", "support", "admin", "mail"}


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

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return False
        digits = re.sub(r"\D", "", phone)
        return 6 <= len(digits) <= 15

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
        return True

    def _pick_name(self, window: str) -> tuple[str, str]:
        for first, last in NAME_RE.findall(window):
            if self._is_plausible_name(first, last):
                return first, last
        return "", ""

    def _regex_parse(self, text: str) -> list[ParsedContact]:
        emails = [email for email in EMAIL_RE.findall(text) if self._is_plausible_email(email)]
        phones = [phone.strip() for phone in PHONE_RE.findall(text) if self._is_plausible_phone(phone)]

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

            window_phone_matches = [phone.strip() for phone in PHONE_RE.findall(window) if self._is_plausible_phone(phone)]
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

        return contacts

    def _sanitize_contacts(self, contacts: list[ParsedContact]) -> list[ParsedContact]:
        sanitized: list[ParsedContact] = []
        for contact in contacts:
            if not self._is_plausible_name(contact.vorname, contact.nachname):
                continue
            if contact.email and not self._is_plausible_email(contact.email):
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
        return sanitized

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
