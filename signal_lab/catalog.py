from dataclasses import dataclass
from datetime import date, timedelta

from .records import CoverageSpan, Source, period_bounds

COVERED = "covered"
PARTIAL = "partial"
UNCOVERED = "uncovered"
UNKNOWN = "unknown"

COVERAGE_POLICY = (
    "A source contributes to a period's coverage-aware totals only when its "
    "coverage spans the whole period. A covered source with no observations "
    "counts as a real zero; a source without coverage is reported as unknown "
    "and is never treated as zero. A source is comparable across two periods "
    "only when it is covered for the whole of both periods."
)


@dataclass(frozen=True)
class CoverageStatus:
    """Coverage judgement for one source over one period."""

    source_id: str
    state: str
    region: str | None
    regions: tuple[str, ...]
    reason: str
    assumed: bool = False
    spans: tuple[CoverageSpan, ...] = ()

    @property
    def covers_whole_period(self):
        return self.state == COVERED


class SourceCatalog:
    """Sources plus their historical coverage.

    ``coverage=None`` keeps the old catalog behaviour: every known source is
    treated as covered for all time, and the assumption is flagged in every
    status. Passing an explicit (possibly empty) collection of
    :class:`CoverageSpan` turns that assumption off, so a source with no span
    for a period is reported as uncovered rather than silently counted.
    """

    def __init__(self, sources=(), coverage=None):
        self._sources = {source.source_id: source for source in sources}
        self._spans = {source_id: [] for source_id in self._sources}
        self._assumed = set()
        if coverage is None:
            for source in self._sources.values():
                self._spans[source.source_id].append(
                    CoverageSpan(
                        source_id=source.source_id,
                        region=source.region,
                        start=date.min,
                        note="implicit coverage under legacy catalog semantics",
                    )
                )
                self._assumed.add(source.source_id)
        else:
            for span in coverage:
                self.record_coverage(span)

    def get(self, source_id):
        return self._sources.get(source_id)

    def source_ids(self):
        return tuple(sorted(self._sources))

    def active_sources(self, region=None):
        values = (source for source in self._sources.values() if source.active)
        if region is not None:
            values = (source for source in values if source.region == region)
        return tuple(sorted(values, key=lambda source: source.source_id))

    def record_coverage(self, span):
        """Record one inclusive coverage span for a source."""
        if span.source_id in self._assumed:
            self._spans[span.source_id] = []
            self._assumed.discard(span.source_id)
        self._spans.setdefault(span.source_id, []).append(span)

    def spans(self, source_id):
        return tuple(sorted(
            self._spans.get(source_id, ()),
            key=lambda span: (span.start, span.end or date.max, span.region),
        ))

    def regions_for_source(self, source_id):
        source = self._sources.get(source_id)
        regions = {span.region for span in self._spans.get(source_id, ())}
        if source is not None:
            regions.add(source.region)
        return tuple(sorted(regions))

    def source_region_on(self, source_id, day):
        """Region in effect for a source on a given day, if it was covered."""
        covering = [span for span in self.spans(source_id) if span.covers(day)]
        if covering:
            # A later span supersedes an earlier one when they overlap.
            return covering[-1].region
        source = self._sources.get(source_id)
        return source.region if source is not None else None

    def effective_region(self, observation):
        """Region to attribute an observation to for trend calculations.

        A source's region can change over time, so the region recorded on the
        coverage span at the observation date takes precedence over the
        source's current region and the region copied onto the observation.
        Legacy catalogs carry no real history, so they keep deferring to the
        observation's own region.
        """
        if observation.source_id in self._assumed:
            return observation.region
        covered_region = self.source_region_on(observation.source_id, observation.observed_on)
        return covered_region or observation.region

    def coverage_for(self, source_id, period, *, region=None):
        """Judge whether a source was covered throughout ``period``.

        ``period`` is either ``(year, month)`` or an inclusive
        ``(start_date, end_date)`` pair. With ``region`` set, only spans in
        that region count, so a source that moved regions is judged separately
        for each region.

        A source covered throughout yields ``COVERED``; a source with some but
        not all of the period covered yields ``PARTIAL``; a source with no
        coverage in the period yields ``UNCOVERED``; a source absent from the
        catalog yields ``UNKNOWN``. Only ``COVERED`` may be read as a real
        zero when there are no observations.
        """
        period_start, period_end = period_bounds(period)
        source = self._sources.get(source_id)
        if source is None:
            return CoverageStatus(
                source_id=source_id,
                state=UNKNOWN,
                region=None,
                regions=(),
                reason="source is not in the source catalog; coverage cannot be established",
            )
        all_spans = self.spans(source_id)
        assumed = source_id in self._assumed
        if assumed:
            # Legacy catalogs have no region history, so the assumed coverage
            # cannot be narrowed to one region.
            region = None
        if region is None:
            scope_spans = all_spans
            other_regions = ()
        else:
            scope_spans = tuple(span for span in all_spans if span.region == region)
            other_regions = tuple(sorted({span.region for span in all_spans} - {region}))
        overlapping = tuple(
            span for span in scope_spans if span.overlaps(period_start, period_end)
        )
        if not overlapping:
            if region is not None:
                reason = f"no coverage in region {region} during the period"
                if other_regions:
                    reason += "; recorded regions: " + ", ".join(other_regions)
            else:
                reason = self._uncovered_reason(all_spans, period_start, period_end)
            return CoverageStatus(
                source_id=source_id,
                state=UNCOVERED,
                region=None,
                regions=(),
                reason=reason,
                assumed=assumed,
            )
        regions = tuple(sorted({span.region for span in overlapping}))
        intervals = self._clipped_intervals(overlapping, period_start, period_end)
        if self._intervals_cover(intervals, period_start, period_end):
            if region is not None:
                judged_region = region
                reason = f"covered throughout the period in region {region}"
            else:
                judged_region = regions[0] if len(regions) == 1 else None
                reason = "covered throughout the period"
                if len(regions) > 1:
                    reason = (
                        "covered throughout the period; region changed: "
                        + self._region_sequence(overlapping)
                    )
            if assumed:
                reason = "no coverage history recorded; assumed covered throughout the period"
            return CoverageStatus(
                source_id=source_id,
                state=COVERED,
                region=judged_region,
                regions=regions,
                reason=reason,
                assumed=assumed,
                spans=overlapping,
            )
        gaps = self._gaps(intervals, period_start, period_end)
        gap_text = ", ".join(
            f"{gap_start.isoformat()} to {gap_end.isoformat()}"
            for gap_start, gap_end in gaps
        )
        judged_region = region if region is not None else (
            regions[0] if len(regions) == 1 else None
        )
        reason = f"not covered for {gap_text}"
        if region is not None:
            reason = f"not covered in region {region} for {gap_text}"
        return CoverageStatus(
            source_id=source_id,
            state=PARTIAL,
            region=judged_region,
            regions=regions,
            reason=reason,
            assumed=assumed,
            spans=overlapping,
        )

    def in_scope_sources(self, period, region=None):
        """Source ids covered for the whole period, optionally in a region."""
        values = []
        for source_id in self.source_ids():
            status = self.coverage_for(source_id, period, region=region)
            if status.state != COVERED:
                continue
            values.append(source_id)
        return tuple(values)

    def coverage_snapshot(self, start, end=None, *, region=None):
        """Freeze the coverage judgement used for one or two periods.

        With ``end`` omitted this returns a snapshot for the single period
        ``start``. With both arguments it returns separate snapshots keyed
        ``start`` and ``end``, mirroring :func:`compare_periods`.
        """
        if end is None:
            return self._single_snapshot(start, region=region)
        return {
            "region": region,
            "policy": COVERAGE_POLICY,
            "start": self._single_snapshot(start, region=region),
            "end": self._single_snapshot(end, region=region),
        }

    def _single_snapshot(self, period, *, region):
        period_start, period_end = period_bounds(period)
        sources = {}
        gaps = []
        in_scope = []
        for source_id in self.source_ids():
            status = self.coverage_for(source_id, period, region=region)
            in_region = status.state == COVERED
            if in_region:
                in_scope.append(source_id)
            elif self._source_matches_region(source_id, status, region):
                gaps.append({"source_id": source_id, "state": status.state, "reason": status.reason})
            sources[source_id] = {
                "state": status.state,
                "region": status.region,
                "regions": list(status.regions),
                "reason": status.reason,
                "assumed": status.assumed,
                "spans": [
                    {
                        "region": span.region,
                        "start": span.start.isoformat(),
                        "end": span.end.isoformat() if span.end is not None else None,
                        "note": span.note,
                    }
                    for span in status.spans
                ],
            }
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "region": region,
            "policy": COVERAGE_POLICY,
            "sources": sources,
            "in_scope_sources": sorted(in_scope),
            "gaps": gaps,
        }

    def _source_matches_region(self, source_id, status, region):
        if region is None:
            return True
        return region in self.regions_for_source(source_id)

    @staticmethod
    def _clipped_intervals(spans, period_start, period_end):
        intervals = []
        for span in spans:
            start = max(span.start, period_start)
            end = min(span.end or period_end, period_end)
            if start <= end:
                intervals.append((start, end))
        return sorted(intervals)

    @staticmethod
    def _intervals_cover(intervals, period_start, period_end):
        cursor = period_start
        for start, end in intervals:
            if start > cursor:
                return False
            if end >= cursor:
                cursor = end + timedelta(days=1)
            if cursor > period_end:
                return True
        return cursor > period_end

    @staticmethod
    def _gaps(intervals, period_start, period_end):
        gaps = []
        cursor = period_start
        for start, end in intervals:
            if start > cursor:
                gaps.append((cursor, start - timedelta(days=1)))
            if end >= cursor:
                cursor = end + timedelta(days=1)
        if cursor <= period_end:
            gaps.append((cursor, period_end))
        return gaps

    @staticmethod
    def _region_sequence(spans):
        sequence = []
        for span in sorted(spans, key=lambda item: (item.start, item.end or date.max)):
            if not sequence or sequence[-1] != span.region:
                sequence.append(span.region)
        return " -> ".join(sequence)

    @staticmethod
    def _uncovered_reason(all_spans, period_start, period_end):
        before = [span for span in all_spans if span.end is not None and span.end < period_start]
        after = [span for span in all_spans if span.start > period_end]
        if not all_spans:
            return "no coverage history recorded for the period"
        parts = ["no coverage in the period"]
        if before:
            last = max(before, key=lambda span: span.end)
            parts.append(f"last coverage ended {last.end.isoformat()}")
        if after:
            first = min(after, key=lambda span: span.start)
            parts.append(f"next coverage starts {first.start.isoformat()}")
        if not before and not after:
            parts.append("no span overlaps the period")
        return "; ".join(parts)
