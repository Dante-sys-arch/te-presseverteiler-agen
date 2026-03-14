"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations

from validator import Validator


class Scorer:
    """Applies central validation and keeps only accept/review candidates."""

    def __init__(self) -> None:
        self.validator = Validator()

    def _fuzzy_email_plausible(self, record: dict) -> bool:
        score = float(record.get("fuzzy_match_score") or 0)
        if score < 92:
            return False
        fuzzy_email = self.validator.clean_email(str(record.get("fuzzy_match_email") or ""))
        return bool(fuzzy_email and "@" in fuzzy_email)

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            validated = self.validator.validate_records(medium, records)
            output: list[dict] = []
            for record in validated:
                enriched = dict(record)
                enriched["fuzzy_match_email"] = (
                    record.get("fuzzy_match_email", "") if self._fuzzy_email_plausible(record) else ""
                )
                output.append(enriched)
            scored[medium] = output
        return scored
