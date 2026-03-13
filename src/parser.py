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

    def _regex_parse(self, text: str) -> list[ParsedContact]:
        emails = EMAIL_RE.findall(text)
        phones = PHONE_RE.findall(text)
        names = NAME_RE.findall(text)

        contacts: list[ParsedContact] = []
        for idx, email in enumerate(emails):
            window_start = max(0, text.find(email) - 200)
            window_end = min(len(text), text.find(email) + 200)
            window = text[window_start:window_end]

            anrede = next((s for s in SALUTATIONS if s in window), "")
            rolle = next((r for r in ROLE_KEYWORDS if r.lower() in window.lower()), "")
            vorname = ""
            nachname = ""
            if idx < len(names):
                vorname, nachname = names[idx]

            telefon = phones[idx] if idx < len(phones) else ""
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
            return contacts
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
