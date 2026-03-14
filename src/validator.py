"""Central validation layer for contact candidates before scoring/reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import html
import re
import unicodedata


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PERSON_TOKEN_RE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'\-]{1,}$")
DATE_PHONE_RE = re.compile(r"^(?:\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4})$")
PHONE_ALLOWED_RE = re.compile(r"^[\d\s\-+()/\.]+$")

STATUS_ACCEPT = "accept"
STATUS_REVIEW = "review"
STATUS_REJECT = "reject"

GENERIC_NON_PERSON_TOKENS = {
    "redaktion",
    "service",
    "kontakt",
    "support",
    "team",
    "impressum",
    "formular",
    "unternehmen",
    "datenschutz",
    "amt",
    "gericht",
    "abteilung",
    "kommunikation",
    "presse",
    "vertrieb",
    "behörde",
    "gmbh",
    "ag",
    "ltd",
    "inc",
    "holding",
    "media",
    "newsroom",
    "digital",
}


@dataclass(frozen=True)
class ValidationResult:
    is_person: bool
    email_plausible: bool
    domain_match: bool
    duplicate: bool
    status: str
    confidence: float
    reason: str
    cleaned_email: str
    cleaned_phone: str


class Validator:
    """Validates records in one central place with status + confidence."""

    def __init__(
        self,
        *,
        terms_file: Path | None = None,
        domain_rules_file: Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        self.terms_file = terms_file or (root / "config" / "non_person_terms.txt")
        self.domain_rules_file = domain_rules_file or (root / "config" / "domain_rules.yaml")
        self.non_person_terms = self._load_terms()
        self.domain_rules = self._load_domain_rules()

    def _normalize_text(self, value: str) -> str:
        cleaned = unicodedata.normalize("NFKC", str(value or ""))
        cleaned = html.unescape(cleaned)
        cleaned = re.sub(r"\\u003[eE]", ">", cleaned)
        cleaned = re.sub(r"\\u003[cC]", "<", cleaned)
        cleaned = cleaned.replace("u003e", ">").replace("u003c", "<")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip().lower()

    def _load_terms(self) -> set[str]:
        terms = set(GENERIC_NON_PERSON_TOKENS)
        if self.terms_file.exists():
            for line in self.terms_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    terms.add(self._normalize_text(stripped))
        return terms

    def _load_domain_rules(self) -> dict[str, dict[str, object]]:
        if not self.domain_rules_file.exists():
            return {}

        rules: dict[str, dict[str, object]] = {}
        current_medium = ""
        current_key = ""
        for raw_line in self.domain_rules_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" ") and line.endswith(":"):
                current_medium = self._normalize_text(line[:-1])
                rules[current_medium] = {}
                current_key = ""
                continue
            if current_medium and line.startswith("  ") and line.strip().endswith(":"):
                current_key = line.strip()[:-1]
                rules[current_medium][current_key] = []
                continue
            if current_medium and line.startswith("    - ") and current_key:
                rules[current_medium][current_key].append(line.strip()[2:].strip())
                continue
            if current_medium and line.startswith("  ") and ":" in line:
                key, value = line.strip().split(":", 1)
                rules[current_medium][key.strip()] = value.strip()
        return rules

    def clean_email(self, email: str) -> str:
        cleaned = self._normalize_text(email)
        cleaned = re.sub(r"^(?:x3e|gt)+", "", cleaned)
        cleaned = cleaned.lstrip(" >\\")
        cleaned = cleaned.replace("%40", "@")
        cleaned = cleaned.replace("(at)", "@")
        cleaned = re.sub(r"\.deu$", ".de", cleaned)
        cleaned = re.sub(r"[^a-z0-9@._%+\-]", "", cleaned)
        return cleaned

    def normalize_phone(self, phone: str, *, from_tel_link: bool = False) -> str:
        if not phone:
            return ""
        raw = self._normalize_text(phone)
        if DATE_PHONE_RE.fullmatch(raw):
            return ""
        if not PHONE_ALLOWED_RE.fullmatch(raw):
            return ""
        if re.search(r"\d+\.\d+", raw):
            return ""

        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8 or len(digits) > 15:
            return ""
        if len(set(digits)) <= 2:
            return ""
        if raw.isdigit() and not from_tel_link:
            return ""
        if raw.startswith("+"):
            normalized = f"+{digits}"
        elif raw.startswith("00"):
            normalized = f"+{digits[2:]}"
        elif from_tel_link:
            normalized = f"+{digits}" if len(digits) >= 10 else ""
        else:
            return ""
        return normalized

    def _is_person_name(self, first: str, last: str) -> bool:
        first_raw = str(first or "").strip()
        last_raw = str(last or "").strip()
        if not first_raw or not last_raw:
            return False
        if not PERSON_TOKEN_RE.fullmatch(first_raw) or not PERSON_TOKEN_RE.fullmatch(last_raw):
            return False

        first_n = self._normalize_text(first_raw)
        last_n = self._normalize_text(last_raw)
        if first_n in self.non_person_terms or last_n in self.non_person_terms:
            return False
        full = f"{first_n} {last_n}"
        return not any(term in full for term in self.non_person_terms)

    def _domain_allowed(self, medium: str, domain: str) -> bool:
        rules = self.domain_rules.get(self._normalize_text(medium), {})
        if not rules:
            return True
        domain = domain.lower().strip()
        blocked = {d.lower() for d in rules.get("blocked_domains", [])}
        if domain in blocked:
            return False

        allowed = {d.lower() for d in rules.get("allowed_domains", [])}
        allowed_secondary = {d.lower() for d in rules.get("allowed_secondary_domains", [])}
        allowed_all = allowed | allowed_secondary
        if not allowed_all:
            return True

        mode = str(rules.get("domain_match_mode", "suffix")).lower()
        if mode == "exact":
            return domain in allowed_all
        return any(domain == allowed_domain or domain.endswith(f".{allowed_domain}") for allowed_domain in allowed_all)

    def _is_plausible_email(self, email: str) -> bool:
        if not email or not EMAIL_RE.fullmatch(email):
            return False
        local, _, domain = email.partition("@")
        if len(local) < 2 or "." not in domain:
            return False
        if any(token in email for token in ("<", ">", "&gt;", "&lt;", "\\")):
            return False
        return True


    def _email_matches_name(self, email: str, first: str, last: str) -> bool:
        if not email or "@" not in email:
            return False
        local = email.split("@", 1)[0]
        first_n = re.sub(r"[^a-z0-9]", "", self._normalize_text(first))
        last_n = re.sub(r"[^a-z0-9]", "", self._normalize_text(last))
        local_n = re.sub(r"[^a-z0-9]", "", local.lower())
        if not first_n or not last_n:
            return False
        return first_n in local_n or last_n in local_n

    def _assign_status(self, *, is_person: bool, email_ok: bool, email_name_ok: bool, domain_ok: bool, phone_ok: bool, duplicate: bool) -> tuple[str, float, str]:
        if duplicate:
            return STATUS_REJECT, 0.0, "duplicate"
        if not is_person:
            return STATUS_REJECT, 0.05, "non_person"
        if not email_ok:
            return STATUS_REJECT, 0.1, "invalid_email"
        if not domain_ok:
            return STATUS_REJECT, 0.1, "foreign_domain"
        if not email_name_ok:
            return STATUS_REVIEW, 0.45, "email_name_mismatch"
        if not phone_ok:
            return STATUS_REVIEW, 0.55, "phone_uncertain"
        return STATUS_ACCEPT, 0.9, "valid"

    def _emails_are_obvious_typos(self, email_a: str, email_b: str) -> bool:
        if not email_a or not email_b or "@" not in email_a or "@" not in email_b:
            return False
        local_a, domain_a = email_a.split("@", 1)
        local_b, domain_b = email_b.split("@", 1)
        if domain_a != domain_b:
            return False
        if local_a == local_b:
            return True
        if abs(len(local_a) - len(local_b)) > 1:
            return False
        mismatch = sum(1 for a, b in zip(local_a, local_b) if a != b) + abs(len(local_a) - len(local_b))
        return mismatch <= 1

    def validate_candidate(self, record: dict, *, medium: str, seen_emails_for_person: set[str]) -> ValidationResult:
        first = str(record.get("vorname") or "").strip()
        last = str(record.get("nachname") or "").strip()
        email = self.clean_email(str(record.get("email") or ""))
        phone = self.normalize_phone(str(record.get("telefon") or ""))

        is_person = self._is_person_name(first, last)
        email_ok = self._is_plausible_email(email)
        domain_ok = False
        email_name_ok = False
        if email_ok:
            domain_ok = self._domain_allowed(medium, email.split("@", 1)[1])
            email_name_ok = self._email_matches_name(email, first, last)

        phone_ok = not str(record.get("telefon") or "").strip() or bool(phone)
        duplicate = any(self._emails_are_obvious_typos(existing, email) for existing in seen_emails_for_person)

        status, confidence, reason = self._assign_status(
            is_person=is_person,
            email_ok=email_ok,
            email_name_ok=email_name_ok,
            domain_ok=domain_ok,
            phone_ok=phone_ok,
            duplicate=duplicate,
        )
        return ValidationResult(
            is_person=is_person,
            email_plausible=email_ok,
            domain_match=domain_ok,
            duplicate=duplicate,
            status=status,
            confidence=confidence,
            reason=reason,
            cleaned_email=email,
            cleaned_phone=phone,
        )

    def validate_records(self, medium: str, records: list[dict]) -> list[dict]:
        output: list[dict] = []
        seen_for_name: dict[tuple[str, str], set[str]] = {}

        for record in records:
            first = str(record.get("vorname") or "").strip().lower()
            last = str(record.get("nachname") or "").strip().lower()
            key = (first, last)
            seen_for_name.setdefault(key, set())

            result = self.validate_candidate(record, medium=medium, seen_emails_for_person=seen_for_name[key])
            if result.status == STATUS_REJECT:
                continue

            cleaned = {
                **record,
                "email": result.cleaned_email,
                "telefon": result.cleaned_phone,
                "status": result.status,
                "confidence": result.confidence,
                "validation_reason": result.reason,
            }
            output.append(cleaned)
            seen_for_name[key].add(result.cleaned_email)

        return output
