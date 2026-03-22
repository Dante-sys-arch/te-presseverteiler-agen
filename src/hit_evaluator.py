"""Heuristische Auswertung von Treffern aus LinkedIn, Branchenquellen und Websuche."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


ROLE_RE = re.compile(
    r"\b(Redakteur(?:in)?|Reporter(?:in)?|Korrespondent(?:in)?|Chefredakteur(?:in)?|"
    r"Ressortleiter(?:in)?|Editor(?:in)?|Head of [A-Za-zÄÖÜäöüß\- ]+|"
    r"Leiter(?:in)? [A-Za-zÄÖÜäöüß\- ]+|CvD|Chef vom Dienst)\b",
    re.IGNORECASE,
)
EMPLOYER_RE = re.compile(
    r"(?:bei|ist jetzt bei|arbeitet bei|wechselt zu|neu bei|joined|joining|wechselt von .*? zu)\s+"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\- ]{2,80})",
    re.IGNORECASE,
)
MEDIA_RE = re.compile(r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\-]{2,}(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&()./\-]{2,}){0,3})\b")
WEAK_CONTEXT_RE = re.compile(r"\b(gewann|event|konferenz|panel|zitiert|genannt|teilnahm)\b", re.IGNORECASE)
STRONG_PROFILE_RE = re.compile(r"\b(profil|redaktion|autor(?:in)?|ressort|korrespondent(?:in)?|editor|linkedin)\b", re.IGNORECASE)


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
    recognized_medium = employer_match.group(1).strip() if employer_match else ""
    if not recognized_medium and source_type != "linkedin_source":
        for candidate in media_candidates:
            if len(candidate.split()) <= 5 and any(ch.isalpha() for ch in candidate):
                recognized_medium = candidate
                break
    recognized_role = role_match.group(1).strip() if role_match else ""
    recognized_employer = recognized_medium

    has_profile_context = bool(STRONG_PROFILE_RE.search(snippet)) or source_type in {"linkedin_source", "industry_source"}
    weak_only = bool(WEAK_CONTEXT_RE.search(snippet)) and not has_profile_context
    medium_change = _is_other_medium(recognized_medium, current_medium)

    if source_type == "linkedin_source" and employer_match and recognized_medium and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="hoch" if recognized_role else "mittel",
            entscheidung="LinkedIn bestaetigt neues Medium",
            kommentar="LinkedIn-Profil nennt aktuellen Arbeitgeber",
        )

    if source_type == "industry_source" and recognized_medium and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="hoch",
            entscheidung="Wahrscheinlicher Medienwechsel",
            kommentar=f"Branchenquelle {source_name} meldet Wechsel",
        )

    if source_type == "open_web" and recognized_medium and has_profile_context and medium_change:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="mittel",
            entscheidung="Bei anderem Medium gefunden",
            kommentar="Webtreffer mit Berufs-/Profilbezug",
        )

    if recognized_medium and has_profile_context and not weak_only:
        return EvaluatedHit(
            erkannter_arbeitgeber=recognized_employer,
            erkanntes_medium=recognized_medium,
            erkannte_rolle=recognized_role,
            bewertungsstufe="mittel",
            entscheidung="Nur schwacher Hinweis",
            kommentar="Treffer enthaelt Mediumhinweis, aber ohne klaren Wechselbeleg",
        )

    return EvaluatedHit(
        erkannter_arbeitgeber="",
        erkanntes_medium="",
        erkannte_rolle=recognized_role,
        bewertungsstufe="niedrig",
        entscheidung="Nur schwacher Hinweis" if not weak_only else "Nichts Belastbares gefunden",
        kommentar="Nur lose Namensnennung ohne belastbaren Arbeitgeberbezug",
    )
