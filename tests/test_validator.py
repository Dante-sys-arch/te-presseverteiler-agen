import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from validator import Validator
from scorer import Scorer


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
