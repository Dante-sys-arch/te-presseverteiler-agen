"""Crawler module for multi-stage medium + journalist research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import os
import re
import time
from urllib.parse import quote_plus, urlparse
from collections import defaultdict

import pandas as pd
import requests
import yaml


USER_AGENT = "te-presseverteiler-agent/2.0 (+respectful-crawler)"
SERPER_ENDPOINT = "https://google.serper.dev/search"
OFFICIAL_PAGE_FIELDS = (
    "impressum_url",
    "redaktion_url",
    "team_url",
    "kontakt_url",
    "autorenseiten_url",
    "ressortseiten_url",
)
BENCHMARK_MEDIA = {
    "Börsen-Zeitung",
    "AssCompact",
    "WirtschaftsWoche (WiWo)",
    "Handelsblatt",
    "The Market (NZZ)",
}


@dataclass(frozen=True)
class MediaTarget:
    medium: str
    priority: str
    official_urls: list[str]


@dataclass(frozen=True)
class CrawlResult:
    medium: str
    source_name: str
    source_type: str
    url: str
    status_code: int | None
    content: str
    snapshot_path: str
    error: str | None = None
    source_priority: str = ""
    search_stage: str = ""
    journalist: str = ""


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
        self._serper_api_key = os.environ.get("SERPER_API_KEY", "").strip()
        # Fallback: also check old Google CSE key names for backward compat
        if not self._serper_api_key:
            self._serper_api_key = os.environ.get("GOOGLE_CSE_API_KEY", "").strip() if os.environ.get("GOOGLE_CSE_API_KEY", "").strip().startswith("") else ""
        self._search_available = bool(self._serper_api_key)

    def _web_search(self, query: str, session: requests.Session, num: int = 5) -> list[dict]:
        """Call Serper.dev API. Returns list of {title, snippet, link}."""
        if not self._search_available:
            return []
        try:
            resp = session.post(
                SERPER_ENDPOINT,
                json={"q": query, "num": num, "gl": "de", "hl": "de"},
                headers={"X-API-KEY": self._serper_api_key, "Content-Type": "application/json"},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                }
                for item in data.get("organic", [])[:num]
            ]
        except Exception:
            return []

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

    def _slug_from_name(self, full_name: str) -> str:
        lowered = re.sub(r"[^a-zA-Z0-9\s-]", "", full_name.lower())
        return re.sub(r"\s+", "-", lowered).strip("-")

    def _profile(self, medium: str, profiles: dict) -> dict:
        defaults = profiles.get("defaults", {}) if isinstance(profiles, dict) else {}
        medium_profile = profiles.get("mediums", {}).get(medium, {}) if isinstance(profiles.get("mediums", {}), dict) else {}
        merged = dict(defaults)
        merged.update(medium_profile)
        return merged

    def _build_official_urls(self, medium: str, base_urls: list[str], profiles: dict) -> list[tuple[str, str]]:
        profile = self._profile(medium, profiles)
        primary_domain = str(profile.get("primary_domain", "")).strip()
        known_paths = profile.get("known_paths") or {}
        preferred_contact_pages = profile.get("preferred_contact_pages") or []

        urls: list[tuple[str, str]] = []
        for url in base_urls:
            urls.append((self._official_source_name(url), url))

        if primary_domain and isinstance(known_paths, dict):
            for source_name, path_values in known_paths.items():
                values = path_values if isinstance(path_values, list) else [path_values]
                for path in values:
                    path = str(path or "").strip()
                    if not path:
                        continue
                    path = path.replace("{query}", "redaktion")
                    final_url = path if path.startswith("http") else f"https://{primary_domain.rstrip('/')}/{path.lstrip('/')}"
                    urls.append((str(source_name), final_url))

        if primary_domain:
            for path in preferred_contact_pages if isinstance(preferred_contact_pages, list) else []:
                clean = str(path or "").strip()
                if clean:
                    clean = clean.replace("{query}", "redaktion")
                    final_url = clean if clean.startswith("http") else f"https://{primary_domain.rstrip('/')}/{clean.lstrip('/')}"
                    urls.append(("kontakt", final_url))

        dedup: list[tuple[str, str]] = []
        seen = set()
        for source_name, url in urls:
            if url not in seen:
                seen.add(url)
                dedup.append((source_name, url))
        return dedup

    def _source_priority(self, medium: str, source_name: str, profiles: dict) -> str:
        profile = self._profile(medium, profiles)
        priority_map = profile.get("source_priority", {}) or {}
        if not isinstance(priority_map, dict):
            return ""
        normalized = str(source_name or "").strip().lower()
        for key, value in priority_map.items():
            if str(key).strip().lower() == normalized:
                return str(value or "")
        return str(priority_map.get("official_medium", ""))

    def _build_domain_research_urls(self, medium: str, profiles: dict, names: list[str]) -> list[tuple[str, str]]:
        profile = self._profile(medium, profiles)
        primary_domain = str(profile.get("primary_domain", "")).strip()
        if not primary_domain:
            return []

        urls: list[tuple[str, str]] = []
        template_map = {
            "team": profile.get("team_patterns", []) or [],
            "autorenseite": profile.get("author_patterns", []) or [],
            "interne_suche": profile.get("search_patterns", []) or [],
        }

        for source_name, templates in template_map.items():
            for template in templates if isinstance(templates, list) else []:
                tpl = str(template or "").strip()
                if not tpl:
                    continue
                for name in names:
                    query = self._normalize_query(name)
                    slug = self._slug_from_name(query)
                    url = tpl
                    if "{domain}" in url:
                        url = url.replace("{domain}", primary_domain)
                    if url.startswith("/"):
                        url = f"https://{primary_domain.rstrip('/')}/{url.lstrip('/')}"
                    url = url.replace("{query}", quote_plus(query))
                    url = url.replace("{name}", quote_plus(query))
                    url = url.replace("{slug}", slug)
                    if "{" in url:
                        continue
                    urls.append((source_name, url))

        dedup: list[tuple[str, str]] = []
        seen = set()
        for source_name, url in urls:
            if url not in seen:
                seen.add(url)
                dedup.append((source_name, url))
        return dedup

    def _build_secondary_search_urls(self, source_name: str, names: list[str]) -> list[tuple[str, str, str]]:
        source = source_name.lower()
        templates = {
            "kress": "https://kress.de/suche?q={query}",
            "turi2": "https://turi2.de/?s={query}",
            "meedia": "https://meedia.de/?s={query}",
        }
        template = templates.get(source)
        if not template:
            return []
        urls: list[tuple[str, str, str]] = []
        for name in names:
            query = quote_plus(self._normalize_query(name))
            urls.append((name, source_name, template.replace("{query}", query)))
        return urls

    def _build_linkedin_urls(self, medium: str, names: list[str], profiles: dict) -> list[tuple[str, str]]:
        domain = str(self._profile(medium, profiles).get("primary_domain", "")).strip()
        urls: list[tuple[str, str]] = []
        for name in names:
            query = quote_plus(f'{name} {medium} LinkedIn')
            urls.append((name, f"https://www.bing.com/search?q={query}"))
            if domain:
                domain_query = quote_plus(f'{name} site:linkedin.com/in "{medium}"')
                urls.append((name, f"https://duckduckgo.com/?q={domain_query}"))
        return urls

    def _build_open_web_urls(self, medium: str, names: list[str], profiles: dict) -> list[tuple[str, str]]:
        domain = str(self._profile(medium, profiles).get("primary_domain", "")).strip()
        patterns = [
            '"{name}" "{medium}"',
            '"{name}" Redaktion',
            '"{name}" Autor',
            '"{name}" Journalist',
            '"{name}" neuer Arbeitgeber',
            '"{name}" kress',
            '"{name}" turi2',
            '"{name}" MEEDIA',
            '"{name}" LinkedIn',
        ]
        if domain:
            patterns.insert(1, '"{name}" site:{domain}')
        urls: list[tuple[str, str]] = []
        for name in names:
            for pattern in patterns:
                query = pattern.format(name=name, medium=medium, domain=domain)
                urls.append((name, f"https://www.bing.com/search?q={quote_plus(query)}"))
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
        allowed = rules.get("levels", {}).get(source_type, {}).get("allowed_domains", [])
        if not allowed:
            return True
        host = (urlparse(url).hostname or "").lower()
        return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed)

    def _fetch(
        self,
        *,
        medium: str,
        source_name: str,
        source_type: str,
        url: str,
        session: requests.Session,
        source_priority: str = "",
        search_stage: str = "",
        journalist: str = "",
    ) -> CrawlResult:
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
            source_priority=source_priority,
            search_stage=search_stage,
            journalist=journalist,
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
        if "suche" in lower or "search" in lower:
            return "interne_suche"
        return "official"

    def crawl(self) -> dict[str, list[CrawlResult]]:
        rules = self._load_yaml(self.source_rules_file)
        results: dict[str, list[CrawlResult]] = {}
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        profiles = self._load_yaml(self.medium_profiles_file)
        contacts_by_medium = self._load_master_contacts_by_medium()

        targets = self.load_targets()
        for target in targets:
            all_contacts = contacts_by_medium.get(target.medium, [])
            domain_research_names = all_contacts[:5]  # Limit domain research to avoid too many page fetches
            candidate_names = all_contacts  # Use ALL journalists for LinkedIn/web search
            official_urls = self._build_official_urls(target.medium, target.official_urls, profiles)
            research_urls = self._build_domain_research_urls(target.medium, profiles, domain_research_names)
            medium_urls = official_urls + research_urls
            if target.medium in BENCHMARK_MEDIA:
                required = {"impressum", "redaktion", "team", "kontakt", "autorenseiten", "ressortseiten", "interne_suche"}
                present = {name for name, _ in medium_urls}
                for source_name, url in official_urls:
                    if source_name in required:
                        present.add(source_name)
                missing_required = required - present
                if missing_required:
                    fallback_urls = self._build_official_urls(target.medium, [], profiles)
                    for source_name, url in fallback_urls:
                        if source_name in missing_required:
                            medium_urls.append((source_name, url))

            for idx, (source_name, url) in enumerate(medium_urls):
                if not self._domain_allowed("official_medium", url, rules):
                    continue
                result = self._fetch(
                    medium=target.medium,
                    source_name=source_name or self._official_source_name(url),
                    source_type="official_medium",
                    url=url,
                    session=session,
                    source_priority=self._source_priority(target.medium, source_name, profiles),
                    search_stage="offizielle_mediumsseiten",
                )
                results.setdefault(target.medium, []).append(result)
                if idx < len(medium_urls) - 1:
                    time.sleep(self.crawl_delay_s)

            for idx, journalist in enumerate(candidate_names):
                if self._search_available:
                    # One LinkedIn query per journalist via Serper
                    query = f'{journalist} {target.medium} LinkedIn'
                    search_results = self._web_search(query, session, num=3)
                    combined_text = "\n".join(
                        f"{r['title']} — {r['snippet']} ({r['link']})"
                        for r in search_results
                    ) if search_results else ""
                    snapshot = self._write_snapshot(f"{target.medium}_linkedin_{self._safe_slug(journalist)}", combined_text)
                    result = CrawlResult(
                        medium=target.medium,
                        source_name="LinkedIn",
                        source_type="linkedin_source",
                        url=f"serper:{query}",
                        status_code=200 if search_results else None,
                        content=combined_text,
                        snapshot_path=str(snapshot),
                        error=None if search_results else "keine Serper-Ergebnisse",
                        search_stage="linkedin",
                        journalist=journalist,
                    )
                    results.setdefault(target.medium, []).append(result)
                    time.sleep(0.3)

                    # One combined web query per journalist via Serper
                    web_query = f'"{journalist}" "{target.medium}" Redakteur OR Journalist OR Autor OR Editor'
                    web_results = self._web_search(web_query, session, num=5)
                    web_text = "\n".join(
                        f"{r['title']} — {r['snippet']} ({r['link']})"
                        for r in web_results
                    ) if web_results else ""
                    snapshot = self._write_snapshot(f"{target.medium}_web_{self._safe_slug(journalist)}", web_text)
                    result = CrawlResult(
                        medium=target.medium,
                        source_name="Websuche",
                        source_type="open_web",
                        url=f"serper:{web_query}",
                        status_code=200 if web_results else None,
                        content=web_text,
                        snapshot_path=str(snapshot),
                        error=None if web_results else "keine Serper-Ergebnisse",
                        search_stage="allgemeine_websuche",
                        journalist=journalist,
                    )
                    results.setdefault(target.medium, []).append(result)
                    time.sleep(0.3)

                    # One Twitter/X bio query per journalist via Serper
                    twitter_query = f'"{journalist}" site:x.com OR site:twitter.com Journalist OR Redakteur OR Reporter'
                    twitter_results = self._web_search(twitter_query, session, num=3)
                    twitter_text = "\n".join(
                        f"{r['title']} — {r['snippet']} ({r['link']})"
                        for r in twitter_results
                    ) if twitter_results else ""
                    if twitter_text:
                        snapshot = self._write_snapshot(f"{target.medium}_twitter_{self._safe_slug(journalist)}", twitter_text)
                        result = CrawlResult(
                            medium=target.medium,
                            source_name="Twitter/X",
                            source_type="social_media",
                            url=f"serper:{twitter_query}",
                            status_code=200,
                            content=twitter_text,
                            snapshot_path=str(snapshot),
                            error=None,
                            search_stage="social_media",
                            journalist=journalist,
                        )
                        results.setdefault(target.medium, []).append(result)
                        time.sleep(0.3)
                else:
                    # Fallback: old Bing-based approach for first 5 only
                    if idx < 5:
                        for _, url in self._build_linkedin_urls(target.medium, [journalist], profiles):
                            result = self._fetch(
                                medium=target.medium,
                                source_name="LinkedIn",
                                source_type="linkedin_source",
                                url=url,
                                session=session,
                                search_stage="linkedin",
                                journalist=journalist,
                            )
                            results.setdefault(target.medium, []).append(result)
                            time.sleep(self.crawl_delay_s)
                        for _, url in self._build_open_web_urls(target.medium, [journalist], profiles):
                            result = self._fetch(
                                medium=target.medium,
                                source_name="Websuche",
                                source_type="open_web",
                                url=url,
                                session=session,
                                search_stage="allgemeine_websuche",
                                journalist=journalist,
                            )
                            results.setdefault(target.medium, []).append(result)
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
                search_stage="branchenquelle",
            )
            results.setdefault(name, []).append(result)
            if idx < len(secondary) - 1:
                time.sleep(self.crawl_delay_s)

        for target in targets:
            candidate_names = contacts_by_medium.get(target.medium, [])
            for source in secondary:
                for journalist, source_name, url in self._build_secondary_search_urls(source["name"], candidate_names):
                    if not self._domain_allowed("industry_source", url, rules):
                        continue
                    result = self._fetch(
                        medium=target.medium,
                        source_name=source_name,
                        source_type="industry_source",
                        url=url,
                        session=session,
                        search_stage="branchenquelle",
                        journalist=journalist,
                    )
                    results.setdefault(target.medium, []).append(result)
                    time.sleep(self.crawl_delay_s)

        return results
