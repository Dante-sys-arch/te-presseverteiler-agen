"""Parser module for transforming raw snapshots into structured records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedContact:
    """Represents one parsed contact candidate."""

    name: str = ""
    role: str = ""
    email: str = ""


class Parser:
    """Converts raw crawl output into structured placeholders."""

    def parse(self, raw_snapshots: dict[str, str]) -> dict[str, list[ParsedContact]]:
        """Parse raw HTML/text to contact candidates (placeholder only)."""
        return {medium: [] for medium in raw_snapshots}
