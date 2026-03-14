"""Reporter module for writing human-reviewable Excel outputs."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

class Reporter:
    """Writes timestamped Excel reports to reports directory."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir

    def write(self, payload: dict[str, list[dict]], diffs: dict[str, object], crawl_info: dict[str, object]) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.reports_dir / f"scan_{timestamp}.xlsx"

        with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
            rows: list[dict] = []
            for medium, records in payload.items():
                for record in records:
                    cleaned = dict(record)
                    cleaned["telefon"] = str(cleaned.get("telefon") or "").strip()
                    if cleaned.get("status") == "reject":
                        continue
                    rows.append({"medium": medium, **cleaned})
            pd.DataFrame(rows).to_excel(writer, sheet_name="scored_candidates", index=False)

            diff_rows: list[dict] = []
            for medium, diff in diffs.items():
                data = asdict(diff)
                diff_rows.append(
                    {
                        "medium": medium,
                        "new_contacts": len(data.get("new_contacts", [])),
                        "vanished_contacts": len(data.get("vanished_contacts", [])),
                        "changed_roles": len(data.get("changed_roles", [])),
                        "changed_contact_data": len(data.get("changed_contact_data", [])),
                    }
                )
            pd.DataFrame(diff_rows).to_excel(writer, sheet_name="diff_summary", index=False)

            crawl_rows: list[dict] = []
            for medium, info in crawl_info.items():
                crawl_rows.append(
                    {
                        "medium": medium,
                        "url": getattr(info, "url", ""),
                        "status_code": getattr(info, "status_code", ""),
                        "snapshot_path": getattr(info, "snapshot_path", ""),
                        "error": getattr(info, "error", ""),
                    }
                )
            pd.DataFrame(crawl_rows).to_excel(writer, sheet_name="crawl_log", index=False)

        return report_path
