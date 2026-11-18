import unittest
from datetime import date

from signal_lab.analysis import compare_periods, compare_periods_with_snapshot
from signal_lab.catalog import (
    COVERED,
    PARTIAL,
    UNCOVERED,
    UNKNOWN,
    SourceCatalog,
)
from signal_lab.records import CoverageSpan, Observation, Source
from signal_lab.report import create_coverage_report


def make_catalog():
    sources = [
        Source("desk-a", "east-asia", "zh"),
        Source("desk-b", "east-asia", "zh"),
        Source("desk-c", "europe", "en"),
    ]
    coverage = [
        CoverageSpan("desk-a", "east-asia", date(2026, 1, 1)),
        CoverageSpan("desk-b", "east-asia", date(2026, 1, 1), date(2026, 10, 15), "paused"),
        CoverageSpan("desk-b", "east-asia", date(2026, 11, 10), None, "resumed"),
        CoverageSpan("desk-c", "europe", date(2026, 1, 1), date(2026, 10, 31), "was europe"),
        CoverageSpan("desk-c", "east-asia", date(2026, 11, 1), None, "moved"),
    ]
    return SourceCatalog(sources, coverage)


OBSERVATIONS = [
    Observation("o1", "desk-a", date(2026, 10, 5), "topic", 5),
    Observation("o2", "desk-b", date(2026, 10, 5), "topic", 4),
    Observation("o3", "desk-c", date(2026, 10, 6), "topic", 3),
    Observation("o4", "desk-a", date(2026, 11, 5), "topic", 6),
    Observation("o5", "desk-b", date(2026, 11, 15), "topic", 9),
    Observation("o6", "desk-c", date(2026, 11, 7), "topic", 8),
]


class CoverageJudgementTests(unittest.TestCase):
    def test_covered_partial_uncovered_and_unknown_are_distinct(self):
        catalog = make_catalog()
        self.assertEqual(catalog.coverage_for("desk-a", (2026, 10)).state, COVERED)
        self.assertEqual(catalog.coverage_for("desk-b", (2026, 10)).state, PARTIAL)
        self.assertEqual(catalog.coverage_for("desk-b", (2026, 10)).state, PARTIAL)
        self.assertEqual(catalog.coverage_for("desk-a", (2025, 12)).state, UNCOVERED)
        self.assertEqual(catalog.coverage_for("missing", (2026, 10)).state, UNKNOWN)

    def test_gap_reason_states_the_uncovered_dates(self):
        status = make_catalog().coverage_for("desk-b", (2026, 10))
        self.assertIn("2026-10-16", status.reason)
        self.assertIn("2026-10-31", status.reason)

    def test_legacy_catalog_without_history_is_flagged_as_assumed(self):
        catalog = SourceCatalog([Source("desk-a", "east-asia", "zh")])
        status = catalog.coverage_for("desk-a", (2026, 10))
        self.assertEqual(status.state, COVERED)
        self.assertTrue(status.assumed)
        self.assertIn("assumed", status.reason)

    def test_explicit_empty_coverage_does_not_assume_coverage(self):
        catalog = SourceCatalog([Source("desk-a", "east-asia", "zh")], coverage=[])
        self.assertEqual(catalog.coverage_for("desk-a", (2026, 10)).state, UNCOVERED)

    def test_source_moved_region_is_judged_per_region(self):
        catalog = make_catalog()
        self.assertEqual(
            catalog.coverage_for("desk-c", (2026, 10), region="europe").state, COVERED
        )
        self.assertEqual(
            catalog.coverage_for("desk-c", (2026, 10), region="east-asia").state, UNCOVERED
        )
        self.assertEqual(
            catalog.coverage_for("desk-c", (2026, 11), region="east-asia").state, COVERED
        )

    def test_effective_region_follows_the_move(self):
        catalog = make_catalog()
        october = Observation("x", "desk-c", date(2026, 10, 6), "topic", 1, "europe")
        november = Observation("y", "desk-c", date(2026, 11, 6), "topic", 1, "europe")
        self.assertEqual(catalog.effective_region(october), "europe")
        self.assertEqual(catalog.effective_region(november), "east-asia")


class CoverageAwareComparisonTests(unittest.TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.result = compare_periods(
            OBSERVATIONS, (2026, 10), (2026, 11), catalog=self.catalog
        )["topic"]

    def test_raw_totals_are_preserved(self):
        self.assertEqual(self.result["start_value"], 12)
        self.assertEqual(self.result["end_value"], 23)

    def test_covered_total_excludes_partial_sources(self):
        # desk-b was paused for part of both months.
        self.assertEqual(self.result["start_covered_value"], 8)
        self.assertEqual(self.result["end_covered_value"], 14)

    def test_comparable_range_uses_only_sources_covered_in_both_periods(self):
        self.assertEqual(self.result["comparable_sources"], ["desk-a", "desk-c"])
        self.assertEqual(self.result["comparable_start_value"], 8)
        self.assertEqual(self.result["comparable_end_value"], 14)

    def test_partial_coverage_is_a_gap_with_a_reason_not_a_zero(self):
        gaps = {gap["source_id"]: gap for gap in self.result["start_coverage_gaps"]}
        self.assertEqual(gaps["desk-b"]["state"], PARTIAL)
        self.assertIn("not covered", gaps["desk-b"]["reason"])
        self.assertTrue(any("unknown rather than zero" in caveat
                            for caveat in self.result["caveats"]))

    def test_covered_scope_silence_is_a_real_zero(self):
        rows = [
            Observation("o1", "desk-a", date(2026, 10, 5), "topic", 5),
            # Only the partially covered desk-b reports on "other-topic".
            Observation("o2", "desk-b", date(2026, 10, 6), "other-topic", 4),
        ]
        result = compare_periods(
            rows, (2026, 10), (2026, 11), catalog=self.catalog
        )["other-topic"]
        # The fully covered sources were silent on this topic, which is a real
        # zero within covered scope -- not a missing value.
        self.assertEqual(result["start_covered_value"], 0.0)
        self.assertEqual(result["start_value"], 4)
        self.assertEqual(result["start_coverage_gaps"][0]["source_id"], "desk-b")

    def test_new_source_does_not_fake_overall_growth(self):
        catalog = SourceCatalog(
            [Source("old", "east-asia", "zh"), Source("new", "east-asia", "zh")],
            [
                CoverageSpan("old", "east-asia", date(2026, 1, 1)),
                CoverageSpan("new", "east-asia", date(2026, 11, 1)),
            ],
        )
        rows = [
            Observation("o1", "old", date(2026, 10, 5), "topic", 5),
            Observation("o2", "old", date(2026, 11, 5), "topic", 5),
            Observation("o3", "new", date(2026, 11, 6), "topic", 10),
        ]
        result = compare_periods(rows, (2026, 10), (2026, 11), catalog=catalog)["topic"]
        self.assertEqual(result["start_value"], 5)
        self.assertEqual(result["end_value"], 15)
        # Like-for-like, reporting did not change at all.
        self.assertEqual(result["comparable_sources"], ["old"])
        self.assertEqual(result["comparable_start_value"], 5)
        self.assertEqual(result["comparable_end_value"], 5)
        self.assertTrue(any("newly in scope" in caveat for caveat in result["caveats"]))

    def test_source_leaving_scope_is_not_counted_as_zero(self):
        catalog = SourceCatalog(
            [Source("old", "east-asia", "zh")],
            [CoverageSpan("old", "east-asia", date(2026, 1, 1), date(2026, 10, 31))],
        )
        rows = [Observation("o1", "old", date(2026, 10, 5), "topic", 5)]
        result = compare_periods(rows, (2026, 10), (2026, 11), catalog=catalog)["topic"]
        self.assertEqual(result["end_value"], 0)
        self.assertIsNone(result["end_covered_value"])
        self.assertTrue(any("left the scope" in caveat for caveat in result["caveats"]))

    def test_region_trend_follows_the_region_move(self):
        east_asia = compare_periods(
            OBSERVATIONS, (2026, 10), (2026, 11), region="east-asia", catalog=self.catalog
        )["topic"]
        europe = compare_periods(
            OBSERVATIONS, (2026, 10), (2026, 11), region="europe", catalog=self.catalog
        )["topic"]
        # desk-c's October value moved from europe to east-asia in November.
        self.assertEqual(east_asia["start_value"], 9)
        self.assertEqual(east_asia["end_value"], 23)
        self.assertEqual(europe["start_value"], 3)
        self.assertEqual(europe["end_value"], 0)
        self.assertEqual(east_asia["comparable_sources"], ["desk-a"])
        self.assertIsNone(europe["end_covered_value"])

    def test_unknown_source_is_reported_not_silently_counted(self):
        rows = [Observation("o1", "ghost", date(2026, 10, 5), "topic", 4)]
        result = compare_periods(rows, (2026, 10), (2026, 11), catalog=self.catalog)["topic"]
        self.assertEqual(result["start_value"], 4)
        gaps = {gap["source_id"]: gap for gap in result["start_coverage_gaps"]}
        self.assertEqual(gaps["ghost"]["state"], UNKNOWN)

    def test_without_catalog_coverage_is_unverified_and_not_zero(self):
        result = compare_periods(OBSERVATIONS, (2026, 10), (2026, 11))["topic"]
        self.assertEqual(result["coverage_state"], "unverified")
        self.assertIsNone(result["start_covered_value"])
        self.assertIsNone(result["comparable_start_value"])
        self.assertEqual(result["start_value"], 12)


class ReportSnapshotTests(unittest.TestCase):
    def test_report_keeps_the_coverage_used_even_after_catalog_changes(self):
        catalog = make_catalog()
        result = compare_periods_with_snapshot(
            OBSERVATIONS, (2026, 10), (2026, 11), catalog=catalog
        )
        report = create_coverage_report("r1", "weekly", result)
        catalog.record_coverage(CoverageSpan("desk-b", "east-asia", date(2026, 10, 1)))

        # The catalog now considers desk-b covered in October, but the issued
        # report still describes the scope it was built with.
        self.assertEqual(
            catalog.coverage_for("desk-b", (2026, 10)).state, COVERED
        )
        start_snapshot = report.coverage["start"]
        self.assertEqual(start_snapshot["sources"]["desk-b"]["state"], PARTIAL)
        self.assertIn("desk-b", [gap["source_id"] for gap in start_snapshot["gaps"]])
        self.assertEqual(report.comparison["topic"]["comparable_sources"], ["desk-a", "desk-c"])

    def test_report_lists_scope_and_policy(self):
        result = compare_periods_with_snapshot(
            OBSERVATIONS, (2026, 10), (2026, 11), catalog=make_catalog()
        )
        report = create_coverage_report("r2", "weekly", result)
        self.assertEqual(report.source_ids, ("desk-a", "desk-c"))
        self.assertIn("whole period", report.policy)
        self.assertEqual(report.period_start, "2026-10-01")
        self.assertEqual(report.period_end, "2026-11-30")

    def test_snapshot_requires_a_catalog(self):
        with self.assertRaises(ValueError):
            compare_periods_with_snapshot(OBSERVATIONS, (2026, 10), (2026, 11))


class ParticipationTrailTests(unittest.TestCase):
    def test_reviewers_can_trace_every_source_and_its_reason(self):
        result = compare_periods_with_snapshot(
            OBSERVATIONS, (2026, 10), (2026, 11), catalog=make_catalog()
        )
        trail = {row["source_id"]: row for row in result["participation"]}
        self.assertTrue(trail["desk-a"]["in_comparable_range"])
        self.assertFalse(trail["desk-b"]["in_comparable_range"])
        self.assertEqual(trail["desk-b"]["start_state"], PARTIAL)
        self.assertIn("2026-10-16", trail["desk-b"]["reason"])
        self.assertTrue(trail["desk-c"]["in_comparable_range"])

    def test_trail_is_frozen_into_the_report(self):
        catalog = make_catalog()
        report = create_coverage_report("r3", "weekly", compare_periods_with_snapshot(
            OBSERVATIONS, (2026, 10), (2026, 11), catalog=catalog
        ))
        catalog.record_coverage(CoverageSpan("desk-b", "east-asia", date(2026, 10, 1)))
        trail = {row["source_id"]: row for row in report.participation}
        self.assertFalse(trail["desk-b"]["in_comparable_range"])
        self.assertEqual(trail["desk-b"]["start_state"], PARTIAL)


if __name__ == "__main__":
    unittest.main()
