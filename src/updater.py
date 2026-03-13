"""Updater module for applying approved changes to a master file.

No write/update operation is implemented in this scaffold.
"""

from __future__ import annotations

from pathlib import Path


class Updater:
    """Prepares update operations without changing source-of-truth files."""

    def __init__(self, master_file: Path) -> None:
        self.master_file = master_file

    def prepare(self, approved_changes: dict[str, list[dict]]) -> dict[str, str]:
        """Return a summary of what *would* be updated."""
        return {
            "master_file": str(self.master_file),
            "planned_changes": str(sum(len(v) for v in approved_changes.values())),
            "status": "dry-run",
        }
