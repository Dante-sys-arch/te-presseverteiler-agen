import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from parser import Parser
from scorer import Scorer
from validator import STATUS_ACCEPT, STATUS_REJECT, STATUS_REVIEW, Validator


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

    def test_rejects_requested_generic_name_and_mailbox_pairs(self) -> None:
        samples = [
            ("finanzen.net", "Im Hilfebereich", "", "impressum@finanzen.net"),
            ("Alles Wichtige", "Alles", "Wichtige", "spiegel_online@spiegel.de"),
            ("Alles Wichtige", "Alles", "Wichtige", "mm_redaktion@manager-magazin.de"),
            ("Der Inhalt", "Der", "Inhalt", "sales@picturepress.de"),
            ("Europäische Gremium", "Europäische", "Gremium", "dsk@diepresse.com"),
            ("Gesetzlicher Vertreter", "Gesetzlicher", "Vertreter", "publikumsservice@mdr.de"),
            ("Mail Kategorie", "Mail", "Kategorie", "anzeigen@sonntagszeitung.ch"),
            ("Inserateaufgabe Basler", "Inserateaufgabe", "Basler", "digitalnext@goldbach.ch"),
        ]
        for medium, first, last, email in samples:
            with self.subTest(email=email):
                records = [{"vorname": first, "nachname": last, "email": email, "telefon": ""}]
                validated = self.validator.validate_records(medium, records)
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
        self.assertEqual(validated, [])

    def test_rejects_broken_tld_comu(self) -> None:
        records = [
            {
                "vorname": "Andrea",
                "nachname": "Wasmuth",
                "email": "handelsblatt@handelsblattgroup.comu",
                "telefon": "+49 211 887-0",
            }
        ]
        validated = self.validator.validate_records("Handelsblatt", records)
        self.assertEqual(validated, [])

    def test_rejects_street_name_candidate(self) -> None:
        records = [
            {
                "vorname": "Toulouser",
                "nachname": "Allee",
                "email": "handelsblatt@handelsblattgroup.com",
                "telefon": "+49 211 887-0",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_location_or_address_like_name_terms(self) -> None:
        records = [
            {
                "vorname": "Berlin",
                "nachname": "Residence",
                "email": "berlin.residence@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_function_or_service_terms_as_name(self) -> None:
        records = [
            {
                "vorname": "Mitarbeiter",
                "nachname": "Informationen",
                "email": "mitarbeiter.informationen@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_form_fragment_name(self) -> None:
        records = [
            {
                "vorname": "Ihrer",
                "nachname": "Daten",
                "email": "leserservice@lzmedien.ch",
                "telefon": "+41 44 258 11 11",
            }
        ]
        validated = self.validator.validate_records("The Market (NZZ)", records)
        self.assertEqual(validated, [])

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

    def test_real_person_with_generic_mailbox_is_rejected(self) -> None:
        records = [
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "redaktion@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_generic_mailbox_with_fake_person_candidate(self) -> None:
        records = [
            {
                "vorname": "Forum",
                "nachname": "Commercial",
                "email": "forum@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_real_person_with_obviously_foreign_person_mailbox(self) -> None:
        records = [
            {
                "vorname": "Claudia",
                "nachname": "Immig",
                "email": "marcel.reyle@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_location_term_as_name(self) -> None:
        records = [
            {
                "vorname": "Zuerich",
                "nachname": "City",
                "email": "zuerich.city@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_role_term_as_name(self) -> None:
        records = [
            {
                "vorname": "Art",
                "nachname": "Director",
                "email": "art.director@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_form_hint_text_as_name(self) -> None:
        records = [
            {
                "vorname": "Moechten",
                "nachname": "Sie",
                "email": "moechten.sie@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_generic_mailbox_with_pretended_person_candidate(self) -> None:
        records = [
            {
                "vorname": "Freie",
                "nachname": "Autoren",
                "email": "politik@wiwo.de",
                "telefon": "+49 30 1234567",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated, [])

    def test_rejects_person_with_review_reported_generic_mailboxes(self) -> None:
        samples = [
            ("Wirtschaftswoche", "Leonard", "Knollenborg", "erfolg@wiwo.de"),
            ("Wirtschaftswoche", "Benedikt", "Becker", "politik@wiwo.de"),
            ("NZZ", "Anna", "Muster", "visuals@nzz.ch"),
            ("Welt", "Anna", "Muster", "nachdrucke@welt.de"),
            ("Sueddeutsche", "Anna", "Muster", "anzeigenannahme@sueddeutsche.de"),
        ]
        for medium, first, last, email in samples:
            with self.subTest(email=email):
                validated = self.validator.validate_records(
                    medium,
                    [{"vorname": first, "nachname": last, "email": email, "telefon": "+49 30 1234567"}],
                )
                self.assertEqual(validated, [])

    def test_rejects_url_encoded_email_artifacts_when_cleaned_to_generic_mailbox(self) -> None:
        records = [
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "%20redaktion%40nzz.ch",
                "telefon": "+41 44 258 11 11",
            }
        ]
        validated = self.validator.validate_records("NZZ", records)
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

    def test_review_candidate_phone_is_blank_when_unreliable(self) -> None:
        records = [
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "anna.otherbox@wiwo.de",
                "telefon": "11.22",
            }
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
        self.assertEqual(validated[0]["status"], STATUS_REVIEW)
        self.assertEqual(validated[0]["telefon"], "")

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

    def test_deduplicates_obviously_derived_mailbox_local_part(self) -> None:
        records = [
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "anna.muster@wiwo.de",
                "telefon": "+49 30 12345678",
            },
            {
                "vorname": "Anna",
                "nachname": "Muster",
                "email": "annamuster@wiwo.de",
                "telefon": "+49 30 12345678",
            },
        ]
        validated = self.validator.validate_records("Wirtschaftswoche", records)
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
        self.assertEqual(len(scored["Wirtschaftswoche"]), 1)
        statuses = {entry["email"]: entry["status"] for entry in scored["Wirtschaftswoche"]}
        self.assertEqual(statuses["anna.muster@wiwo.de"], STATUS_ACCEPT)
        self.assertNotIn("digital.service@wiwo.de", statuses)


if __name__ == "__main__":
    unittest.main()
