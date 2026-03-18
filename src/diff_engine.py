"""Diff engine for producing user-facing delta rows from match assessments."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiffResult:
    new_contacts: list[dict] = field(default_factory=list)
    vanished_contacts: list[dict] = field(default_factory=list)
    changed_roles: list[dict] = field(default_factory=list)
    changed_contact_data: list[dict] = field(default_factory=list)


class DiffEngine:
    """Generates delta outputs for reporting while retaining backward compatibility."""

    def build_delta_rows(self, matched_rows: list[dict]) -> list[dict]:
        output: list[dict] = []
        for row in matched_rows:
            output.append(
                {
                    "Medium": row.get("medium", ""),
                    "Journalist": row.get("journalist", ""),
                    "Im_Master": row.get("im_master", "Ja"),
                    "Im_Web_gefunden": row.get("im_web_gefunden", "Nein"),
                    "Externer_Hinweis": row.get("externer_hinweis", "kein externer Hinweis"),
                    "Was_ist_anders": row.get("was_ist_anders", ""),
                    "Alter_Stand": row.get("alter_stand", ""),
                    "Neuer_Stand": row.get("neuer_stand", ""),
                    "Quelle": row.get("quelle", ""),
                    "Empfohlene_Aktion": row.get("empfohlene_aktion", "Prüfen"),
                    "Pruefen": row.get("pruefen", "Ja"),
                    "Kommentar": row.get("kommentar", ""),
                }
            )
        return output

    def compare(self, current: dict[str, list[dict]], previous: dict[str, list[dict]]) -> dict[str, DiffResult]:
        diffs: dict[str, DiffResult] = {}
        for medium in sorted(set(current) | set(previous)):
            current_records = {r.get("email", "").lower(): r for r in current.get(medium, []) if r.get("email")}
            previous_records = {r.get("email", "").lower(): r for r in previous.get(medium, []) if r.get("email")}
            new_contacts = [rec for email, rec in current_records.items() if email not in previous_records]
            vanished_contacts = [rec for email, rec in previous_records.items() if email not in current_records]
            changed_roles: list[dict] = []
            changed_contact_data: list[dict] = []
            for email in sorted(set(current_records) & set(previous_records)):
                curr, prev = current_records[email], previous_records[email]
                if (curr.get("rolle") or "").strip() != (prev.get("rolle") or "").strip():
                    changed_roles.append({"email": email, "old": prev.get("rolle", ""), "new": curr.get("rolle", "")})
                if (curr.get("telefon") or "").strip() != (prev.get("telefon") or "").strip():
                    changed_contact_data.append({"email": email, "changes": {"telefon": {"old": prev.get("telefon", ""), "new": curr.get("telefon", "")}}})
            diffs[medium] = DiffResult(new_contacts=new_contacts, vanished_contacts=vanished_contacts, changed_roles=changed_roles, changed_contact_data=changed_contact_data)
        return diffs
