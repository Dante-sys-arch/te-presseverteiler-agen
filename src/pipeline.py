"""Pipeline entrypoint that wires all modules together for a dry-run scan."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from crawler import Crawler
from parser import Parser, as_records
from diff_engine import DiffEngine
from matcher import Matcher
from scorer import Scorer
from reporter import Reporter
from updater import Updater


def _load_previous_scored(reports_dir: Path) -> dict[str, list[dict]]:
    files = sorted(reports_dir.glob("scan_*.xlsx"))
    if not files:
        return {}
    latest = files[-1]
    try:
        df = pd.read_excel(latest, sheet_name="scored_candidates")
    except Exception:
        return {}

    required = {"medium", "email"}
    if not required.issubset(set(df.columns)):
        return {}

    previous: dict[str, list[dict]] = {}
    for _, row in df.fillna("").iterrows():
        medium = str(row.get("medium", "")).strip()
        if not medium:
            continue
        previous.setdefault(medium, []).append({k: str(v) for k, v in row.to_dict().items()})
    return previous


def run_pipeline(base_dir: Path) -> Path:
    config_dir = base_dir / "config"
    reports_dir = base_dir / "reports"

    master_file = base_dir / "data" / "master" / "MASTER_DACHLILUX.xlsx"
    crawler = Crawler(config_dir / "media_targets.csv", base_dir / "snapshots")
    parser = Parser()
    matcher = Matcher(config_dir / "mandate_mapping.csv", master_file)
    scorer = Scorer()
    diff_engine = DiffEngine()
    reporter = Reporter(reports_dir)
    updater = Updater(master_file, base_dir / "backups")

    raw = crawler.crawl()
    parsed = parser.parse(raw)
    parsed_records = as_records(parsed)
    matched = matcher.match(parsed_records)
    scored = scorer.score(matched)
    previous = _load_previous_scored(reports_dir)
    diffs = diff_engine.compare(scored, previous=previous)
    _update_plan = updater.prepare(approved_changes={k: [] for k in scored})

    return reporter.write(scored, diffs=diffs, crawl_info=raw)


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent.parent
    report = run_pipeline(repository_root)
    print(f"Pipeline dry-run completed. Report: {report}")
