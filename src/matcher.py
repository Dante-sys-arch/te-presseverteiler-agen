"""Matcher module for linking parsed contacts to master data and mandate mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv

import pandas as pd
from rapidfuzz import fuzz


@dataclass(frozen=True)
class MandateRule:
    """Simple mandate assignment rule based on a ressort tag."""

    ressort_tag: str
    mandat: str


class Matcher:
    """Matches parsed contacts against mandate rules and master workbook."""

    def __init__(self, mapping_file: Path, master_file: Path) -> None:
        self.mapping_file = mapping_file
        self.master_file = master_file

    def load_rules(self) -> list[MandateRule]:
        if not self.mapping_file.exists():
            return []
        with self.mapping_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                MandateRule(
                    ressort_tag=(row.get("ressort_tag") or "").strip(),
                    mandat=(row.get("mandat") or "").strip(),
                )
                for row in reader
                if (row.get("ressort_tag") or "").strip()
            ]

    def load_master_contacts(self) -> list[dict[str, str]]:
        if not self.master_file.exists():
            return []

        sheets = pd.read_excel(self.master_file, sheet_name=None, header=None)
        contacts: list[dict[str, str]] = []
        for _, frame in sheets.items():
            for _, row in frame.iterrows():
                values = ["" if pd.isna(v) else str(v).strip() for v in row.tolist()]
                if len(values) < 5 or "@" not in values[4]:
                    continue
                contacts.append(
                    {
                        "medium": values[0],
                        "anrede": values[1],
                        "vorname": values[2],
                        "nachname": values[3],
                        "email": values[4].lower(),
                        "telefon": values[8] if len(values) > 8 else "",
                    }
                )
        return contacts

    def _mandate_for(self, role: str, rules: list[MandateRule]) -> str:
        role_l = (role or "").lower()
        for rule in rules:
            if rule.ressort_tag.lower() in role_l:
                return rule.mandat
        return ""

    def _fuzzy_match_score(self, record: dict, master_contacts: list[dict[str, str]]) -> tuple[int, str]:
        candidate_name = f"{record.get('vorname', '')} {record.get('nachname', '')}".strip().lower()
        if not candidate_name:
            return (0, "")

        best_score = 0
        best_email = ""
        for master in master_contacts:
            master_name = f"{master.get('vorname', '')} {master.get('nachname', '')}".strip().lower()
            score = fuzz.ratio(candidate_name, master_name)
            if score > best_score:
                best_score = int(score)
                best_email = master.get("email", "")
        return best_score, best_email

    def match(self, parsed_data: dict[str, list[dict]]) -> dict[str, list[dict]]:
        rules = self.load_rules()
        master_contacts = self.load_master_contacts()
        master_by_email = {c["email"]: c for c in master_contacts if c.get("email")}

        enriched: dict[str, list[dict]] = {}
        for medium, records in parsed_data.items():
            enriched_records: list[dict] = []
            for record in records:
                email = (record.get("email") or "").lower()
                existing = master_by_email.get(email)
                fuzzy_score, fuzzy_email = self._fuzzy_match_score(record, master_contacts)
                enriched_records.append(
                    {
                        **record,
                        "mandat": self._mandate_for(record.get("rolle", ""), rules),
                        "master_match_email": existing.get("email", "") if existing else "",
                        "fuzzy_match_score": fuzzy_score,
                        "fuzzy_match_email": fuzzy_email,
                    }
                )
            enriched[medium] = enriched_records
        return enriched
