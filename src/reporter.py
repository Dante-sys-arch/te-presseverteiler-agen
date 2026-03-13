"""Reporter module for writing human-reviewable outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json


@dataclass(frozen=True)
class ReportMetadata:
    """Metadata describing a generated report run."""

    generated_at: str
    entry_count: int


class Reporter:
    """Writes a minimal JSON report to the reports folder."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir

    def write(self, payload: dict[str, list[dict]]) -> Path:
        """Create a timestamped report artifact."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.reports_dir / f"scan_{timestamp}.json"
        metadata = ReportMetadata(generated_at=timestamp, entry_count=sum(len(v) for v in payload.values()))

        with report_path.open("w", encoding="utf-8") as handle:
            json.dump({"metadata": metadata.__dict__, "data": payload}, handle, indent=2)

        return report_path
