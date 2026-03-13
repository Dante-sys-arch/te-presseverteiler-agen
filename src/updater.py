"""Updater module for dry-run update planning and backup preparation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class UpdatePlan:
    master_file: str
    backup_file: str
    planned_changes: int
    status: str


class Updater:
    """Prepares update operations without changing source-of-truth files."""

    def __init__(self, master_file: Path, backup_dir: Path) -> None:
        self.master_file = master_file
        self.backup_dir = backup_dir

    def prepare(self, approved_changes: dict[str, list[dict]]) -> UpdatePlan:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"MASTER_DACHLILUX_{stamp}.xlsx.bak"
        backup_path = self.backup_dir / backup_name
        return UpdatePlan(
            master_file=str(self.master_file),
            backup_file=str(backup_path),
            planned_changes=sum(len(v) for v in approved_changes.values()),
            status="dry-run",
        )
