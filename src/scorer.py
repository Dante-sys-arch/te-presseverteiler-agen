"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations

import re


class Scorer:
    """Assign confidence score for each candidate suggestion."""

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
        "new",
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

    GENERIC_ROLE_TERMS = {
        "kontakt",
        "service",
        "vertrieb",
        "datenschutz",
        "impressum",
        "formular",
    }

    GENERIC_MAILBOX_TERMS = {
        "info",
        "kontakt",
        "contact",
        "hello",
        "office",
        "redaktion",
        "kommunikation",
        "presse",
        "newsroom",
        "service",
        "support",
        "admin",
        "mail",
    }

    def _has_real_name(self, record: dict) -> bool:
        first = str(record.get("vorname") or "").strip().lower()
        last = str(record.get("nachname") or "").strip().lower()
        if not first or not last:
            return False
        if len(first) < 2 or len(last) < 2:
            return False
        if first in self.BLOCKED_NAME_TERMS or last in self.BLOCKED_NAME_TERMS:
            return False
        return True

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return False
        normalized = str(phone).strip()
        if normalized.count("/") > 1 or normalized.endswith("/"):
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return False
        if re.fullmatch(r"(?:\d\s+){5,}\d", normalized):
            return False
        if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", normalized):
            return False
        if "." in normalized and re.search(r"\d\s+\d\s+\d\s+\d", normalized):
            return False
        digits = re.sub(r"\D", "", normalized)
        if len(digits) < 7 or len(digits) > 15:
            return False
        if len(set(digits)) <= 2:
            return False
        return True

    def _is_person_email(self, record: dict) -> bool:
        email = str(record.get("email") or "").strip().lower()
        if "@" not in email:
            return False
        local = email.partition("@")[0]
        local_tokens = [token for token in re.split(r"[._+-]", local) if token]
        if not local_tokens:
            return False
        if local_tokens[0] in self.GENERIC_MAILBOX_TERMS:
            return False

        first = str(record.get("vorname") or "").strip().lower()
        last = str(record.get("nachname") or "").strip().lower()
        return (
            first in local_tokens
            or last in local_tokens
            or f"{first}{last}" in local
            or f"{last}{first}" in local
            or (first[:1] and f"{first[:1]}{last}" in local)
        )

    def _score_record(self, record: dict) -> float:
        has_real_name = self._has_real_name(record)
        if not has_real_name:
            return 0.0

        email = str(record.get("email") or "").strip().lower()
        role = str(record.get("rolle") or "").strip().lower()
        phone = str(record.get("telefon") or "").strip()
        has_person_email = self._is_person_email(record)

        if email and not has_person_email:
            return 0.0
        if role in self.GENERIC_ROLE_TERMS:
            return 0.0

        score = 0.2
        if email:
            score += 0.45
        if self._is_plausible_phone(phone):
            score += 0.2
        if role:
            score += 0.1

        fuzzy = float(record.get("fuzzy_match_score") or 0)
        score += min(fuzzy / 100.0, 0.15)

        if record.get("master_match_email"):
            score += 0.1

        if not self._is_plausible_phone(phone) and phone:
            score -= 0.2

        return round(max(0.0, min(score, 1.0)), 2)

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            output: list[dict] = []
            for record in records:
                confidence = self._score_record(record)
                if confidence <= 0.0:
                    continue
                output.append({**record, "confidence": confidence})
            scored[medium] = output
        return scored
