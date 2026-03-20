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
                    "industry_hints": [],
                    "medium_status": "ok",
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertEqual(row["Im_Web_gefunden"], "Ja")
        self.assertIn("Journalist bestaetigt", row["Was_ist_anders"])

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
        self.assertEqual(row["Empfohlene_Aktion"], "weitere Quelle pruefen")

    def test_official_conflicts_with_industry(self):
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [{"vorname": "Anna", "nachname": "Muster", "email": "anna.muster@handelsblatt.com", "telefon": "+49 30 111", "rolle": "Finanzen"}],
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
        row = self._first_row(
            {
                "Handelsblatt": {
                    "official_contacts": [],
                    "industry_hints": [],
                    "medium_status": "ok",
                    "official_source_stats": {"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
                }
            },
            scan_scope_media={"Handelsblatt"},
        )
        self.assertIn("Auf aktueller Quelle nicht belegt", row["Was_ist_anders"])
        self.assertNotIn("Journalist bei Medium nicht mehr gefunden", row["Was_ist_anders"])
        self.assertEqual(row["Kommentar"], "nur Impressum vorhanden")

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
        self.assertNotIn("Journalist bei Medium nicht mehr gefunden", row["Was_ist_anders"])
        self.assertTrue(
            "Wahrscheinlicher Medienwechsel" in row["Was_ist_anders"] or "Weitere Quelle pruefen" in row["Was_ist_anders"]
        )
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


if __name__ == "__main__":
    unittest.main()
