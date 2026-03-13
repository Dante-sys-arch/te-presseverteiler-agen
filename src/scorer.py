"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations


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
    }

    def _has_real_name(self, record: dict) -> bool:
        first = str(record.get("vorname") or "").strip().lower()
        last = str(record.get("nachname") or "").strip().lower()
        if not first or not last:
            return False
        if first in self.BLOCKED_NAME_TERMS or last in self.BLOCKED_NAME_TERMS:
            return False
        return True

    def _score_record(self, record: dict) -> float:
        has_real_name = self._has_real_name(record)
        score = 0.1 if has_real_name else 0.0
        if record.get("email"):
            score += 0.3
        if has_real_name:
            score += 0.25
        else:
            score -= 0.15
        if record.get("rolle"):
            score += 0.1

        fuzzy = float(record.get("fuzzy_match_score") or 0)
        score += min(fuzzy / 100.0, 0.2)

        if record.get("master_match_email"):
            score += 0.1

        if not has_real_name:
            score = min(score, 0.35)

        return round(max(0.0, min(score, 1.0)), 2)

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            scored[medium] = [{**record, "confidence": self._score_record(record)} for record in records]
        return scored
