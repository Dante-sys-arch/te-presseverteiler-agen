"""Matcher module for linking parsed contacts to mandate mappings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv


@dataclass(frozen=True)
class MandateRule:
    """Simple mandate assignment rule based on a ressort tag."""

    ressort_tag: str
    mandat: str


class Matcher:
    """Loads mapping rules and applies a no-op matching scaffold."""

    def __init__(self, mapping_file: Path) -> None:
        self.mapping_file = mapping_file

    def load_rules(self) -> list[MandateRule]:
        """Load mapping rules from CSV configuration."""
        if not self.mapping_file.exists():
            return []

        with self.mapping_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                MandateRule(
                    ressort_tag=row.get("ressort_tag", ""),
                    mandat=row.get("mandat", ""),
                )
                for row in reader
            ]

    def match(self, parsed_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Return parsed data unchanged (matching logic intentionally omitted)."""
        _rules = self.load_rules()
        return parsed_data
