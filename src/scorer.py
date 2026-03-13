"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations


class Scorer:
    """Assign confidence score for each candidate suggestion."""

    def _score_record(self, record: dict) -> float:
        score = 0.2
        if record.get("email"):
            score += 0.3
        if record.get("vorname") and record.get("nachname"):
            score += 0.2
        if record.get("rolle"):
            score += 0.1

        fuzzy = float(record.get("fuzzy_match_score") or 0)
        score += min(fuzzy / 100.0, 0.2)

        if record.get("master_match_email"):
            score += 0.1

        return round(min(score, 1.0), 2)

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            scored[medium] = [{**record, "confidence": self._score_record(record)} for record in records]
        return scored
