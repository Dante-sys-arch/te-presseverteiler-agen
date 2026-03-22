"""Pipeline entrypoint wiring crawler, parser, matcher, diffing and reporting."""

from __future__ import annotations

from pathlib import Path

from crawler import Crawler
from parser import Parser
from diff_engine import DiffEngine
from matcher import Matcher
from reporter import Reporter
from updater import Updater


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
    delta_rows = diff_engine.build_delta_rows(matched_rows)
    unscanned_rows = diff_engine.build_unscanned_rows(not_scanned_master_rows)

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
