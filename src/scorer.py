"""Scorer module for assigning confidence values to candidate changes."""

from __future__ import annotations


class Scorer:
    """Adds placeholder confidence values without real scoring heuristics."""

    def score(self, matched_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Attach a static score field to each candidate dictionary."""
        scored: dict[str, list[dict]] = {}
        for medium, records in matched_data.items():
            scored[medium] = [
                {**record, "confidence": 0.0}
                for record in records
            ]
        return scored
