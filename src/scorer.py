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
        digits = re.sub(r"\D", "", normalized)
        if len(digits) < 7 or len(digits) > 15:
            return False
        if len(set(digits)) <= 2:
            return False
        return True

    def _score_record(self, record: dict) -> float:
        has_real_name = self._has_real_name(record)
        if not has_real_name:
            return 0.0

        email = str(record.get("email") or "").strip().lower()
        role = str(record.get("rolle") or "").strip().lower()
        phone = str(record.get("telefon") or "").strip()

        score = 0.25
        if email:
            score += 0.3
        if self._is_plausible_phone(phone):
            score += 0.2
        if role:
            score += 0.1

        fuzzy = float(record.get("fuzzy_match_score") or 0)
        score += min(fuzzy / 100.0, 0.15)

        if record.get("master_match_email"):
            score += 0.1

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
            score -= 0.3

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
