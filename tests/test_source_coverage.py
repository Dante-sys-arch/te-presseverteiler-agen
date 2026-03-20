import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from validator import SourceCoverageAssessor


class SourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.assessor = SourceCoverageAssessor()

    def test_impressum_only_is_not_high(self):
        result = self.assessor.assess(
            medium_status="ok",
            source_stats={"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
            has_external_hint=False,
        )
        self.assertEqual(result.level, "mittel")
        self.assertEqual(result.comment, "nur Impressum vorhanden")

    def test_unreachable_is_low(self):
        result = self.assessor.assess(
            medium_status="technisch_nicht_erreichbar",
            source_stats={"total_sources": 2, "reachable_sources": 0, "has_impressum": True, "has_editorial_pages": True},
            has_external_hint=False,
        )
        self.assertEqual(result.level, "niedrig")
        self.assertEqual(result.comment, "Quelle technisch nicht erreichbar")

    def test_high_coverage_scored_high(self):
        result = self.assessor.assess(
            medium_status="ok",
            source_stats={"total_sources": 3, "reachable_sources": 3, "has_impressum": True, "has_editorial_pages": True},
            has_external_hint=False,
        )
        self.assertEqual(result.level, "hoch")

    def test_external_hint_overrides_weak_comment(self):
        result = self.assessor.assess(
            medium_status="ok",
            source_stats={"total_sources": 1, "reachable_sources": 1, "has_impressum": True, "has_editorial_pages": False},
            has_external_hint=True,
        )
        self.assertEqual(result.comment, "nur externer Hinweis vorhanden")


if __name__ == "__main__":
    unittest.main()
