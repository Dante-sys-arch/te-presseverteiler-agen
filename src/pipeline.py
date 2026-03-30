"""Pipeline entrypoint wiring crawler, parser, matcher, diffing and reporting."""

from __future__ import annotations

import os
from pathlib import Path

from crawler import Crawler
from parser import Parser
from diff_engine import DiffEngine
from matcher import Matcher
from reporter import Reporter
from updater import Updater
from llm_evaluator import evaluate_journalist_snippets, LLMJournalistStatus
from history import annotate_changes, save_snapshot


def _needs_llm_review(row: dict) -> bool:
    """Determine if a row should be reviewed by Claude for higher precision."""
    change = str(row.get("was_ist_anders", ""))
    return any(keyword in change for keyword in (
        "Medienwechsel",
        "anderem Medium",
        "schwacher Hinweis",
        "Weitere Quelle",
        "LinkedIn bestaetigt",
    ))


def _collect_snippets(journalist: str, medium: str, structured: dict) -> tuple[list[str], list[str], list[str]]:
    """Collect LinkedIn snippets, web snippets, and official page name matches for a journalist."""
    payload = structured.get(medium, {})
    industry_docs = payload.get("industry_documents", [])
    official_docs = payload.get("official_documents", [])

    name_lower = journalist.lower()
    linkedin_snippets = []
    web_snippets = []
    official_names = []

    for doc in industry_docs:
        text = str(doc.get("text", ""))[:500]
        if name_lower in text.lower():
            source_type = str(doc.get("source_type", ""))
            if source_type == "linkedin_source":
                linkedin_snippets.append(text)
            elif source_type == "open_web":
                web_snippets.append(text)

    for doc in official_docs:
        text = str(doc.get("text", ""))
        if name_lower in text.lower():
            official_names.append(str(doc.get("page_type", "official")))

    return linkedin_snippets, web_snippets, official_names


def _apply_llm_status(row: dict, status: LLMJournalistStatus) -> dict:
    """Override row fields based on LLM evaluation."""
    row = dict(row)  # copy

    if status.status == "bestaetigt":
        row["was_ist_anders"] = "Journalist bestaetigt"
        row["kommentar"] = f"LLM-Prüfung: {status.begruendung}"
        row["pruefen"] = "Nein"
        if status.aktuelle_rolle:
            row["neuer_stand"] = status.aktuelle_rolle
    elif status.status == "gewechselt" and status.konfidenz in ("mittel", "hoch"):
        row["was_ist_anders"] = "Wahrscheinlicher Medienwechsel; Bei anderem Medium gefunden"
        row["neues_medium_hinweis"] = status.neues_medium
        row["gefunden_bei"] = status.neues_medium
        row["kommentar"] = f"LLM-Prüfung ({status.konfidenz}): {status.begruendung}"
        row["pruefen"] = "Ja"
    elif status.status == "gewechselt" and status.konfidenz == "niedrig":
        row["was_ist_anders"] = "Weitere Quelle pruefen"
        row["neues_medium_hinweis"] = status.neues_medium
        row["kommentar"] = f"LLM-Prüfung (niedrige Konfidenz): {status.begruendung}"
        row["pruefen"] = "Ja"
    elif status.status == "nicht_gefunden":
        row["was_ist_anders"] = "Nichts Belastbares gefunden"
        row["kommentar"] = f"LLM-Prüfung: {status.begruendung}"
        row["pruefen"] = "Ja"
    else:  # unklar
        row["was_ist_anders"] = "Weitere Quelle pruefen"
        row["kommentar"] = f"LLM-Prüfung: {status.begruendung}"
        row["pruefen"] = "Ja"

    row["quellenbasis"] = f"LLM-verifiziert ({status.quellen_anzahl} Quellen, {status.konfidenz})"
    return row


def run_pipeline(base_dir: Path) -> Path:
    config_dir = base_dir / "config"
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    master_file = base_dir / "data" / "master" / "MASTER_DACHLILUX.xlsx"
    crawler = Crawler(
        config_dir / "media_targets.csv",
        base_dir / "snapshots",
        source_rules_file=config_dir / "source_rules.yaml",
        secondary_sources_file=config_dir / "secondary_sources.yaml",
        medium_profiles_file=config_dir / "medium_profiles.yaml",
        master_file=master_file,
    )
    parser = Parser()
    matcher = Matcher(config_dir / "mandate_mapping.csv", master_file)
    diff_engine = DiffEngine()
    reporter = Reporter(reports_dir)
    updater = Updater(master_file, base_dir / "backups")

    raw = crawler.crawl()
    scan_scope_media = {
        medium
        for medium, infos in raw.items()
        if any(getattr(info, "source_type", "") == "official_medium" for info in infos)
    }
    structured = parser.parse_structured(raw)
    medium_recherche_detail_rows = [
        row
        for payload in structured.values()
        for row in payload.get("source_diagnostics", [])
    ]
    matched_rows, not_scanned_master_rows = matcher.build_delta_inputs(structured, scan_scope_media=scan_scope_media)
    matching_detail_rows = matcher.get_matching_detail_rows()
    web_research_detail_rows = matcher.get_web_research_detail_rows()
    treffer_auswertung_detail_rows = matcher.get_treffer_auswertung_detail_rows()

    # === LLM Evaluation Step ===
    llm_available = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    llm_reviewed = 0
    if llm_available:
        print("[LLM] Claude-basierte Verifizierung aktiv")
        for i, row in enumerate(matched_rows):
            if not _needs_llm_review(row):
                continue

            journalist = str(row.get("journalist", ""))
            medium = str(row.get("medium", ""))
            if not journalist or journalist == "(Medium-Ebene)":
                continue

            linkedin_snippets, web_snippets, official_names = _collect_snippets(
                journalist, medium, structured
            )

            status = evaluate_journalist_snippets(
                journalist=journalist,
                master_medium=medium,
                master_email=str(row.get("alter_stand", "")).split("|")[0].strip(),
                master_ressort=str(row.get("alter_stand", "")).split("|")[-1].strip() if "|" in str(row.get("alter_stand", "")) else "",
                linkedin_snippets=linkedin_snippets,
                web_snippets=web_snippets,
                official_page_names=official_names,
            )

            matched_rows[i] = _apply_llm_status(row, status)
            llm_reviewed += 1

        print(f"[LLM] {llm_reviewed} Journalisten durch Claude verifiziert")
    else:
        print("[LLM] Kein ANTHROPIC_API_KEY — überspringe LLM-Verifizierung")

    delta_rows = diff_engine.build_delta_rows(matched_rows)
    unscanned_rows = diff_engine.build_unscanned_rows(not_scanned_master_rows)

    # === Historical Comparison ===
    delta_rows = annotate_changes(delta_rows)
    snapshot_path = save_snapshot(delta_rows)
    changed = sum(1 for r in delta_rows if r.get("Veraenderung_seit_gestern") in ("NEU", "VERAENDERT"))
    unchanged = sum(1 for r in delta_rows if r.get("Veraenderung_seit_gestern") == "UNVERAENDERT")
    print(f"[History] {changed} verändert, {unchanged} unverändert seit letztem Scan. Snapshot: {snapshot_path}")

    _update_plan = updater.prepare(approved_changes={"delta": delta_rows})
    report_path = reporter.write(
        delta_rows,
        crawl_info=raw,
        unscanned_rows=unscanned_rows,
        medium_recherche_detail_rows=medium_recherche_detail_rows,
        matching_detail_rows=matching_detail_rows,
        web_research_detail_rows=web_research_detail_rows,
        treffer_auswertung_detail_rows=treffer_auswertung_detail_rows,
    )
    return report_path


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent.parent
    report = run_pipeline(repository_root)
    print(f"Pipeline dry-run completed. Report: {report}")
