"""Heuristische Auswertung von Treffern aus LinkedIn, Branchenquellen und Websuche."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


ROLE_RE = re.compile(
    r"\b(Redakteur(?:in)?|Reporter(?:in)?|Korrespondent(?:in)?|Chefredakteur(?:in)?|"
    r"Ressortleiter(?:in)?|Editor(?:in)?|Head of [A-Za-zÄÖÜäöüß\- ]+|"
    r"Leiter(?:in)? [A-Za-zÄÖÜäöüß\- ]+|CvD|Chef vom Dienst|"
    r"Chefreporter(?:in)?|Wirtschaftsredakteur(?:in)?|Finanzredakteur(?:in)?|"
    r"Nachrichtenchef(?:in)?|Textchef(?:in)?|Ressortchef(?:in)?)\b",
    re.IGNORECASE,
)

# Extended employer patterns for Google Search snippets
EMPLOYER_RE = re.compile(
    r"(?:"
    r"bei|ist jetzt bei|arbeitet bei|taetig bei|taetig fuer|"
    r"wechselt zu|neu bei|joined|joining|"
    r"wechselt von .*? zu|"
    r"ist [\w\s]+ bei|"       # "ist Redakteur bei X"
    r"als [\w\s]+ bei|"       # "als Reporter bei X"
    r"Redakteur(?:in)? bei|Reporter(?:in)? bei|Korrespondent(?:in)? bei|"
    r"Editor(?:in)? bei"
    r")\s+"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\- ]{2,80})",
    re.IGNORECASE,
)

# LinkedIn snippet title format: "Name – Titel bei Firma | LinkedIn"
LINKEDIN_TITLE_RE = re.compile(
    r"[\u2013\u2014\u2012\u2015\-–—]\s*"
    r"([^|]{3,80}?)\s*"
    r"(?:bei|at|@|,)\s*"
    r"([A-ZÄÖÜ][^|]{2,80}?)\s*"
    r"(?:\||\u2013|\u2014)?\s*(?:LinkedIn)?",
    re.IGNORECASE,
)

# Google snippet: "Name, Medium" or "Name | Medium" patterns
SNIPPET_SEPARATOR_RE = re.compile(
    r"^[A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+\s*[,|·]\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\- ]{2,60})",
)

MEDIA_RE = re.compile(r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\-]{2,}(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\-]{2,}){0,3})\b")
WEAK_CONTEXT_RE = re.compile(r"\b(gewann|event|konferenz|panel|zitiert|genannt|teilnahm|preis|award|podium)\b", re.IGNORECASE)
STRONG_PROFILE_RE = re.compile(
    r"\b(profil|redaktion|autor(?:in)?|ressort|korrespondent(?:in)?|editor|linkedin|"
    r"journalist(?:in)?|reporter(?:in)?|medien|impressum|team|lebenslauf|vita)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvaluatedHit:
    erkannter_arbeitgeber: str = ""
    erkanntes_medium: str = ""
    erkannte_rolle: str = ""
    bewertungsstufe: str = "niedrig"
    entscheidung: str = "Nur schwacher Hinweis"
    kommentar: str = ""


def _normalize(value: str) -> str:
    txt = unicodedata.normalize("NFKD", str(value or "").lower().strip())
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _is_other_medium(candidate: str, current_medium: str) -> bool:
    c1 = _normalize(candidate)
    c2 = _normalize(current_medium)
    return bool(c1 and c2 and c1 != c2 and c1 not in c2 and c2 not in c1)


def _extract_linkedin_employer(snippet: str) -> tuple[str, str]:
    """Extract role and employer from LinkedIn-style snippets."""
    match = LINKEDIN_TITLE_RE.search(snippet)
    if match:
        role_part = match.group(1).strip().rstrip(",;. ")
        employer_part = match.group(2).strip().rstrip(",;.|. ")
        return role_part, employer_part
    # Try separator pattern: "Name, Medium" or "Name | Medium"
    sep_match = SNIPPET_SEPARATOR_RE.search(snippet)
    if sep_match:
        return "", sep_match.group(1).strip().rstrip(",;.|. ")
    return "", ""


def evaluate_hit(
    *,
    text: str,
    source_type: str,
    source_name: str,
    current_medium: str,
) -> EvaluatedHit:
    snippet = str(text or "")
    role_match = ROLE_RE.search(snippet)
    employer_match = EMPLOYER_RE.search(snippet)
    media_candidates = [m.group(1).strip() for m in MEDIA_RE.finditer(snippet)]

    # Try LinkedIn-specific extraction first for linkedin sources
    linkedin_role, linkedin_employer = "", ""
    if source_type == "linkedin_source":
        linkedin_role, linkedin_employer = _extract_linkedin_employer(snippet)

    recognized_medium = employer_match.group(1).strip() if employer_match else ""
    # Use LinkedIn-extracted employer if standard regex failed
    if not recognized_medium and linkedin_employer:
        recognized_medium = linkedin_employer
    if not recognized_medium and source_type != "linkedin_source":
        for candidate in media_candidates:
            if len(candidate.split()) <= 5 and any(ch.isalpha() for ch in candidate):
                recognized_medium = candidate
                break

    recognized_role = role_match.group(1).strip() if role_match else ""
    if not recognized_role and linkedin_role:
        # Check if the LinkedIn role part contains a known role
        role_check = ROLE_RE.search(linkedin_role)
        if role_check:
            recognized_role = role_check.group(1).strip()
        elif any(kw in linkedin_role.lower() for kw in ("redakt", "report", "editor", "korrespond", "journalist", "chef")):
            recognized_role = linkedin_role

    recognized_employer = recognized_medium

    has_profile_context = bool(STRONG_PROFILE_RE.search(snippet)) or source_type in {"linkedin_source", "industry_source"}
    weak_only = bool(WEAK_CONTEXT_RE.search(snippet)) and not has_profile_context
    medium_change = _is_other_medium(recognized_medium, current_medium)

    # === LinkedIn source with detected employer change ===
    if source_type == "linkedin_source" and recognized_medium and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="hoch" if recognized_role else "mittel",
            entscheidung="LinkedIn bestaetigt neues Medium",
            kommentar="LinkedIn-Profil nennt aktuellen Arbeitgeber",
        )

    # === LinkedIn source confirming current medium ===
    if source_type == "linkedin_source" and recognized_medium and not medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="mittel",
            entscheidung="Journalist bestaetigt",
            kommentar="LinkedIn-Profil bestaetigt aktuelles Medium",
        )

    # === Industry source with medium change ===
    if source_type == "industry_source" and recognized_medium and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="hoch",
            entscheidung="Wahrscheinlicher Medienwechsel",
            kommentar=f"Branchenquelle {source_name} meldet Wechsel",
        )

    # === Open web with medium change + profile context ===
    if source_type == "open_web" and recognized_medium and has_profile_context and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="mittel",
            entscheidung="Bei anderem Medium gefunden",
            kommentar="Webtreffer mit Berufs-/Profilbezug",
        )

    # === Any source with recognized medium + profile context (FIX: check medium_change) ===
    if recognized_medium and has_profile_context and not weak_only:
        if medium_change:
            return EvaluatedHit(
                erkannter_arbeitgeber=recognized_employer,
                erkanntes_medium=recognized_medium,
                erkannte_rolle=recognized_role,
                bewertungsstufe="mittel",
                entscheidung="Bei anderem Medium gefunden",
                kommentar="Treffer enthaelt abweichendes Medium mit Profilbezug",
            )
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="mittel",
            entscheidung="Journalist bestaetigt",
            kommentar="Treffer enthaelt aktuelles Medium mit Profilbezug",
        )

    # === Fallback: nothing concrete found ===
    if weak_only:
        return EvaluatedHit(
            erkannter_arbeitgeber="",
            erkanntes_medium="",
            erkannte_rolle=recognized_role,
            bewertungsstufe="niedrig",
            entscheidung="Nichts Belastbares gefunden",
            kommentar="Nur lose Namensnennung in Event-/Konferenzkontext",
        )

    return EvaluatedHit(
        erkannter_arbeitgeber="",
        erkanntes_medium="",
        erkannte_rolle=recognized_role,
        bewertungsstufe="niedrig",
        entscheidung="Auf offiziellen Seiten nicht bestaetigt",
        kommentar="Namensnennung ohne belastbaren Arbeitgeberbezug",
    )
