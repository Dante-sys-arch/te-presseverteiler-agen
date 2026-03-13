"""Diff engine module for comparing current and previous parsed states."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiffResult:
    """Tracks additions, updates, and removals for one source."""

    added: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)


class DiffEngine:
    """Produces a structural diff without business rules."""

    def compare(
        self,
        current: dict[str, list[dict]],
        previous: dict[str, list[dict]],
    ) -> dict[str, DiffResult]:
        """Return placeholder diffs keyed by source name."""
        keys = set(current) | set(previous)
        return {key: DiffResult() for key in keys}
