"""E-Mail-Muster-Ableitung und automatische Master-Update-Vorschläge.

1. Email Pattern Inference:
   Lernt das E-Mail-Schema pro Medium aus bestehenden Kontakten.
   Wenn ein neuer Journalist auftaucht, schlägt es die wahrscheinliche E-Mail vor.
   z.B. FAZ: vorname.nachname@faz.net → neuer Journalist Max Müller → max.mueller@faz.net

2. Master Update Suggestions:
   Erzeugt konkrete Änderungsvorschläge für die Master-Excel:
   "Zeile 47: Medium ändern von Handelsblatt zu Capital"
   "Zeile 128: E-Mail ändern von alt@... zu neu@..."
"""

from __future__ import annotations

import re
from collections import defaultdict


# --- Email Pattern Inference ---

def _extract_email_pattern(email: str, vorname: str, nachname: str) -> str | None:
    """Detect the email pattern for a given contact."""
    if not email or "@" not in email or not vorname or not nachname:
        return None

    local, domain = email.lower().split("@", 1)
    v = vorname.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    n = nachname.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")

    # Strip common umlauts also without replacement
    v_raw = vorname.lower()
    n_raw = nachname.lower()

    patterns = [
        ("vorname.nachname", f"{v}.{n}"),
        ("nachname.vorname", f"{n}.{v}"),
        ("v.nachname", f"{v[0]}.{n}"),
        ("vorname.n", f"{v}.{n[0]}"),
        ("vnachname", f"{v[0]}{n}"),
        ("vorname_nachname", f"{v}_{n}"),
        ("nachname", f"{n}"),
        ("vorname", f"{v}"),
    ]

    for pattern_name, expected_local in patterns:
        # Check with umlaut replacement
        if local == expected_local:
            return f"{pattern_name}@{domain}"
        # Check without umlaut replacement
        expected_raw = expected_local.replace(v, v_raw).replace(n, n_raw)
        if local == expected_raw:
            return f"{pattern_name}@{domain}"

    return None


def learn_email_patterns(master_contacts: list[dict]) -> dict[str, dict]:
    """Learn the dominant email pattern per medium from existing contacts.

    Returns: {medium: {"pattern": "vorname.nachname", "domain": "faz.net", "confidence": 0.85, "sample_count": 12}}
    """
    medium_patterns: dict[str, list[str]] = defaultdict(list)
    medium_domains: dict[str, list[str]] = defaultdict(list)

    for contact in master_contacts:
        medium = contact.get("medium", "")
        email = contact.get("email", "")
        vorname = contact.get("vorname", "")
        nachname = contact.get("nachname", "")

        if not medium or not email or "@" not in email:
            continue

        domain = email.split("@", 1)[1].lower()
        pattern = _extract_email_pattern(email, vorname, nachname)

        if pattern:
            pattern_name = pattern.split("@")[0]
            medium_patterns[medium].append(pattern_name)
            medium_domains[medium].append(domain)

    results = {}
    for medium, patterns in medium_patterns.items():
        if not patterns:
            continue
        # Find dominant pattern
        from collections import Counter
        pattern_counts = Counter(patterns)
        dominant_pattern, dominant_count = pattern_counts.most_common(1)[0]
        domain_counts = Counter(medium_domains[medium])
        dominant_domain, _ = domain_counts.most_common(1)[0]

        confidence = dominant_count / len(patterns) if patterns else 0
        results[medium] = {
            "pattern": dominant_pattern,
            "domain": dominant_domain,
            "confidence": round(confidence, 2),
            "sample_count": len(patterns),
        }

    return results


def suggest_email(vorname: str, nachname: str, medium: str, patterns: dict[str, dict]) -> str | None:
    """Suggest a probable email for a new journalist based on learned patterns.

    Returns email suggestion or None if confidence is too low.
    """
    info = patterns.get(medium)
    if not info or info["confidence"] < 0.5 or info["sample_count"] < 3:
        return None

    v = vorname.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    n = nachname.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    domain = info["domain"]
    pattern = info["pattern"]

    templates = {
        "vorname.nachname": f"{v}.{n}@{domain}",
        "nachname.vorname": f"{n}.{v}@{domain}",
        "v.nachname": f"{v[0]}.{n}@{domain}",
        "vorname.n": f"{v}.{n[0]}@{domain}",
        "vnachname": f"{v[0]}{n}@{domain}",
        "vorname_nachname": f"{v}_{n}@{domain}",
        "nachname": f"{n}@{domain}",
        "vorname": f"{v}@{domain}",
    }

    return templates.get(pattern)


# --- Master Update Suggestions ---

def generate_update_suggestions(
    delta_rows: list[dict],
    master_contacts: list[dict],
    email_patterns: dict[str, dict],
) -> list[dict]:
    """Generate concrete update suggestions for the master Excel.

    Returns list of suggestions like:
    {"zeile": 47, "medium": "FAZ", "journalist": "Max Müller",
     "feld": "medium", "alter_wert": "Handelsblatt", "neuer_wert": "Capital",
     "konfidenz": 85, "begruendung": "LinkedIn + Branchenquelle bestätigen Wechsel"}
    """
    # Build lookup: (medium, email) -> row index in master
    master_lookup: dict[str, int] = {}
    for idx, contact in enumerate(master_contacts):
        key = f"{contact.get('medium', '')}|||{contact.get('email', '')}".lower()
        master_lookup[key] = idx + 1  # 1-based row number

    suggestions = []

    for row in delta_rows:
        medium = str(row.get("Medium", ""))
        journalist = str(row.get("Journalist", ""))
        was = str(row.get("Was_ist_anders", ""))
        konfidenz = int(row.get("Konfidenz_Score", 0) or 0)
        alter_stand = str(row.get("Alter_Stand", ""))

        # Extract master email from alter_stand (format: "email | telefon | ressort")
        master_email = alter_stand.split("|")[0].strip() if "|" in alter_stand else ""
        lookup_key = f"{medium}|||{master_email}".lower()
        zeile = master_lookup.get(lookup_key, 0)

        # --- Medienwechsel mit hoher Konfidenz ---
        if "Medienwechsel" in was or "anderem Medium" in was:
            neues_medium = str(row.get("Neues_Medium_Hinweis", "") or row.get("Gefunden_bei", ""))
            if neues_medium and konfidenz >= 50:
                suggestions.append({
                    "zeile": zeile,
                    "medium": medium,
                    "journalist": journalist,
                    "aktion": "MEDIUM_AENDERN",
                    "feld": "Medium",
                    "alter_wert": medium,
                    "neuer_wert": neues_medium,
                    "konfidenz": konfidenz,
                    "begruendung": str(row.get("Konfidenz_Detail", "")),
                })

                # Suggest new email based on pattern
                vorname = journalist.split()[0] if " " in journalist else ""
                nachname = journalist.split()[-1] if " " in journalist else journalist
                suggested_email = suggest_email(vorname, nachname, neues_medium, email_patterns)
                if suggested_email:
                    suggestions.append({
                        "zeile": zeile,
                        "medium": medium,
                        "journalist": journalist,
                        "aktion": "EMAIL_VORSCHLAG",
                        "feld": "E-Mail",
                        "alter_wert": master_email,
                        "neuer_wert": suggested_email,
                        "konfidenz": min(konfidenz, int(email_patterns.get(neues_medium, {}).get("confidence", 0) * 100)),
                        "begruendung": f"E-Mail-Muster von {neues_medium}: {email_patterns.get(neues_medium, {}).get('pattern', '?')}",
                    })

        # --- E-Mail geändert ---
        if "E-Mail geaendert" in was:
            neuer_stand = str(row.get("Neuer_Stand", ""))
            new_email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", neuer_stand)
            if new_email_match and konfidenz >= 40:
                suggestions.append({
                    "zeile": zeile,
                    "medium": medium,
                    "journalist": journalist,
                    "aktion": "EMAIL_AENDERN",
                    "feld": "E-Mail",
                    "alter_wert": master_email,
                    "neuer_wert": new_email_match.group(0),
                    "konfidenz": konfidenz,
                    "begruendung": str(row.get("Konfidenz_Detail", "")),
                })

        # --- Journalist nicht mehr gefunden (mehrere Tage) ---
        tage = int(row.get("Tage_nicht_gefunden", 0) or 0)
        if tage >= 5:
            suggestions.append({
                "zeile": zeile,
                "medium": medium,
                "journalist": journalist,
                "aktion": "DEAKTIVIEREN_PRUEFEN",
                "feld": "Status",
                "alter_wert": "aktiv",
                "neuer_wert": f"prüfen (seit {tage} Tagen nicht gefunden)",
                "konfidenz": min(tage * 10, 80),
                "begruendung": f"Seit {tage} konsekutiven Scans nicht auf offiziellen Seiten oder im Web gefunden",
            })

    return suggestions
