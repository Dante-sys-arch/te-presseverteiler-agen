import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from parser import Parser
from scorer import Scorer
from validator import STATUS_ACCEPT, STATUS_REVIEW, Validator


class ValidatorRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = Validator()

    def test_rejects_non_person_generic_terms(self) -> None:
        records = [
            {
                "vorname": "Digital",
                "nachname": "Service",
                "email": "digital.service@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_cleans_html_escape_and_deu_tld(self) -> None:
        records = [
            {
                "vorname": "Michaela",
                "nachname": "Knapp",
                "email": "u003eMichaela.Knapp@trend.deu",
                "telefon": "+43 1 2345678",
            }
        ]
        validated = self.validator.validate_records("Trend", records)
        self.assertEqual(validated[0]["email"], "michaela.knapp@trend.de")
        self.assertEqual(validated[0]["status"], STATUS_ACCEPT)

    def test_rejects_foreign_domain_per_medium_rules(self) -> None:
        records = [
            {
                "vorname": "Dijana",
                "nachname": "Matkovic",
                "email": "matkovic@fondsprofessionell.com",
                "telefon": "+43 1 2345678",
            }
        ]
        validated = self.validator.validate_records("Institutional Money", records)
        self.assertEqual(validated, [])

    def test_phone_date_values_are_blank_and_sent_to_review(self) -> None:
        records = [
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "anna.muster@wiwo.de",
                "telefon": "2026-03-13",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated[0]["telefon"], "")
        self.assertEqual(validated[0]["status"], STATUS_REVIEW)

    def test_deduplicates_email_typos_for_same_person(self) -> None:
        records = [
            {
                "vorname": "Burkhard",
                "nachname": "Bernhardt",
                "email": "b.bernhardt@boersen-zeitung.de",
                "telefon": "+49 30 12345678",
            },
            {
                "vorname": "Burkhard",
                "nachname": "Bernhardt",
                "email": "b.bernhardt@boersen-zeitung.deu",
                "telefon": "+49 30 12345678",
            },
        ]
        validated = self.validator.validate_records("Börsen-Zeitung", records)
        self.assertEqual(len(validated), 1)


class ParserAndScorerIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Parser()
        self.scorer = Scorer()

    def test_parser_keeps_raw_candidates_and_scorer_applies_validation(self) -> None:
        text = """
        Digital Service digital.service@wiwo.de
        Anna Muster anna.muster@wiwo.de +49 40 12345678
        """
        parsed = self.parser.parse({"Wirtschaftswoche": text})
        records = [c.__dict__ for c in parsed["Wirtschaftswoche"]]
        scored = self.scorer.score({"Wirtschaftswoche": records})
        self.assertEqual(len(scored["Wirtschaftswoche"]), 2)
        statuses = {entry["email"]: entry["status"] for entry in scored["Wirtschaftswoche"]}
        self.assertEqual(statuses["anna.muster@wiwo.de"], STATUS_ACCEPT)
        self.assertEqual(statuses["digital.service@wiwo.de"], STATUS_REVIEW)


if __name__ == "__main__":
    unittest.main()
