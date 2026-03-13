import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from validator import Validator
from scorer import Scorer
from parser import Parser


class ValidatorNegativeExamplesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = Validator()

    def test_blocks_known_non_person_terms(self) -> None:
        samples = [
            ("Allgemeine", "Zeitung"),
            ("Ihre", "Daten"),
            ("Ein", "Unternehmen"),
            ("Entdecken", "Sie"),
            ("Technische", "Betreuung"),
            ("Geschätzte", "Lesezeit"),
            ("New", "York"),
            ("Picture", "Press"),
            ("Handelsgericht", "Wien"),
            ("Zentrale", "Kontaktstellen"),
            ("Digital", "Service"),
            ("Die", "Frankfurter"),
            ("Vertrieb", "Einzelverkauf"),
            ("Amtsgericht", "Köln"),
        ]
        for first, last in samples:
            with self.subTest(first=first, last=last):
                result = self.validator.validate_record(
                    {
                        "vorname": first,
                        "nachname": last,
                        "rolle": "",
                        "email": "max.mustermann@example.com",
                        "telefon": "+49 30 1234567",
                    }
                )
                self.assertFalse(result.is_valid)

    def test_blocks_email_name_mismatch(self) -> None:
        result = self.validator.validate_record(
            {
                "vorname": "Max",
                "nachname": "Mustermann",
                "rolle": "Redakteur",
                "email": "info@example.com",
                "telefon": "+49 30 1234567",
            }
        )
        self.assertFalse(result.is_valid)

    def test_blocks_invalid_phone(self) -> None:
        result = self.validator.validate_record(
            {
                "vorname": "Max",
                "nachname": "Mustermann",
                "rolle": "Redakteur",
                "email": "max.mustermann@example.com",
                "telefon": "12345",
            }
        )
        self.assertFalse(result.is_valid)

    def test_accepts_valid_person_record(self) -> None:
        result = self.validator.validate_record(
            {
                "vorname": "Max",
                "nachname": "Mustermann",
                "rolle": "Redakteur",
                "email": "max.mustermann@example.com",
                "telefon": "+49 30 1234567",
            }
        )
        self.assertTrue(result.is_valid)




class ParserAndScorerRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Parser()
        self.validator = Validator()
        self.scorer = Scorer()

    def test_blocks_organization_names_netpoint_media_and_google_ireland(self) -> None:
        for first, last in [("Netpoint", "Media"), ("Google", "Ireland")]:
            with self.subTest(first=first, last=last):
                result = self.validator.validate_record(
                    {
                        "vorname": first,
                        "nachname": last,
                        "rolle": "",
                        "email": "max.mustermann@example.com",
                        "telefon": "+49 30 1234567",
                    }
                )
                self.assertFalse(result.is_valid)

    def test_cleans_html_escaped_email_prefix_u003e(self) -> None:
        cleaned = self.parser._clean_email_candidate("u003eknapp.michaela@trend.at")  # noqa: SLF001
        self.assertEqual(cleaned, "knapp.michaela@trend.at")

    def test_rejects_implausible_phone_examples(self) -> None:
        invalid_phones = ["26054200", "20260313.3"]
        for phone in invalid_phones:
            with self.subTest(phone=phone):
                self.assertFalse(self.parser._is_plausible_phone(phone))  # noqa: SLF001
                self.assertFalse(self.validator._is_plausible_phone(phone))  # noqa: SLF001

    def test_fuzzy_match_email_is_cleared_for_unplausible_match(self) -> None:
        scored = self.scorer.score(
            {
                "Trend": [
                    {
                        "vorname": "Michaela",
                        "nachname": "Knapp",
                        "rolle": "Redakteurin",
                        "email": "michaela.knapp@trend.at",
                        "telefon": "+43 1 2345678",
                        "fuzzy_match_score": 70,
                        "fuzzy_match_email": "wrong.person@otherdomain.com",
                    }
                ]
            }
        )
        self.assertEqual(scored["Trend"][0]["fuzzy_match_email"], "")

class ScorerValidationIntegrationTest(unittest.TestCase):
    def test_scorer_filters_invalid_candidates(self) -> None:
        scorer = Scorer()
        scored = scorer.score(
            {
                "Medium A": [
                    {
                        "vorname": "Digital",
                        "nachname": "Service",
                        "rolle": "Service",
                        "email": "digital.service@example.com",
                        "telefon": "+49 30 1234567",
                    },
                    {
                        "vorname": "Anna",
                        "nachname": "Muster",
                        "rolle": "Politik",
                        "email": "anna.muster@example.com",
                        "telefon": "+49 40 12345678",
                    },
                ]
            }
        )
        self.assertEqual(len(scored["Medium A"]), 1)
        self.assertEqual(scored["Medium A"][0]["vorname"], "Anna")


if __name__ == "__main__":
    unittest.main()
