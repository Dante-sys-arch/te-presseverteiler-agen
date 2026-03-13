"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations

import re

from validator import ORGANIZATION_TERMS, Validator


class Scorer:
    """Assign confidence score for each candidate suggestion."""

    def __init__(self) -> None:
        self.validator = Validator()

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
        if first in ORGANIZATION_TERMS or last in ORGANIZATION_TERMS:
            return False
        return True

    def _is_plausible_phone(self, phone: str) -> bool:
        if not phone:
            return False
        normalized = str(phone).strip()
        if normalized.count("/") > 1 or normalized.endswith("/"):
            return False
        if not re.fullmatch(r"[\d\s\-+()/\.]+", normalized):
            return False
        digits = re.sub(r"\D", "", normalized)
        if len(digits) < 7 or len(digits) > 15:
            return False
        if re.search(r"\d+\.\d+", normalized):
            return False
        if normalized.isdigit() and len(digits) < 10:
            return False
        if len(set(digits)) <= 2:
            return False
        return True

    def _email_matches_name(self, record: dict) -> bool:
        return self.validator._email_matches_name(  # noqa: SLF001
            str(record.get("email") or ""),
            str(record.get("vorname") or ""),
            str(record.get("nachname") or ""),
        )

    def _fuzzy_email_plausible(self, record: dict, medium: str) -> bool:
        score = float(record.get("fuzzy_match_score") or 0)
        fuzzy_email = str(record.get("fuzzy_match_email") or "").strip().lower()
        first = str(record.get("vorname") or "").strip()
        last = str(record.get("nachname") or "").strip()
        medium_norm = re.sub(r"[^a-z0-9]", "", medium.lower())
        if score < 92 or not fuzzy_email:
            return False
        if not self.validator._email_matches_name(fuzzy_email, first, last):  # noqa: SLF001
            return False
        domain = fuzzy_email.split("@", 1)[1] if "@" in fuzzy_email else ""
        domain_norm = re.sub(r"[^a-z0-9]", "", domain)
        return not medium_norm or medium_norm[:6] in domain_norm or domain_norm[:6] in medium_norm

    def _score_record(self, record: dict, medium: str) -> tuple[float, bool]:
        validation = self.validator.validate_record(record)
        if not validation.is_valid:
            return 0.0, False

        if not self._has_real_name(record):
            return 0.0, False

        email = str(record.get("email") or "").strip().lower()
        role = str(record.get("rolle") or "").strip().lower()
        phone = str(record.get("telefon") or "").strip()

        score = 0.35
        email_match = bool(email) and self._email_matches_name(record)
        if email_match:
            score += 0.35
        else:
            score -= 0.55

        plausible_phone = self._is_plausible_phone(phone)
        if plausible_phone:
            score += 0.15
        elif phone:
            score -= 0.45

        if role:
            score += 0.05

        fuzzy_is_plausible = self._fuzzy_email_plausible(record, medium)
        if fuzzy_is_plausible:
            score += 0.1

        if record.get("master_match_email"):
            score += 0.05

        if not self._has_real_name(record):
            score -= 0.7

        low_quality_penalty_terms = {
            "kontakt",
            "service",
            "vertrieb",
            "datenschutz",
            "impressum",
            "formular",
            "redaktion",
        }
        if role in low_quality_penalty_terms:
            score -= 0.25

        return round(max(0.0, min(score, 1.0)), 2), fuzzy_is_plausible

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            output: list[dict] = []
            for record in records:
                confidence, fuzzy_ok = self._score_record(record, medium)
                if confidence <= 0.0:
                    continue
                enriched = {**record, "confidence": confidence}
                enriched["fuzzy_match_email"] = record.get("fuzzy_match_email", "") if fuzzy_ok else ""
                output.append(enriched)
            scored[medium] = output
        return scored
