"""Crawler module for fetching official medium, journalist-search and secondary sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import re
import time
from urllib.parse import urlparse
from collections import defaultdict

import requests
import yaml
import pandas as pd


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
        medium_profiles_file: Path | None = None,
        master_file: Path | None = None,
        crawl_delay_s: float = 1.5,
        timeout_s: float = 20.0,
    ) -> None:
        self.targets_file = targets_file
        self.snapshots_dir = snapshots_dir
        self.source_rules_file = source_rules_file
        self.secondary_sources_file = secondary_sources_file
        self.medium_profiles_file = medium_profiles_file
        self.master_file = master_file
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
            pages = entry.get("target_pages", []) or entry.get("urls", []) or []
            for url in pages:
                cleaned = str(url).strip()
                if name and cleaned:
                    normalized.append({"name": name, "url": cleaned})
        return normalized

    def _load_master_contacts_by_medium(self) -> dict[str, list[str]]:
        if not self.master_file or not self.master_file.exists():
            return {}
        sheets = pd.read_excel(self.master_file, sheet_name=None, header=None)
        names_by_medium: dict[str, list[str]] = defaultdict(list)
        for _, frame in sheets.items():
            for _, row in frame.iterrows():
                values = ["" if pd.isna(v) else str(v).strip() for v in row.tolist()]
                if len(values) < 5 or "@" not in values[4]:
                    continue
                medium = values[0]
                name = f"{values[2]} {values[3]}".strip()
                if medium and name:
                    names_by_medium[medium].append(name)
        return {k: sorted(set(v)) for k, v in names_by_medium.items()}

    def _normalize_query(self, name: str) -> str:
        return re.sub(r"\s+", " ", name).strip()

    def _build_official_urls(self, medium: str, base_urls: list[str], profiles: dict) -> list[tuple[str, str]]:
        profile = profiles.get("mediums", {}).get(medium, {})
        defaults = profiles.get("defaults", {})
        primary_domain = (profile.get("primary_domain") or "").strip()
        official_paths = profile.get("official_paths") or defaults.get("official_paths") or []
        urls: list[tuple[str, str]] = []
        for url in base_urls:
            urls.append((self._official_source_name(url), url))
        if primary_domain and official_paths:
            for source_name, path in official_paths.items():
                path_list = path if isinstance(path, list) else [path]
                for entry in path_list:
                    cleaned = str(entry or "").strip()
                    if not cleaned:
                        continue
                    if cleaned.startswith("http"):
                        url = cleaned
                    else:
                        url = f"https://{primary_domain.rstrip('/')}/{cleaned.lstrip('/')}"
                    urls.append((source_name, url))
        dedup: list[tuple[str, str]] = []
        seen = set()
        for source_name, url in urls:
            if url not in seen:
                seen.add(url)
                dedup.append((source_name, url))
        return dedup

    def _build_internal_search_urls(self, medium: str, profiles: dict, names: list[str]) -> list[tuple[str, str]]:
        profile = profiles.get("mediums", {}).get(medium, {})
        defaults = profiles.get("defaults", {})
        primary_domain = (profile.get("primary_domain") or "").strip()
        patterns = profile.get("search_patterns") or defaults.get("search_patterns") or []
        urls: list[tuple[str, str]] = []
        for pattern in patterns:
            tpl = str(pattern or "").strip()
            if "{query}" not in tpl:
                continue
            for name in names:
                query = self._normalize_query(name)
                url = tpl.replace("{query}", query.replace(" ", "+"))
                if "{domain}" in url:
                    if not primary_domain:
                        continue
                    url = url.replace("{domain}", primary_domain)
                urls.append(("interne_suche", url))
        return urls

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
        profiles = self._load_yaml(self.medium_profiles_file)
        contacts_by_medium = self._load_master_contacts_by_medium()

        targets = self.load_targets()
        for target in targets:
            official_urls = self._build_official_urls(target.medium, target.official_urls, profiles)
            search_urls = self._build_internal_search_urls(
                target.medium,
                profiles,
                contacts_by_medium.get(target.medium, [])[:5],
            )
            medium_urls = official_urls + search_urls
            for idx, (source_name, url) in enumerate(medium_urls):
                if not self._domain_allowed("official_medium", url, rules):
                    continue
                result = self._fetch(
                    medium=target.medium,
                    source_name=source_name or self._official_source_name(url),
                    source_type="official_medium",
                    url=url,
                    session=session,
                )
                results.setdefault(target.medium, []).append(result)
                if idx < len(medium_urls) - 1:
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
