"""Diff engine for comparing current and previous contact snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiffResult:
    """Tracks additions, updates, and removals for one source."""

    new_contacts: list[dict] = field(default_factory=list)
    vanished_contacts: list[dict] = field(default_factory=list)
    changed_roles: list[dict] = field(default_factory=list)
    changed_contact_data: list[dict] = field(default_factory=list)


class DiffEngine:
    """Produces meaningful diffs based on email identity."""

    def compare(
        self,
        current: dict[str, list[dict]],
        previous: dict[str, list[dict]],
    ) -> dict[str, DiffResult]:
        diffs: dict[str, DiffResult] = {}
        for medium in sorted(set(current) | set(previous)):
            current_records = {r.get("email", "").lower(): r for r in current.get(medium, []) if r.get("email")}
            previous_records = {r.get("email", "").lower(): r for r in previous.get(medium, []) if r.get("email")}

            new_contacts = [rec for email, rec in current_records.items() if email not in previous_records]
            vanished_contacts = [rec for email, rec in previous_records.items() if email not in current_records]

            changed_roles: list[dict] = []
            changed_contact_data: list[dict] = []

            for email in sorted(set(current_records) & set(previous_records)):
                curr = current_records[email]
                prev = previous_records[email]

                if (curr.get("rolle") or "").strip() != (prev.get("rolle") or "").strip():
                    changed_roles.append({"email": email, "old": prev.get("rolle", ""), "new": curr.get("rolle", "")})

                contact_fields = ("anrede", "vorname", "nachname", "telefon")
                changed_fields = {
                    field: {"old": prev.get(field, ""), "new": curr.get(field, "")}
                    for field in contact_fields
                    if (curr.get(field, "") or "").strip() != (prev.get(field, "") or "").strip()
                }
                if changed_fields:
                    changed_contact_data.append({"email": email, "changes": changed_fields})

            diffs[medium] = DiffResult(
                new_contacts=new_contacts,
                vanished_contacts=vanished_contacts,
                changed_roles=changed_roles,
                changed_contact_data=changed_contact_data,
            )

        return diffs
