import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from diff_engine import DiffEngine
from matcher import Matcher


class StubMatcher(Matcher):
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
            },
            {
                "medium": "NichtGescannt Medium",
                "vorname": "Peter",
                "nachname": "Beispiel",
                "email": "peter.beispiel@example.com",
                "telefon": "+49 30 222",
                "ressort": "Politik",
            },
        ]


class DeltaViewTests(unittest.TestCase):
    def setUp(self):
        self.matcher = StubMatcher()
        self.diff_engine = DiffEngine()

    def _rows(self, structured, scan_scope_media=None):
        rows, not_scanned = self.matcher.build_delta_inputs(structured, scan_scope_media=scan_scope_media)
        delta = self.diff_engine.build_delta_rows(rows)
        not_scanned_delta = self.diff_engine.build_unscanned_rows(not_scanned)
        return delta, not_scanned_delta

    def _first_row(self, structured, scan_scope_media=None):
        delta, _ = self._rows(structured, scan_scope_media=scan_scope_media)
        return delta[0]

    def test_official_hit_confirms_journalist(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [{"vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "+49 30 111", "rolle": "Finanzen"}],
                    "official_documents": [{"url": "https://handelsblatt.com/team", "page_type": "team", "text": "Anna Muster"}],
                    "industry_hints": [],
                    "medium_status": "ok",
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertEqual(row["Im_Web_gefunden"], "Ja")
        self.assertIn("Journalist bestaetigt", row["Was_ist_anders"])

    def test_team_author_structure_confirms_multiple_contacts(self):
        class MultiMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {"medium": "Handelsblatt", "vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "", "ressort": ""},
                    {"medium": "Handelsblatt", "vorname": "Bernd", "nachname": "Beispiel", "email": "bernd.beispiel@handelsblatt.com", "telefon": "", "ressort": ""},
                ]

        matcher = MultiMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [
                        {"url": "https://handelsblatt.com/team", "page_type": "team", "text": "Anna Muster"},
                        {"url": "https://handelsblatt.com/autor/bernd-beispiel", "page_type": "autorenseite", "text": "Bernd Beispiel"},
                    ],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True, "contact_expected_sources": 2},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        delta = self.diff_engine.build_delta_rows(rows)
        confirmed = [row for row in delta if "Journalist bestaetigt" in row["Was_ist_anders"]]
        self.assertEqual(len(confirmed), 2)

    def test_official_missing_industry_reports_probable_change(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
                },
                "kress": {
                    "official_contacts": [],
                    "industry_hints": [{"journalist": "Anna Muster", "source": "kress", "hint_type": "Branchenquelle meldet Wechsel", "detail": "..."}],
                    "medium_status": "ok",
                },
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Plausibler Wechselhinweis aus kress", row["Externer_Hinweis"])
        self.assertIn("Wahrscheinlicher Medienwechsel", row["Was_ist_anders"])
        self.assertEqual(row["Empfohlene_Aktion"], "manuell pruefen")

    def test_official_conflicts_with_industry(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [{"vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "+49 30 111", "rolle": "Finanzen"}],
                    "official_documents": [{"url": "https://handelsblatt.com/autor/anna-muster", "page_type": "autorenseite", "text": "Anna Muster"}],
                    "industry_hints": [],
                    "medium_status": "ok",
                },
                "turi2": {
                    "official_contacts": [],
                    "industry_hints": [{"journalist": "Anna Muster", "source": "turi2", "hint_type": "Branchenquelle meldet Wechsel", "detail": "..."}],
                    "medium_status": "ok",
                },
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Plausibler Wechselhinweis aus turi2", row["Externer_Hinweis"])
        self.assertEqual(row["Pruefen"], "Ja")
        self.assertIn("Weitere Quelle pruefen", row["Was_ist_anders"])

    def test_medium_technical_error(self):
        row = self._first_row(
            {
                "Handelsblatt": {"official_contacts": [], "industry_hints": [], "medium_status": "technisch_nicht_erreichbar"}
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Medium nicht erreichbar", row["Was_ist_anders"])
        self.assertNotIn("Journalist bei Medium nicht mehr gefunden", row["Was_ist_anders"])
        self.assertEqual(row["Empfohlene_Aktion"], "spaeter erneut pruefen oder URL korrigieren")
        self.assertEqual(row["Kommentar"], "Quelle technisch nicht erreichbar")

    def test_impressum_only_uses_soft_wording(self):
        delta, _ = self._rows(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {
                        "total_sources": 1,
                        "reachable_sources": 1,
                        "has_impressum": True,
                        "has_editorial_pages": False,
                        "contact_expected_sources": 0,
                    },
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertEqual(len(delta), 1)
        row = delta[0]
        self.assertEqual(row["Journalist"], "(Medium-Ebene)")
        self.assertIn("Weitere Quelle pruefen", row["Was_ist_anders"])
        self.assertIn("nur Impressum", row["Kommentar"])

    def test_high_coverage_missing_contact_marks_not_found(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Journalist bei Medium nicht mehr gefunden", row["Was_ist_anders"])
        self.assertEqual(row["Empfohlene_Aktion"], "deaktivieren oder manuell pruefen")

    def test_weak_official_with_external_hint_is_not_final_missing(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
                },
                "kress": {
                    "official_contacts": [],
                    "industry_hints": [{"journalist": "Anna Muster", "source": "kress", "hint_type": "Branchenquelle meldet Wechsel", "detail": "..."}],
                    "medium_status": "ok",
                },
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertNotIn("Auf offiziellen Seiten nicht bestaetigt", row["Was_ist_anders"])
        self.assertTrue("Wahrscheinlicher Medienwechsel" in row["Was_ist_anders"] or "Weitere Quelle pruefen" in row["Was_ist_anders"])
        self.assertIn("Plausibler Wechselhinweis", row["Externer_Hinweis"])

    def test_unscanned_medium_not_reported_as_missing_in_main_sheet(self):
        main_rows, unscanned_rows = self._rows(
            {
                "Handelsblatt": {"official_contacts": [], "industry_hints": [], "medium_status": "ok"},
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertEqual(len(main_rows), 1)
        self.assertEqual(main_rows[0]["Medium"], "Handelsblatt")
        self.assertTrue(all(row["Medium"] != "NichtGescannt Medium" for row in main_rows))
        self.assertEqual(len(unscanned_rows), 1)
        self.assertEqual(unscanned_rows[0]["Medium"], "NichtGescannt Medium")
        self.assertIn("Nicht im aktuellen Scan-Scope", unscanned_rows[0]["Was_ist_anders"])

    def test_mittel_coverage_with_team_page_uses_official_not_proven(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {
                        "total_sources": 1,
                        "reachable_sources": 1,
                        "has_impressum": False,
                        "has_editorial_pages": True,
                        "contact_expected_sources": 1,
                    },
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Auf offiziellen Seiten nicht bestaetigt", row["Was_ist_anders"])
        self.assertEqual(row["Kommentar"], "keine belastbare offizielle Quelle")

    def test_medium_probably_inactive(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "wahrscheinlich_nicht_mehr_aktiv",
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertEqual(row["Journalist"], "(Medium-Ebene)")
        self.assertIn("Medium wahrscheinlich nicht mehr aktiv", row["Was_ist_anders"])

    def test_journalist_found_on_author_page_without_impressum_match(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [{"url": "https://handelsblatt.com/autor/anna-muster", "page_type": "autorenseite", "text": "Anna Muster berichtet ..."}],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 1, "reachable_sources": 1, "has_impressum": False, "has_editorial_pages": True, "contact_expected_sources": 1},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Journalist bestaetigt", row["Was_ist_anders"])
        self.assertEqual(row["Kommentar"], "Autorenprofil gefunden")

    def test_journalist_found_at_other_medium_via_web_hint(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [{"journalist": "Anna Muster", "source": "kress", "hint_type": "Branchenquelle meldet Wechsel", "detail": "Anna Muster geht zu Neue Zeitung", "new_medium_hint": "Neue Zeitung"}],
                    "industry_documents": [{"source_type": "industry_source", "source_name": "kress", "text": "Anna Muster geht zu Neue Zeitung"}],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
                    "research_stages": {"offizielle_mediumsseiten": True, "domain_interne_suche": True, "branchenquelle": True, "linkedin": True, "allgemeine_websuche": True},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Bei anderem Medium gefunden", row["Was_ist_anders"])
        self.assertEqual(row["Gefunden_bei"], "Neue Zeitung")

    def test_linkedin_only_results_in_follow_up_instead_of_weak_hint(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [],
                    "industry_documents": [{"source_type": "linkedin_source", "source_name": "LinkedIn", "text": "Anna Muster | Senior Editor"}],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
                    "research_stages": {"offizielle_mediumsseiten": True, "domain_interne_suche": True, "branchenquelle": True, "linkedin": True, "allgemeine_websuche": True},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertNotIn("Nur schwacher Hinweis", row["Was_ist_anders"])
        self.assertIn("Weitere Quelle pruefen", row["Was_ist_anders"])
        self.assertEqual(row["LinkedIn_Hinweis"], "Ja")

    def test_open_web_only_results_in_weak_hint(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [],
                    "industry_documents": [{"source_type": "open_web", "source_name": "Websuche", "text": "Anna Muster Journalist"}],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
                    "research_stages": {"offizielle_mediumsseiten": True, "domain_interne_suche": True, "branchenquelle": True, "linkedin": True, "allgemeine_websuche": True},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Nur schwacher Hinweis", row["Was_ist_anders"])

    def test_no_reliable_hit_after_full_cascade(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [],
                    "industry_hints": [],
                    "industry_documents": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
                    "research_stages": {"offizielle_mediumsseiten": True, "domain_interne_suche": True, "branchenquelle": True, "linkedin": True, "allgemeine_websuche": True},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Nichts Belastbares gefunden", row["Was_ist_anders"])

    def test_no_other_medium_override_without_official_hit(self):
        row = self._first_row(
            {
                "Handelsblatt": {"official_contacts": [], "official_documents": [], "industry_hints": [], "medium_status": "ok", "official_source_stats": {"total_sources": 2, "reachable_sources": 2, "has_impressum": True, "has_editorial_pages": True}},
                "Andere Zeitung": {"official_contacts": [], "official_documents": [{"url": "https://andere.de/team", "page_type": "team", "text": "Anna Muster"}], "industry_hints": [], "medium_status": "ok"},
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Journalist bei Medium nicht mehr gefunden", row["Was_ist_anders"])

    def test_name_variant_umlaut_and_hyphen(self):
        class UmlautMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {
                        "medium": "Handelsblatt",
                        "vorname": "Jörg",
                        "nachname": "Müller-Schmidt",
                        "email": "joerg.mueller-schmidt@handelsblatt.com",
                        "telefon": "+49 30 111",
                        "ressort": "Finanzen",
                    }
                ]

        matcher = UmlautMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "official_documents": [{"url": "https://handelsblatt.com/team", "page_type": "team", "text": "Jorg Mueller Schmidt"}],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 2, "reachable_sources": 2, "has_impressum": True, "has_editorial_pages": True, "contact_expected_sources": 1},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        delta = self.diff_engine.build_delta_rows(rows)
        self.assertIn("Journalist bestaetigt", delta[0]["Was_ist_anders"])

    def test_title_and_abbreviation_variant_results_in_probable_match(self):
        class VariantMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {
                        "medium": "Handelsblatt",
                        "vorname": "Dr. Maximilian",
                        "nachname": "Schröder",
                        "email": "maximilian.schroeder@handelsblatt.com",
                        "telefon": "+49 30 999",
                        "ressort": "Politik",
                    }
                ]

        matcher = VariantMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [{"vorname": "Max", "nachname": "Schroeder", "email": "m.schroeder@handelsblatt.com", "telefon": "", "rolle": "Politik"}],
                    "official_documents": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True, "contact_expected_sources": 2},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        delta = self.diff_engine.build_delta_rows(rows)
        self.assertTrue(
            "Wahrscheinlicher Treffer" in delta[0]["Was_ist_anders"]
            or "Journalist bestaetigt" in delta[0]["Was_ist_anders"]
        )

    def test_function_address_is_labeled_in_comment(self):
        class FunctionMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {
                        "medium": "Handelsblatt",
                        "vorname": "Redaktion",
                        "nachname": "Finanzen",
                        "email": "redaktion@handelsblatt.com",
                        "telefon": "",
                        "ressort": "Desk",
                    }
                ]

        matcher = FunctionMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [{"vorname": "Redaktion", "nachname": "Finanzen", "email": "redaktion@handelsblatt.com", "telefon": "", "rolle": "Desk"}],
                    "official_documents": [{"url": "https://handelsblatt.com/redaktion", "page_type": "team", "text": "redaktion@handelsblatt.com"}],
                    "industry_hints": [],
                    "medium_status": "ok",
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        delta = self.diff_engine.build_delta_rows(rows)
        self.assertIn("Funktionsadresse", delta[0]["Kommentar"])

    def test_multiple_similar_names_prefers_email_pattern(self):
        class SimilarMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {
                        "medium": "Handelsblatt",
                        "vorname": "Anna",
                        "nachname": "Muster",
                        "email": "anna.muster@handelsblatt.com",
                        "telefon": "",
                        "ressort": "Finanzen",
                    }
                ]

        matcher = SimilarMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [
                        {"vorname": "Anne", "nachname": "Muster", "email": "anne.muster@handelsblatt.com", "telefon": "", "rolle": "Finanzen"},
                        {"vorname": "Anna", "nachname": "Muster", "email": "a.muster@handelsblatt.com", "telefon": "", "rolle": "Finanzen"},
                    ],
                    "official_documents": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True, "contact_expected_sources": 2},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        details = matcher.get_matching_detail_rows()
        self.assertTrue(details)
        self.assertEqual(details[0]["Match_Art"], "email_pattern")

    def test_benchmark_media_prefers_confirmed_and_probable_over_not_found(self):
        class BenchmarkMatcher(StubMatcher):
            def load_master_contacts(self):
                return [
                    {"medium": "Handelsblatt", "vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "", "ressort": "Finanzen"},
                    {"medium": "Handelsblatt", "vorname": "Bernd", "nachname": "Beispiel", "email": "bernd.beispiel@handelsblatt.com", "telefon": "", "ressort": "Politik"},
                    {"medium": "Handelsblatt", "vorname": "Clara", "nachname": "Demo", "email": "clara.demo@handelsblatt.com", "telefon": "", "ressort": "Wirtschaft"},
                ]

        matcher = BenchmarkMatcher()
        rows, _ = matcher.build_delta_inputs(
            {
                "Handelsblatt": {
                    "official_contacts": [
                        {"vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "", "rolle": "Finanzen"},
                        {"vorname": "B.", "nachname": "Beispiel", "email": "bernd.beispiel@handelsblatt.com", "telefon": "", "rolle": "Politik"},
                        {"vorname": "Cl", "nachname": "Demo", "email": "cl.demo@handelsblatt.com", "telefon": "", "rolle": "Wirtschaft"},
                    ],
                    "official_documents": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 4, "reachable_sources": 4, "has_impressum": True, "has_editorial_pages": True, "contact_expected_sources": 2},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        delta = self.diff_engine.build_delta_rows(rows)
        labels = " ".join(row["Was_ist_anders"] for row in delta)
        self.assertNotIn("Journalist bei Medium nicht mehr gefunden", labels)
        self.assertTrue(any("Journalist bestaetigt" in row["Was_ist_anders"] or "Wahrscheinlicher Treffer" in row["Was_ist_anders"] for row in delta))


if __name__ == "__main__":
    unittest.main()
