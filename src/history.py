"""Historischer Vergleich: Nur echte Veränderungen seit dem letzten Scan melden.

Speichert nach jedem Lauf einen Snapshot des aktuellen Zustands.
Beim nächsten Lauf wird verglichen: Was hat sich geändert?
Nur echte Änderungen werden im Report markiert.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


HISTORY_DIR = Path("history")


def _build_snapshot(delta_rows: list[dict]) -> dict[str, dict]:
    """Build a snapshot keyed by Medium+Journalist with their current status."""
    snapshot = {}
    for row in delta_rows:
        medium = str(row.get("Medium", ""))
        journalist = str(row.get("Journalist", ""))
        if not medium or not journalist:
            continue
        key = f"{medium}|||{journalist}"
        snapshot[key] = {
            "medium": medium,
            "journalist": journalist,
            "was_ist_anders": str(row.get("Was_ist_anders", "")),
            "neues_medium_hinweis": str(row.get("Neues_Medium_Hinweis", "")),
            "gefunden_bei": str(row.get("Gefunden_bei", "")),
            "im_web_gefunden": str(row.get("Im_Web_gefunden", "")),
            "empfohlene_aktion": str(row.get("Empfohlene_Aktion", "")),
        }
    return snapshot


def save_snapshot(delta_rows: list[dict]) -> Path:
    """Save today's scan results as a JSON snapshot for future comparison."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    snapshot = _build_snapshot(delta_rows)
    path = HISTORY_DIR / f"snapshot_{timestamp}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_previous_snapshot() -> dict[str, dict] | None:
    """Load the most recent snapshot before today."""
    if not HISTORY_DIR.exists():
        return None
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True)
    for path in snapshots:
        date_part = path.stem.replace("snapshot_", "")
        if date_part < today:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    return None


def annotate_changes(delta_rows: list[dict]) -> list[dict]:
    """Compare current results with previous snapshot and add change annotations.

    Adds two fields to each row:
    - Veraenderung_seit_gestern: NEU | VERAENDERT | UNVERAENDERT | ERSTLAUF
    - Veraenderung_detail: What exactly changed
    """
    previous = load_previous_snapshot()

    if previous is None:
        # First run — mark everything as ERSTLAUF
        for row in delta_rows:
            row["Veraenderung_seit_gestern"] = "ERSTLAUF"
            row["Veraenderung_detail"] = "Erster Scan, kein Vergleich möglich"
        return delta_rows

    for row in delta_rows:
        medium = str(row.get("Medium", ""))
        journalist = str(row.get("Journalist", ""))
        key = f"{medium}|||{journalist}"

        prev = previous.get(key)
        if prev is None:
            row["Veraenderung_seit_gestern"] = "NEU"
            row["Veraenderung_detail"] = "Journalist erstmals im Scan"
            continue

        # Compare key fields
        changes = []
        curr_status = str(row.get("Was_ist_anders", ""))
        prev_status = str(prev.get("was_ist_anders", ""))
        if curr_status != prev_status:
            changes.append(f"Status: {prev_status} → {curr_status}")

        curr_medium = str(row.get("Neues_Medium_Hinweis", ""))
        prev_medium = str(prev.get("neues_medium_hinweis", ""))
        if curr_medium != prev_medium and curr_medium:
            changes.append(f"Neues Medium: {prev_medium or '(leer)'} → {curr_medium}")

        curr_gefunden = str(row.get("Gefunden_bei", ""))
        prev_gefunden = str(prev.get("gefunden_bei", ""))
        if curr_gefunden != prev_gefunden and curr_gefunden:
            changes.append(f"Gefunden bei: {prev_gefunden or '(leer)'} → {curr_gefunden}")

        if changes:
            row["Veraenderung_seit_gestern"] = "VERAENDERT"
            row["Veraenderung_detail"] = "; ".join(changes)
        else:
            row["Veraenderung_seit_gestern"] = "UNVERAENDERT"
            row["Veraenderung_detail"] = ""

    return delta_rows
