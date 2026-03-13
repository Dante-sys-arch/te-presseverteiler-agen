"""Central validation layer for contact candidates before reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_ALLOWED_RE = re.compile(r"^[\d\s\-+()/\.]+$")
PERSON_TOKEN_RE = re.compile(r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß'\-]{1,}$")

DEFAULT_NON_PERSON_TERMS = {
    "allgemeine zeitung",
    "ihre daten",
    "ein unternehmen",
    "entdecken sie",
    "technische betreuung",
    "geschätzte lesezeit",
    "new york",
    "picture press",
    "handelsgericht wien",
    "zentrale kontaktstellen",
    "digital service",
    "die frankfurter",
    "vertrieb einzelverkauf",
    "amtsgericht köln",
}

GENERIC_PERSON_WORDS = {
    "team",
    "redaktion",
    "kontakt",
    "service",
    "support",
    "presse",
    "kommunikation",
    "vertrieb",
    "datenschutz",
    "impressum",
    "formular",
    "unternehmen",
    "gericht",
    "amt",
    "new",
    "york",
}


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    reason: str = ""


class Validator:
    """Validates parsed/matched records with strict person-level criteria."""

    def __init__(self, terms_file: Path | None = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self.terms_file = terms_file or (root / "config" / "non_person_terms.txt")
        self.non_person_terms = self._load_terms()

    def _normalize_text(self, value: str) -> str:
        cleaned = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
        return re.sub(r"\s+", " ", cleaned)

    def _load_terms(self) -> set[str]:
        terms = set(DEFAULT_NON_PERSON_TERMS)
        if not self.terms_file.exists():
            return terms
        for line in self.terms_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            terms.add(self._normalize_text(stripped))
        return terms

    def _looks_like_person_name(self, first: str, last: str) -> bool:
        first_n = self._normalize_text(first)
        last_n = self._normalize_text(last)
        if not first_n or not last_n:
            return False
        if first_n in GENERIC_PERSON_WORDS or last_n in GENERIC_PERSON_WORDS:
            return False
        if not PERSON_TOKEN_RE.fullmatch(first.strip()) or not PERSON_TOKEN_RE.fullmatch(last.strip()):
            return False
        return True

    def _contains_non_person_terms(self, *values: str) -> bool:
        haystack = " ".join(self._normalize_text(v) for v in values if v)
        if not haystack:
            return False
        return any(term in haystack for term in self.non_person_terms)

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return True
        phone_s = str(phone).strip()
        if not PHONE_ALLOWED_RE.fullmatch(phone_s):
            return False
        digits = re.sub(r"\D", "", phone_s)
        if len(digits) < 7 or len(digits) > 15:
            return False
        if len(set(digits)) <= 2:
            return False
        return True

    def _email_matches_name(self, email: str, first: str, last: str) -> bool:
        email = self._normalize_text(email)
        first_n = self._normalize_text(first)
        last_n = self._normalize_text(last)
        if not email or not first_n or not last_n:
            return False
        if not EMAIL_RE.fullmatch(email):
            return False
        local = email.split("@", 1)[0]
        parts = [p for p in re.split(r"[^a-z0-9äöüß]+", local) if p]
        if not parts:
            return False
        initials = {first_n[:1], last_n[:1]}
        joined = "".join(parts)
        first_simple = re.sub(r"[^a-z0-9]", "", first_n)
        last_simple = re.sub(r"[^a-z0-9]", "", last_n)
        if first_simple in joined and last_simple in joined:
            return True
        if first_simple in parts or last_simple in parts:
            return True
        if set(parts).intersection(initials) and (first_simple in joined or last_simple in joined):
            return True
        return False

    def validate_record(self, record: dict) -> ValidationResult:
        first = str(record.get("vorname") or "").strip()
        last = str(record.get("nachname") or "").strip()
        role = str(record.get("rolle") or "").strip()
        email = str(record.get("email") or "").strip().lower()
        phone = str(record.get("telefon") or "").strip()

        if not self._looks_like_person_name(first, last):
            return ValidationResult(False, "missing_or_invalid_name")
        if self._contains_non_person_terms(first, last, role):
            return ValidationResult(False, "contains_non_person_term")
        if not email or not self._email_matches_name(email, first, last):
            return ValidationResult(False, "email_name_mismatch")
        if not self._is_plausible_phone(phone):
            return ValidationResult(False, "invalid_phone")
        return ValidationResult(True, "")

    def filter_records(self, records: list[dict]) -> list[dict]:
        return [record for record in records if self.validate_record(record).is_valid]
