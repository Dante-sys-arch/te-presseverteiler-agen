"""Historischer Vergleich, Deduplizierung und Trend-Erkennung.

- Speichert nach jedem Lauf einen Snapshot
- Vergleicht mit dem Vortag: Was hat sich geändert?
- Dedupliziert: Bereits bekannte Medienwechsel werden nicht erneut als NEU gemeldet
- Trend: Journalist seit X Tagen nicht mehr gefunden → Warnstufe erhöhen
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


HISTORY_DIR = Path("history")
KNOWN_CHANGES_FILE = HISTORY_DIR / "known_changes.json"


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
            "konfidenz_score": str(row.get("Konfidenz_Score", "")),
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


def _load_known_changes() -> dict[str, dict]:
    """Load the persistent registry of already-reported changes."""
    if not KNOWN_CHANGES_FILE.exists():
        return {}
    try:
        data = json.loads(KNOWN_CHANGES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_known_changes(known: dict[str, dict]) -> None:
    """Save the persistent registry of already-reported changes."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    KNOWN_CHANGES_FILE.write_text(json.dumps(known, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _count_consecutive_not_found(key: str) -> int:
    """Count how many consecutive days a journalist was 'not found'."""
    if not HISTORY_DIR.exists():
        return 0
    snapshots = sorted(HISTORY_DIR.glob("snapshot_*.json"), reverse=True)
    count = 0
    for path in snapshots:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = data.get(key, {})
            status = str(entry.get("was_ist_anders", ""))
            if "Nichts Belastbares" in status or "schwacher Hinweis" in status or "nicht bestaetigt" in status:
                count += 1
            else:
                break
        except Exception:
            break
    return count


def annotate_changes(delta_rows: list[dict]) -> list[dict]:
    """Compare current results with previous snapshot and add change annotations.

    Adds fields:
    - Veraenderung_seit_gestern: NEU | VERAENDERT | UNVERAENDERT | ERSTLAUF | BEREITS_BEKANNT
    - Veraenderung_detail: What exactly changed
    - Tage_nicht_gefunden: How many consecutive days not found (trend)
    """
    previous = load_previous_snapshot()
    known = _load_known_changes()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if previous is None:
        for row in delta_rows:
            row["Veraenderung_seit_gestern"] = "ERSTLAUF"
            row["Veraenderung_detail"] = "Erster Scan, kein Vergleich möglich"
            row["Tage_nicht_gefunden"] = 0
        return delta_rows

    for row in delta_rows:
        medium = str(row.get("Medium", ""))
        journalist = str(row.get("Journalist", ""))
        key = f"{medium}|||{journalist}"
        was = str(row.get("Was_ist_anders", ""))

        # --- Trend: consecutive days not found ---
        if "Nichts Belastbares" in was or "schwacher Hinweis" in was:
            row["Tage_nicht_gefunden"] = _count_consecutive_not_found(key) + 1
        else:
            row["Tage_nicht_gefunden"] = 0

        # --- Deduplication: already known change? ---
        is_change = "Medienwechsel" in was or "anderem Medium" in was or "LinkedIn bestaetigt" in was
        if is_change and key in known:
            prev_change = known[key]
            prev_medium = str(prev_change.get("neues_medium", ""))
            curr_medium = str(row.get("Neues_Medium_Hinweis", "") or row.get("Gefunden_bei", ""))
            if prev_medium and curr_medium and prev_medium.lower() == curr_medium.lower():
                row["Veraenderung_seit_gestern"] = "BEREITS_BEKANNT"
                row["Veraenderung_detail"] = f"Medienwechsel zu {prev_medium} bereits am {prev_change.get('datum', '?')} gemeldet"
                continue

        # --- Register new changes ---
        if is_change:
            known[key] = {
                "neues_medium": str(row.get("Neues_Medium_Hinweis", "") or row.get("Gefunden_bei", "")),
                "datum": today,
                "was": was,
            }

        # --- Compare with yesterday ---
        prev = previous.get(key)
        if prev is None:
            row["Veraenderung_seit_gestern"] = "NEU"
            row["Veraenderung_detail"] = "Journalist erstmals im Scan"
            continue

        changes = []
        curr_status = was
        prev_status = str(prev.get("was_ist_anders", ""))
        if curr_status != prev_status:
            changes.append(f"Status: {prev_status[:40]} → {curr_status[:40]}")

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

    _save_known_changes(known)
    return delta_rows
