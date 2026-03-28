"""LLM-gestützte Auswertung von Webseiten und Suchergebnissen via Anthropic API.

Ersetzt die rein Regex-basierte Auswertung durch Claude-basierte Analyse für:
1. Offizielle Medien-Seiten (Impressum, Team, Redaktion) → Kontakte extrahieren
2. Serper-Snippets (LinkedIn, Web) → Arbeitgeber/Medium/Rolle erkennen
3. Multi-Source-Bestätigung → Medienwechsel erst bei ≥2 Quellen melden
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import anthropic


MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic | None:
    global _client
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    if _client is None:
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


@dataclass
class LLMContact:
    vorname: str = ""
    nachname: str = ""
    rolle: str = ""
    ressort: str = ""
    email: str = ""
    telefon: str = ""


@dataclass
class LLMJournalistStatus:
    journalist: str = ""
    aktuelles_medium: str = ""
    aktuelle_rolle: str = ""
    status: str = ""  # bestaetigt | gewechselt | unklar | nicht_gefunden
    neues_medium: str = ""
    neue_rolle: str = ""
    konfidenz: str = "niedrig"  # niedrig | mittel | hoch
    quellen_anzahl: int = 0
    begruendung: str = ""


def extract_contacts_from_page(medium: str, page_text: str, page_type: str, url: str) -> list[LLMContact]:
    """Use Claude to extract journalist contacts from an official media page."""
    client = _get_client()
    if not client:
        return []

    # Truncate to avoid token limits
    text = page_text[:12000] if len(page_text) > 12000 else page_text

    # Skip empty or very short pages
    if len(text.strip()) < 100:
        return []

    prompt = f"""Analysiere diese {page_type}-Seite von "{medium}" ({url}).

Extrahiere ALLE Journalisten/Redakteure mit ihren Kontaktdaten.

REGELN:
- Nur echte Personen, KEINE Funktionsadressen (redaktion@, info@, kontakt@)
- Nur Personen die klar als Journalist/Redakteur/Reporter/Korrespondent/Editor erkennbar sind
- Bei E-Mail-Adressen: nur persönliche (vorname.nachname@...), NICHT allgemeine
- Wenn keine Journalisten erkennbar sind, gib ein leeres Array zurück

Antworte AUSSCHLIESSLICH mit einem JSON-Array, kein anderer Text:
[{{"vorname":"...","nachname":"...","rolle":"...","ressort":"...","email":"...","telefon":"..."}}]

Seiteninhalt:
{text}"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return [
            LLMContact(
                vorname=str(item.get("vorname", "")).strip(),
                nachname=str(item.get("nachname", "")).strip(),
                rolle=str(item.get("rolle", "")).strip(),
                ressort=str(item.get("ressort", "")).strip(),
                email=str(item.get("email", "")).strip(),
                telefon=str(item.get("telefon", "")).strip(),
            )
            for item in data
            if isinstance(item, dict) and (item.get("vorname") or item.get("nachname"))
        ]
    except Exception:
        return []


def evaluate_journalist_snippets(
    journalist: str,
    master_medium: str,
    master_email: str,
    master_ressort: str,
    linkedin_snippets: list[str],
    web_snippets: list[str],
    official_page_names: list[str],
) -> LLMJournalistStatus:
    """Use Claude to evaluate all collected evidence about a journalist."""
    client = _get_client()
    if not client:
        return LLMJournalistStatus(journalist=journalist, status="unklar", begruendung="Keine API verfügbar")

    # Only call if we have any snippets to evaluate
    if not linkedin_snippets and not web_snippets and not official_page_names:
        return LLMJournalistStatus(journalist=journalist, status="nicht_gefunden", begruendung="Keine Suchergebnisse")

    linkedin_text = "\n".join(f"- {s[:300]}" for s in linkedin_snippets[:5]) if linkedin_snippets else "(keine)"
    web_text = "\n".join(f"- {s[:300]}" for s in web_snippets[:8]) if web_snippets else "(keine)"
    official_text = ", ".join(official_page_names[:10]) if official_page_names else "(nicht gefunden)"

    prompt = f"""Du bist ein Medienanalyst. Bewerte den aktuellen Status dieses Journalisten:

JOURNALIST: {journalist}
AKTUELLES MEDIUM (laut Master-Verteiler): {master_medium}
AKTUELLE E-MAIL (laut Master): {master_email}
AKTUELLES RESSORT (laut Master): {master_ressort}

EVIDENCE:

1. Offizielle Medien-Seiten (Impressum/Team/Redaktion von {master_medium}):
Name auf offiziellen Seiten gefunden: {official_text}

2. LinkedIn-Suchergebnisse:
{linkedin_text}

3. Allgemeine Web-Suchergebnisse:
{web_text}

AUFGABE: Bewerte den Status des Journalisten. Antworte NUR mit JSON:

{{
  "status": "bestaetigt|gewechselt|unklar|nicht_gefunden",
  "aktuelles_medium": "Medium wo die Person JETZT arbeitet (laut Evidence)",
  "aktuelle_rolle": "aktuelle Rolle/Position falls erkennbar",
  "neues_medium": "nur ausfüllen wenn gewechselt",
  "neue_rolle": "nur ausfüllen wenn gewechselt",
  "konfidenz": "niedrig|mittel|hoch",
  "quellen_anzahl": 0,
  "begruendung": "kurze Begründung in einem Satz"
}}

REGELN:
- "bestaetigt": Person arbeitet nachweislich noch bei {master_medium}
- "gewechselt": Person arbeitet nachweislich bei einem ANDEREN Medium
- "unklar": Es gibt Hinweise, aber keine klare Bestätigung
- "nicht_gefunden": Keine verwertbaren Ergebnisse
- "konfidenz" = "hoch" nur wenn ≥2 unabhängige Quellen übereinstimmen
- ACHTUNG: Freie Journalisten schreiben für MEHRERE Medien — das ist KEIN Wechsel!
- ACHTUNG: Zusammengeführte Redaktionen (z.B. FAZ/FAS, WELT/WamS) sind KEIN Wechsel!
- ACHTUNG: Wenn jemand in einem Artikel eines anderen Mediums nur ZITIERT wird, ist das KEIN Wechsel!
- Nur echte Arbeitgeberwechsel als "gewechselt" markieren"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return LLMJournalistStatus(
            journalist=journalist,
            aktuelles_medium=str(data.get("aktuelles_medium", "")).strip(),
            aktuelle_rolle=str(data.get("aktuelle_rolle", "")).strip(),
            status=str(data.get("status", "unklar")).strip(),
            neues_medium=str(data.get("neues_medium", "")).strip(),
            neue_rolle=str(data.get("neue_rolle", "")).strip(),
            konfidenz=str(data.get("konfidenz", "niedrig")).strip(),
            quellen_anzahl=int(data.get("quellen_anzahl", 0)),
            begruendung=str(data.get("begruendung", "")).strip(),
        )
    except Exception as exc:
        return LLMJournalistStatus(
            journalist=journalist,
            status="unklar",
            begruendung=f"LLM-Fehler: {str(exc)[:100]}",
        )
