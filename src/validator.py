"""Central validation layer for contact candidates before scoring/reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import html
import re
import unicodedata
from urllib.parse import unquote


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
    "navigation",
    "seite",
    "abo",
    "aboservice",
    "leserbriefe",
    "archiv",
    "webmaster",
    "ombudsstelle",
    "whistleblowing",
    "tv",
    "jugendschutz",
    "dsa",
    "events",
    "picturedesk",
    "forum",
    "commercial",
    "mitarbeiter",
    "bereiche",
    "nachricht",
    "informationen",
    "residence",
    "tel",
    "editorial",
    "tech",
    "autoren",
    "privatkunden",
    "kunden",
    "mochten",
    "moechten",
    "director",
    "art",
}

GENERIC_NON_PERSON_PHRASES = {
    "im hilfebereich",
    "alles wichtige",
    "der inhalt",
    "europäische gremium",
    "gesetzlicher vertreter",
    "mail kategorie",
    "inserateaufgabe basler",
    "impressum",
    "datenschutz",
    "kontakt",
    "service",
    "kundenservice",
    "leserservice",
    "publikumsservice",
    "formular",
    "navigation",
    "anzeigen",
    "vertrieb",
    "verkauf",
    "sales",
    "chefredaktion",
    "archiv",
    "leserbriefe",
    "aboservice",
    "webmaster",
    "ombudsstelle",
    "whistleblowing",
    "tv",
    "jugendschutz",
    "dsa",
    "events",
    "picturedesk",
    "forum",
    "gf",
    "commercial",
    "editorial tech",
    "freie autoren",
    "unsere mitarbeitenden",
    "fur privatkunden",
    "für privatkunden",
    "mochten sie",
    "möchten sie",
    "art director",
    "schicken sie",
    "die federfuehrung",
    "die federführung",
    "browser emojis",
    "king george",
}

GENERIC_MAILBOX_LOCALS = {
    "impressum",
    "redaktion",
    "spiegel_online",
    "mm_redaktion",
    "sales",
    "publikumsservice",
    "anzeigen",
    "kundenservice",
    "leserservice",
    "unternehmen",
    "innovation",
    "geld",
    "chefredaktion",
    "archiv",
    "leserbriefe",
    "aboservice",
    "webmaster",
    "ombudsstelle",
    "whistleblowing",
    "tv",
    "jugendschutz",
    "dsa",
    "events",
    "picturedesk",
    "forum",
    "gf",
    "commercial",
    "service",
    "kontakt",
    "support",
    "team",
    "info",
    "politik",
    "erfolg",
    "ausland",
    "visuals",
    "nachdrucke",
    "anzeigenannahme",
    "handelsblatt",
    "online-pr",
    "suedwesten",
    "stiftung",
    "potsdam",
}

GENERIC_MAILBOX_PREFIXES = (
    "themen",
    "buero",
    "leser",
    "abo",
)

BROKEN_EMAIL_TLDS = {
    "comu",
    "deu",
}

FORM_FRAGMENT_TOKENS = {
    "ihrer",
    "ihre",
    "ihren",
    "ihres",
    "deine",
    "deiner",
    "deinen",
    "daten",
    "angaben",
    "formular",
    "datenschutz",
    "zustimmung",
    "einwilligung",
}

STREET_SUFFIXES = {
    "allee",
    "straße",
    "strasse",
    "gasse",
    "platz",
    "weg",
    "ring",
    "ufer",
}

ADDRESS_LOCATION_TOKENS = {
    "street",
    "str",
    "stadt",
    "city",
    "residence",
    "haus",
    "building",
    "plz",
    "postfach",
    "ort",
    "land",
}

FUNCTION_ROLE_TOKENS = {
    "mitarbeiter",
    "bereich",
    "bereiche",
    "redakteur",
    "redakteurin",
    "leitung",
    "manager",
    "team",
    "service",
    "vertrieb",
    "verkauf",
    "support",
    "kontakt",
    "information",
    "informationen",
    "nachricht",
    "tel",
    "editorial",
    "tech",
    "autoren",
    "mitarbeitenden",
    "privatkunden",
    "director",
}

FORM_LANGUAGE_TOKENS = {
    "bitte",
    "ihre",
    "ihrer",
    "ihren",
    "deine",
    "deiner",
    "nachricht",
    "informationen",
    "senden",
    "anfrage",
    "mochten",
    "moechten",
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




@dataclass(frozen=True)
class SourceCoverageAssessment:
    level: str
    comment: str


class SourceCoverageAssessor:
    """Rates how reliable official medium sources are for absence decisions."""

    def assess(
        self,
        *,
        medium_status: str,
        source_stats: dict | None = None,
        has_external_hint: bool = False,
    ) -> SourceCoverageAssessment:
        stats = source_stats or {}
        total_sources = int(stats.get("total_sources", 0) or 0)
        reachable_sources = int(stats.get("reachable_sources", 0) or 0)
        has_impressum = bool(stats.get("has_impressum", False))
        has_editorial_pages = bool(stats.get("has_editorial_pages", False))

        if medium_status == "technisch_nicht_erreichbar" or reachable_sources == 0:
            return SourceCoverageAssessment(level="niedrig", comment="Quelle technisch nicht erreichbar")

        score = 0
        if reachable_sources >= 2:
            score += 2
        elif reachable_sources == 1:
            score += 1

        if has_editorial_pages:
            score += 2
        elif has_impressum:
            score += 1

        if total_sources >= 3:
            score += 1

        if has_editorial_pages and score >= 4:
            level = "hoch"
        elif score >= 2:
            level = "mittel"
        else:
            level = "niedrig"

        if not has_editorial_pages and has_impressum:
            comment = "nur Impressum vorhanden"
        elif not has_editorial_pages:
            comment = "keine Redaktionsseite gefunden"
        else:
            comment = "offizielle Quelle bestaetigt Abweichung" if level == "hoch" else "Quellenlage begrenzt"

        if has_external_hint and level != "hoch":
            comment = "nur externer Hinweis vorhanden"

        return SourceCoverageAssessment(level=level, comment=comment)
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
        cleaned = unquote(cleaned)
        cleaned = re.sub(r"^(?:x3e|gt)+", "", cleaned)
        cleaned = cleaned.lstrip(" >\\")
        cleaned = cleaned.replace("%40", "@")
        cleaned = cleaned.replace("(at)", "@")
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
        if first_n in FORM_FRAGMENT_TOKENS or last_n in FORM_FRAGMENT_TOKENS:
            return False
        if first_n in STREET_SUFFIXES or last_n in STREET_SUFFIXES:
            return False
        if first_n in self.non_person_terms or last_n in self.non_person_terms:
            return False
        full = f"{first_n} {last_n}"
        if any(token in full.split() for token in FORM_FRAGMENT_TOKENS):
            return False
        return not any(term in full for term in self.non_person_terms)

    def _looks_generic_non_person_name(self, first: str, last: str) -> bool:
        first_n = self._normalize_text(first)
        last_n = self._normalize_text(last)
        full = f"{first_n} {last_n}".strip()
        if not full:
            return True

        phrase_checks = {full, first_n, last_n}
        if phrase_checks & GENERIC_NON_PERSON_PHRASES:
            return True
        if any(term in full for term in self.non_person_terms):
            return True
        tokens = [t for t in re.split(r"[^a-zäöüß]+", full) if t]
        if not tokens:
            return True
        if any(t in ADDRESS_LOCATION_TOKENS for t in tokens):
            return True
        if any(t in FUNCTION_ROLE_TOKENS for t in tokens):
            return True
        if any(t in FORM_LANGUAGE_TOKENS for t in tokens):
            return True
        return False

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

    def _ascii_variants(self, value: str) -> set[str]:
        normalized = self._normalize_text(value)
        if not normalized:
            return set()
        raw = re.sub(r"[^a-z0-9äöüß]", "", normalized)
        if not raw:
            return set()
        mapped = (
            raw.replace("ä", "ae")
            .replace("ö", "oe")
            .replace("ü", "ue")
            .replace("ß", "ss")
        )
        collapsed = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
        variants = {re.sub(r"[^a-z0-9]", "", raw), re.sub(r"[^a-z0-9]", "", mapped), re.sub(r"[^a-z0-9]", "", collapsed)}
        return {v for v in variants if v}

    def _is_plausible_email(self, email: str) -> bool:
        if not email or not EMAIL_RE.fullmatch(email):
            return False
        local, _, domain = email.partition("@")
        tld = domain.rsplit(".", 1)[-1]
        if tld in BROKEN_EMAIL_TLDS:
            return False
        if len(local) < 2 or "." not in domain:
            return False
        if any(token in email for token in ("<", ">", "&gt;", "&lt;", "\\")):
            return False
        return True

    def _is_generic_mailbox(self, email: str) -> bool:
        if not email or "@" not in email:
            return True
        local = self._normalize_text(email.split("@", 1)[0])
        local = local.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        if local in GENERIC_MAILBOX_LOCALS:
            return True
        if any(local.startswith(prefix) for prefix in GENERIC_MAILBOX_PREFIXES):
            return True
        local_parts = set(re.split(r"[._\-+]", local))
        if local_parts & GENERIC_MAILBOX_LOCALS:
            return True
        if any(any(part.startswith(prefix) for prefix in GENERIC_MAILBOX_PREFIXES) for part in local_parts):
            return True
        return False

    def _canonical_email_key(self, email: str) -> str:
        if not email or "@" not in email:
            return ""
        local, domain = email.split("@", 1)
        local_n = re.sub(r"[^a-z0-9]", "", local.lower())
        domain_n = domain.lower().strip()
        if not local_n or not domain_n:
            return ""
        return f"{local_n}@{domain_n}"


    def _email_matches_name(self, email: str, first: str, last: str) -> bool:
        if not email or "@" not in email:
            return False
        local = email.split("@", 1)[0]
        first_vars = self._ascii_variants(first)
        last_vars = self._ascii_variants(last)
        local_n = re.sub(r"[^a-z0-9]", "", self._normalize_text(local))
        local_parts = [p for p in re.split(r"[._\-+]", self._normalize_text(local)) if p]
        local_parts_n = [re.sub(r"[^a-z0-9]", "", p) for p in local_parts]
        if not first_vars or not last_vars:
            return False

        for first_n in first_vars:
            for last_n in last_vars:
                if local_n == last_n:
                    return True
                first_initial = first_n[0]
                last_initial = last_n[0]
                plausible_patterns = {
                    f"{first_n}{last_n}",
                    f"{last_n}{first_n}",
                    f"{first_initial}{last_n}",
                    f"{last_n}{first_initial}",
                    f"{first_n}{last_initial}",
                    f"{last_initial}{first_n}",
                }
                if local_n in plausible_patterns:
                    return True
                if len(local_parts_n) >= 2 and local_parts_n[0] == first_n and local_parts_n[1] == last_n:
                    return True
                if len(local_parts_n) >= 2 and local_parts_n[0] == last_n and local_parts_n[1] == first_n:
                    return True
        return False

    def _email_looks_name_related_but_uncertain(self, email: str, first: str, last: str) -> bool:
        if not email or "@" not in email:
            return False
        local_n = re.sub(r"[^a-z0-9]", "", self._normalize_text(email.split("@", 1)[0]))
        if not local_n:
            return False
        first_vars = self._ascii_variants(first)
        last_vars = self._ascii_variants(last)
        if any(len(last_n) >= 4 and last_n in local_n for last_n in last_vars):
            return True
        if any(len(first_n) >= 4 and first_n in local_n for first_n in first_vars):
            return True
        return False

    def _is_clear_name_mailbox_mismatch(self, email: str, first: str, last: str) -> bool:
        if not email or "@" not in email:
            return False
        if self._is_generic_mailbox(email):
            return False
        local = self._normalize_text(email.split("@", 1)[0])
        first_vars = self._ascii_variants(first)
        last_vars = self._ascii_variants(last)
        if not first_vars or not last_vars:
            return False
        local_n = re.sub(r"[^a-z0-9]", "", local)
        if self._email_matches_name(email, first, last):
            return False
        if self._email_looks_name_related_but_uncertain(email, first, last):
            return False
        parts = [p for p in re.split(r"[._\-+]", local) if p and p.isalpha()]
        if len(parts) >= 2 and all(len(p) >= 3 for p in parts[:2]):
            # Looks like another concrete person mailbox (e.g. marcel.reyle@...).
            return not any(v in local_n for v in first_vars | last_vars)
        # Single-token mailboxes without name overlap are usually generic/functional.
        if len(parts) <= 1 and len(local_n) >= 4:
            return not any(v in local_n for v in first_vars | last_vars)
        return False

    def _assign_status(
        self,
        *,
        is_person: bool,
        email_ok: bool,
        email_name_ok: bool,
        email_name_uncertain: bool,
        domain_ok: bool,
        phone_ok: bool,
        duplicate: bool,
        generic_name: bool,
        generic_mailbox: bool,
        clear_name_mailbox_mismatch: bool,
    ) -> tuple[str, float, str]:
        if duplicate:
            return STATUS_REJECT, 0.0, "duplicate"
        if generic_name and generic_mailbox:
            return STATUS_REJECT, 0.01, "generic_name_and_mailbox"
        if generic_name:
            return STATUS_REJECT, 0.02, "generic_non_person_name"
        if generic_mailbox and not is_person:
            return STATUS_REJECT, 0.02, "generic_mailbox_non_person"
        if not is_person:
            return STATUS_REJECT, 0.05, "non_person"
        if not email_ok:
            return STATUS_REJECT, 0.1, "invalid_email"
        if generic_mailbox:
            return STATUS_REJECT, 0.1, "generic_mailbox"
        if not domain_ok:
            return STATUS_REJECT, 0.1, "foreign_domain"
        if clear_name_mailbox_mismatch:
            return STATUS_REJECT, 0.1, "name_mailbox_mismatch"
        if not email_name_ok:
            if not email_name_uncertain:
                return STATUS_REJECT, 0.1, "name_mailbox_mismatch"
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

    def validate_candidate(
        self,
        record: dict,
        *,
        medium: str,
        seen_emails_for_person: set[str],
        seen_keys_for_person: set[str],
    ) -> ValidationResult:
        first = str(record.get("vorname") or "").strip()
        last = str(record.get("nachname") or "").strip()
        email = self.clean_email(str(record.get("email") or ""))
        phone = self.normalize_phone(str(record.get("telefon") or ""))

        is_person = self._is_person_name(first, last)
        email_ok = self._is_plausible_email(email)
        generic_name = self._looks_generic_non_person_name(first, last)
        generic_mailbox = self._is_generic_mailbox(email)
        domain_ok = False
        email_name_ok = False
        email_name_uncertain = False
        clear_name_mailbox_mismatch = False
        if email_ok:
            domain_ok = self._domain_allowed(medium, email.split("@", 1)[1])
            email_name_ok = self._email_matches_name(email, first, last)
            email_name_uncertain = self._email_looks_name_related_but_uncertain(email, first, last)
            clear_name_mailbox_mismatch = self._is_clear_name_mailbox_mismatch(email, first, last)

        phone_ok = not str(record.get("telefon") or "").strip() or bool(phone)
        canonical_key = self._canonical_email_key(email)
        duplicate = canonical_key in seen_keys_for_person or any(
            self._emails_are_obvious_typos(existing, email) for existing in seen_emails_for_person
        )

        status, confidence, reason = self._assign_status(
            is_person=is_person,
            email_ok=email_ok,
            email_name_ok=email_name_ok,
            email_name_uncertain=email_name_uncertain,
            domain_ok=domain_ok,
            phone_ok=phone_ok,
            duplicate=duplicate,
            generic_name=generic_name,
            generic_mailbox=generic_mailbox,
            clear_name_mailbox_mismatch=clear_name_mailbox_mismatch,
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
        seen_keys_for_name: dict[tuple[str, str], set[str]] = {}

        for record in records:
            first = str(record.get("vorname") or "").strip().lower()
            last = str(record.get("nachname") or "").strip().lower()
            key = (first, last)
            seen_for_name.setdefault(key, set())
            seen_keys_for_name.setdefault(key, set())

            result = self.validate_candidate(
                record,
                medium=medium,
                seen_emails_for_person=seen_for_name[key],
                seen_keys_for_person=seen_keys_for_name[key],
            )
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
            seen_keys_for_name[key].add(self._canonical_email_key(result.cleaned_email))

        return output
