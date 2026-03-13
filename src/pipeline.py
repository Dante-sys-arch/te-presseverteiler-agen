"""Pipeline entrypoint that wires all scaffold modules together."""

from __future__ import annotations

from pathlib import Path

from crawler import Crawler
from parser import Parser
from diff_engine import DiffEngine
from matcher import Matcher
from scorer import Scorer
from reporter import Reporter
from updater import Updater


def run_pipeline(base_dir: Path) -> Path:
    """Execute a dry-run scan pipeline and return the report path."""
    config_dir = base_dir / "config"
    reports_dir = base_dir / "reports"

    crawler = Crawler(config_dir / "media_targets.csv")
    parser = Parser()
    matcher = Matcher(config_dir / "mandate_mapping.csv")
    scorer = Scorer()
    diff_engine = DiffEngine()
    reporter = Reporter(reports_dir)
    updater = Updater(base_dir / "data" / "master" / "MASTER_DACHLILUX.xls")

    raw = crawler.crawl()
    parsed = parser.parse(raw)
    matched = matcher.match({k: [item.__dict__ for item in v] for k, v in parsed.items()})
    scored = scorer.score(matched)
    _diffs = diff_engine.compare(scored, previous={})
    _update_plan = updater.prepare(approved_changes={})

    return reporter.write(scored)


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent.parent
    report = run_pipeline(repository_root)
    print(f"Pipeline dry-run completed. Report: {report}")
