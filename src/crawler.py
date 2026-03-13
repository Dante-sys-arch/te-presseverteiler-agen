"""Crawler module for fetching media target sources and persisting snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import re
import time

import requests


USER_AGENT = "te-presseverteiler-agent/1.0 (+respectful-crawler)"


@dataclass(frozen=True)
class MediaTarget:
    """Represents one source that should be scanned."""

    medium: str
    priority: str
    impressum_url: str


@dataclass(frozen=True)
class CrawlResult:
    """Normalized crawl output for one target."""

    medium: str
    url: str
    status_code: int | None
    content: str
    snapshot_path: str
    error: str | None = None


class Crawler:
    """Loads scan targets, performs web requests, and saves snapshots."""

    def __init__(self, targets_file: Path, snapshots_dir: Path, crawl_delay_s: float = 1.5, timeout_s: float = 20.0) -> None:
        self.targets_file = targets_file
        self.snapshots_dir = snapshots_dir
        self.crawl_delay_s = crawl_delay_s
        self.timeout_s = timeout_s

    def load_targets(self) -> list[MediaTarget]:
        """Load target definitions from CSV configuration."""
        if not self.targets_file.exists():
            return []

        with self.targets_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            targets: list[MediaTarget] = []
            for row in reader:
                medium = (row.get("medium") or "").strip()
                url = (row.get("impressum_url") or "").strip()
                if not medium:
                    continue
                targets.append(
                    MediaTarget(
                        medium=medium,
                        priority=(row.get("priority") or "").strip(),
                        impressum_url=url,
                    )
                )
            return targets

    def _safe_slug(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_") or "unknown"

    def _write_snapshot(self, medium: str, content: str) -> Path:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self._safe_slug(medium)}_{timestamp}.html"
        path = self.snapshots_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def crawl(self) -> dict[str, CrawlResult]:
        """Fetch each target page with respectful rate limiting and save snapshots."""
        results: dict[str, CrawlResult] = {}
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        targets = self.load_targets()
        for idx, target in enumerate(targets):
            status_code: int | None = None
            content = ""
            error: str | None = None

            if target.impressum_url:
                try:
                    response = session.get(target.impressum_url, timeout=self.timeout_s)
                    status_code = response.status_code
                    response.raise_for_status()
                    content = response.text
                except requests.RequestException as exc:
                    error = str(exc)
            else:
                error = "missing impressum_url"

            snapshot_path = self._write_snapshot(target.medium, content)
            results[target.medium] = CrawlResult(
                medium=target.medium,
                url=target.impressum_url,
                status_code=status_code,
                content=content,
                snapshot_path=str(snapshot_path),
                error=error,
            )

            if idx < len(targets) - 1:
                time.sleep(self.crawl_delay_s)

        return results
