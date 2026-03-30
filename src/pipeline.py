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
from llm_evaluator import evaluate_journalist_snippets, LLMJournalistStatus, fetch_and_read_url
from history import annotate_changes, save_snapshot
from email_patterns import learn_email_patterns, generate_update_suggestions


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
    pages_read = 0
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

            # === Deep Page Reading (Punkt 2) ===
            # For the most important cases, fetch the actual URLs from snippets
            # and read the full page content with Claude
            deep_insights = []
            if linkedin_snippets or web_snippets:
                all_snippets = linkedin_snippets + web_snippets
                # Extract URLs from snippets (format: "Title — Snippet (URL)")
                import re
                urls_found = []
                for snippet in all_snippets:
                    url_match = re.search(r"\(https?://[^\s)]+\)", snippet)
                    if url_match:
                        url = url_match.group(0).strip("()")
                        urls_found.append(url)
                # Read top 2 most promising URLs
                for url in urls_found[:2]:
                    insight = fetch_and_read_url(url, journalist, medium)
                    if insight:
                        deep_insights.append(insight)
                        pages_read += 1

            # Add deep insights to snippets for LLM evaluation
            enriched_web = web_snippets + [f"[Seitenanalyse] {ins}" for ins in deep_insights]

            status = evaluate_journalist_snippets(
                journalist=journalist,
                master_medium=medium,
                master_email=str(row.get("alter_stand", "")).split("|")[0].strip(),
                master_ressort=str(row.get("alter_stand", "")).split("|")[-1].strip() if "|" in str(row.get("alter_stand", "")) else "",
                linkedin_snippets=linkedin_snippets,
                web_snippets=enriched_web,
                official_page_names=official_names,
            )

            matched_rows[i] = _apply_llm_status(row, status)
            llm_reviewed += 1

        print(f"[LLM] {llm_reviewed} Journalisten durch Claude verifiziert, {pages_read} Seiten gelesen")
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

    # === Email Patterns & Master Update Suggestions ===
    master_contacts = matcher.load_master_contacts()
    email_patterns = learn_email_patterns(master_contacts)
    update_suggestions = generate_update_suggestions(delta_rows, master_contacts, email_patterns)
    print(f"[Updates] {len(email_patterns)} E-Mail-Muster gelernt, {len(update_suggestions)} Update-Vorschläge generiert")

    _update_plan = updater.prepare(approved_changes={"delta": delta_rows})
    report_path = reporter.write(
        delta_rows,
        crawl_info=raw,
        unscanned_rows=unscanned_rows,
        medium_recherche_detail_rows=medium_recherche_detail_rows,
        matching_detail_rows=matching_detail_rows,
        web_research_detail_rows=web_research_detail_rows,
        treffer_auswertung_detail_rows=treffer_auswertung_detail_rows,
        update_suggestions=update_suggestions,
    )

    # === Dashboard JSON Export ===
    import json
    from datetime import datetime, timezone
    docs_dir = base_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    dashboard_data = {
        "scan_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(delta_rows),
        "rows": delta_rows,
    }
    (docs_dir / "dashboard.json").write_text(
        json.dumps(dashboard_data, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"[Dashboard] JSON exportiert: docs/dashboard.json ({len(delta_rows)} Zeilen)")

    return report_path


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent.parent
    report = run_pipeline(repository_root)
    print(f"Pipeline dry-run completed. Report: {report}")
