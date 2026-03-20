"""Crawler module for fetching official and secondary media sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import re
import time
from urllib.parse import urlparse

import requests
import yaml


USER_AGENT = "te-presseverteiler-agent/1.1 (+respectful-crawler)"
OFFICIAL_PAGE_FIELDS = (
    "impressum_url",
    "redaktion_url",
    "team_url",
    "kontakt_url",
    "autorenseiten_url",
    "ressortseiten_url",
)


@dataclass(frozen=True)
class MediaTarget:
    """Represents one medium and all official target pages to scan."""

    medium: str
    priority: str
    official_urls: list[str]


@dataclass(frozen=True)
class CrawlResult:
    """Normalized crawl output for one target URL."""

    medium: str
    source_name: str
    source_type: str
    url: str
    status_code: int | None
    content: str
    snapshot_path: str
    error: str | None = None


class Crawler:
    """Loads scan targets, performs web requests, and saves snapshots."""

    def __init__(
        self,
        targets_file: Path,
        snapshots_dir: Path,
        source_rules_file: Path | None = None,
        secondary_sources_file: Path | None = None,
        crawl_delay_s: float = 1.5,
        timeout_s: float = 20.0,
    ) -> None:
        self.targets_file = targets_file
        self.snapshots_dir = snapshots_dir
        self.source_rules_file = source_rules_file
        self.secondary_sources_file = secondary_sources_file
        self.crawl_delay_s = crawl_delay_s
        self.timeout_s = timeout_s

    def _load_yaml(self, path: Path | None) -> dict:
        if not path or not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data if isinstance(data, dict) else {}

    def _split_urls(self, raw: str) -> list[str]:
        values = [part.strip() for part in re.split(r"[;,\n]", raw or "")]
        return [url for url in values if url]

    def _collect_official_urls(self, row: dict) -> list[str]:
        urls: list[str] = []
        for field in OFFICIAL_PAGE_FIELDS:
            urls.extend(self._split_urls(row.get(field, "")))
        if not urls:
            urls.extend(self._split_urls(row.get("impressum_url", "")))
        return list(dict.fromkeys(urls))

    def load_targets(self) -> list[MediaTarget]:
        """Load medium definitions from CSV configuration."""
        if not self.targets_file.exists():
            return []

        targets: list[MediaTarget] = []
        with self.targets_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                medium = (row.get("medium") or "").strip()
                if not medium:
                    continue
                targets.append(
                    MediaTarget(
                        medium=medium,
                        priority=(row.get("priority") or "").strip(),
                        official_urls=self._collect_official_urls(row),
                    )
                )
        return targets

    def load_secondary_sources(self) -> list[dict[str, str]]:
        config = self._load_yaml(self.secondary_sources_file)
        entries = config.get("sources", []) if isinstance(config, dict) else []
        normalized: list[dict[str, str]] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            for url in entry.get("target_pages", []) or []:
                cleaned = str(url).strip()
                if name and cleaned:
                    normalized.append({"name": name, "url": cleaned})
        return normalized

    def _safe_slug(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_") or "unknown"

    def _write_snapshot(self, key: str, content: str) -> Path:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self._safe_slug(key)}_{timestamp}.html"
        path = self.snapshots_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _domain_allowed(self, source_type: str, url: str, rules: dict) -> bool:
        allowed = (
            rules.get("levels", {})
            .get(source_type, {})
            .get("allowed_domains", [])
        )
        if not allowed:
            return True
        host = (urlparse(url).hostname or "").lower()
        return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed)

    def _fetch(self, *, medium: str, source_name: str, source_type: str, url: str, session: requests.Session) -> CrawlResult:
        status_code: int | None = None
        content = ""
        error: str | None = None
        try:
            response = session.get(url, timeout=self.timeout_s)
            status_code = response.status_code
            response.raise_for_status()
            content = response.text
        except requests.RequestException as exc:
            error = str(exc)

        snapshot = self._write_snapshot(f"{medium}_{source_name}", content)
        return CrawlResult(
            medium=medium,
            source_name=source_name,
            source_type=source_type,
            url=url,
            status_code=status_code,
            content=content,
            snapshot_path=str(snapshot),
            error=error,
        )

    def _official_source_name(self, url: str) -> str:
        lower = str(url or "").lower()
        if "impressum" in lower:
            return "impressum"
        if "kontakt" in lower:
            return "kontakt"
        if "team" in lower:
            return "team"
        if "redaktion" in lower:
            return "redaktion"
        if "autor" in lower:
            return "autorenseite"
        if "ressort" in lower:
            return "ressortseite"
        return "official"

    def crawl(self) -> dict[str, list[CrawlResult]]:
        """Fetch official medium pages and curated industry sources."""
        rules = self._load_yaml(self.source_rules_file)
        results: dict[str, list[CrawlResult]] = {}
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        targets = self.load_targets()
        for target in targets:
            for idx, url in enumerate(target.official_urls):
                if not self._domain_allowed("official_medium", url, rules):
                    continue
                result = self._fetch(
                    medium=target.medium,
                    source_name=self._official_source_name(url),
                    source_type="official_medium",
                    url=url,
                    session=session,
                )
                results.setdefault(target.medium, []).append(result)
                if idx < len(target.official_urls) - 1:
                    time.sleep(self.crawl_delay_s)

        secondary = self.load_secondary_sources()
        for idx, source in enumerate(secondary):
            url = source["url"]
            name = source["name"]
            if not self._domain_allowed("industry_source", url, rules):
                continue
            result = self._fetch(
                medium=name,
                source_name=name,
                source_type="industry_source",
                url=url,
                session=session,
            )
            results.setdefault(name, []).append(result)
            if idx < len(secondary) - 1:
                time.sleep(self.crawl_delay_s)

        return results
