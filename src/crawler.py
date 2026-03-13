"""Crawler module for fetching media target sources.

This module intentionally contains only a lightweight scaffold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv


@dataclass(frozen=True)
class MediaTarget:
    """Represents one source that should be scanned."""

    medium: str
    priority: str
    impressum_url: str


class Crawler:
    """Loads scan targets and returns placeholder snapshots."""

    def __init__(self, targets_file: Path) -> None:
        self.targets_file = targets_file

    def load_targets(self) -> list[MediaTarget]:
        """Load target definitions from CSV configuration."""
        if not self.targets_file.exists():
            return []

        with self.targets_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                MediaTarget(
                    medium=row.get("medium", ""),
                    priority=row.get("priority", ""),
                    impressum_url=row.get("impressum_url", ""),
                )
                for row in reader
            ]

    def crawl(self) -> dict[str, str]:
        """Return a placeholder raw-content mapping per medium.

        NOTE: No real network requests are performed in the scaffold.
        """
        return {target.medium: "" for target in self.load_targets()}
