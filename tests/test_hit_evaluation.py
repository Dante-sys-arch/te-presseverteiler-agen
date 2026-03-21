import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from diff_engine import DiffEngine
from matcher import Matcher
from parser import Parser


class HitEvalMatcher(Matcher):
    def __init__(self) -> None:
        super().__init__(Path("config/mandate_mapping.csv"), Path("data/master/MASTER_DACHLILUX.xlsx"))

    def load_master_contacts(self):
        return [
            {
                "medium": "Handelsblatt",
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "anna.muster@handelsblatt.com",
                "telefon": "+49 30 111",
                "ressort": "Finanzen",
            }
        ]


class HitEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.matcher = HitEvalMatcher()
        self.diff_engine = DiffEngine()

    def _first_delta_row(self, structured):
        rows, _ = self.matcher.build_delta_inputs(structured, scan_scope_media={"Handelsblatt"})
        delta = self.diff_engine.build_delta_rows(rows)
        self.assertEqual(len(delta), 1)
        return delta[0]

    def test_linkedin_match_sets_new_medium_and_found_at(self):
        row = self._first_delta_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [],
                    "industry_documents": [
                        {
                            "source_type": "linkedin_source",
                            "source_name": "LinkedIn",
                            "journalist": "Anna Muster",
                            "text": "Anna Muster ist jetzt bei WirtschaftsWoche als Redakteurin.",
                        }
                    ],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 2, "reachable_sources": 2, "has_impressum": True, "has_editorial_pages": False},
                }
            }
        )
        self.assertEqual(row["LinkedIn_Hinweis"], "Ja")
        self.assertTrue(row["Neues_Medium_Hinweis"].startswith("WirtschaftsWoche als Redakteurin"))
        self.assertEqual(row["Gefunden_bei"], row["Neues_Medium_Hinweis"])
        self.assertIn("Wahrscheinlicher Medienwechsel", row["Was_ist_anders"])
        self.assertIn("Bei anderem Medium gefunden", row["Was_ist_anders"])
        self.assertNotIn("Nur schwacher Hinweis", row["Was_ist_anders"])

    def test_open_web_other_medium_marks_medium_change(self):
        row = self._first_delta_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [],
                    "industry_documents": [
                        {
                            "source_type": "open_web",
                            "source_name": "Presseportal",
                            "journalist": "Anna Muster",
                            "text": "Anna Muster arbeitet bei NZZ und betreut den Finanzbereich.",
                        }
                    ],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
                }
            }
        )
        self.assertIn("Bei anderem Medium gefunden", row["Was_ist_anders"])
        self.assertTrue(row["Gefunden_bei"].startswith("NZZ und betreut den Finanzbereich"))

    def test_parser_extracts_employer_hint_from_linkedin_without_change_keyword(self):
        parser = Parser()
        snapshot = SimpleNamespace(
            content="Profil: Anna Muster ist jetzt bei The Market.",
            source_type="linkedin_source",
            source_name="LinkedIn",
            journalist="Anna Muster",
            status_code=200,
            url="https://linkedin.com/in/anna-muster",
        )
        structured = parser.parse_structured({"Handelsblatt": [snapshot]})
        hints = structured["Handelsblatt"]["industry_hints"]
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["journalist"], "Anna Muster")
        self.assertEqual(hints[0]["new_medium_hint"], "The Market")


if __name__ == "__main__":
    unittest.main()
