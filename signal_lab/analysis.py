from collections import defaultdict
from dataclasses import dataclass

from .catalog import COVERED, PARTIAL, UNKNOWN, COVERAGE_POLICY
from .records import period_bounds


@dataclass(frozen=True)
class _PeriodData:
    totals: dict
    source_totals: dict
    sources: dict
    covered_sources: tuple
    gaps: tuple
    assumed_sources: tuple


def compare_periods(observations, start, end, *, region=None, catalog=None):
    """Compare two periods, separating raw counts from covered scope.

    The returned mapping keeps the original ``start_value`` / ``end_value`` /
    ``start_sources`` / ``end_sources`` keys (raw observations actually
    received). When a ``catalog`` with historical coverage is supplied, each
    topic also carries the coverage-aware view:

    * ``start_covered_value`` / ``end_covered_value``: totals from sources that
      were covered for the whole period. A covered source with no observations
      contributes a real zero, so silence is not confused with absence.
    * ``start_covered_sources`` / ``end_covered_sources``: source ids in scope.
    * ``start_coverage_gaps`` / ``end_coverage_gaps``: sources that could not
      be fully observed, with the reason.
    * ``comparable_sources`` and ``comparable_start_value`` /
      ``comparable_end_value``: limited to sources covered for the whole of
      *both* periods, the only basis for a like-for-like change.
    * ``caveats``: human-readable explanations of changes driven by scope
      changes rather than reporting behaviour.

    Without a ``catalog`` the raw keys are unchanged and the coverage-aware
    keys are ``None`` with an ``unverified`` state, because no coverage history
    exists to justify treating any silence as a zero.
    """
    period_data = _period_data(observations, start, end, region, catalog)
    start_data, end_data = period_data
    topics = sorted(set(start_data.totals) | set(end_data.totals))
    comparable = tuple(sorted(set(start_data.covered_sources) & set(end_data.covered_sources)))
    return {
        topic: _topic_result(topic, start_data, end_data, start, end, comparable, catalog)
        for topic in topics
    }


def compare_periods_with_snapshot(observations, start, end, *, region=None, catalog=None):
    """Return the comparison plus a frozen coverage snapshot for reports."""
    if catalog is None:
        raise ValueError("a source catalog with coverage history is required")
    comparison = compare_periods(observations, start, end, region=region, catalog=catalog)
    coverage = catalog.coverage_snapshot(start, end, region=region)
    observed = set()
    for data in _period_data(observations, start, end, region, catalog):
        observed.update(data.source_totals)
    return {
        "comparison": comparison,
        "coverage": coverage,
        "participation": _participation(catalog, start, end, region, observed),
        "policy": COVERAGE_POLICY,
        "region": region,
        "start": _period_label(start),
        "end": _period_label(end),
    }


def _period_data(observations, start, end, region, catalog):
    return [
        _collect(observations, period_bounds(period), period, region, catalog)
        for period in (start, end)
    ]


def _participation(catalog, start, end, region, observed):
    """Explain, per source, whether it entered the comparable range and why."""
    known = set(catalog.source_ids())
    rows = []
    for source_id in sorted(known | observed):
        start_status = catalog.coverage_for(source_id, start, region=region)
        end_status = catalog.coverage_for(source_id, end, region=region)
        in_comparable = start_status.state == COVERED and end_status.state == COVERED
        if in_comparable:
            reason = "covered for the whole of both periods"
        else:
            reasons = []
            if start_status.state != COVERED:
                reasons.append(f"start period: {start_status.reason}")
            if end_status.state != COVERED:
                reasons.append(f"end period: {end_status.reason}")
            reason = "; ".join(reasons)
        rows.append({
            "source_id": source_id,
            "known_to_catalog": source_id in known,
            "start_state": start_status.state,
            "end_state": end_status.state,
            "in_comparable_range": in_comparable,
            "reason": reason,
        })
    return rows


def _collect(observations, bound, period, region, catalog):
    totals = defaultdict(float)
    source_totals = defaultdict(lambda: defaultdict(float))
    sources = defaultdict(set)
    in_scope = set()
    assumed = set()
    gaps = []
    if catalog is not None:
        for source_id in catalog.source_ids():
            status = catalog.coverage_for(source_id, period, region=region)
            if status.state == COVERED:
                in_scope.add(source_id)
                if status.assumed:
                    assumed.add(source_id)
            elif _relevant_to_region(catalog, source_id, region):
                gaps.append({
                    "source_id": source_id,
                    "state": status.state,
                    "reason": status.reason,
                })
    observed_sources = set()
    for observation in observations:
        observed_on = observation.observed_on
        if not (bound[0] <= observed_on <= bound[1]):
            continue
        effective_region = (
            catalog.effective_region(observation)
            if catalog is not None
            else observation.region
        )
        if region is not None and effective_region != region:
            continue
        topic = observation.topic
        totals[topic] += observation.value
        source_totals[observation.source_id][topic] += observation.value
        sources[topic].add(observation.source_id)
        observed_sources.add(observation.source_id)
    if catalog is not None:
        # A source that reported but is not in the catalog has unverifiable
        # coverage; keep it visible to reviewers instead of dropping it.
        known = {gap["source_id"] for gap in gaps}
        for source_id in sorted(observed_sources):
            if source_id not in known and catalog.get(source_id) is None:
                gaps.append({
                    "source_id": source_id,
                    "state": UNKNOWN,
                    "reason": "source is not in the source catalog; coverage cannot be established",
                })
    return _PeriodData(
        totals=dict(totals),
        source_totals={
            source_id: dict(values) for source_id, values in source_totals.items()
        },
        sources={topic: frozenset(values) for topic, values in sources.items()},
        covered_sources=tuple(sorted(in_scope)),
        gaps=tuple(sorted(gaps, key=lambda gap: gap["source_id"])),
        assumed_sources=tuple(sorted(assumed)),
    )


def _relevant_to_region(catalog, source_id, region):
    if region is None:
        return True
    return region in catalog.regions_for_source(source_id)


def _covered_total(data, topic, source_ids):
    """Sum a topic over ``source_ids``, or ``None`` when scope is empty.

    An empty scope has no evidence either way, so it must not be reported as
    a zero total.
    """
    if not source_ids:
        return None
    return float(sum(
        data.source_totals.get(source_id, {}).get(topic, 0.0)
        for source_id in source_ids
    ))


def _topic_result(topic, start_data, end_data, start, end, comparable, catalog):
    start_raw_sources = start_data.sources.get(topic, frozenset())
    end_raw_sources = end_data.sources.get(topic, frozenset())
    entry = {
        "start_period": _period_label(start),
        "end_period": _period_label(end),
        "start_value": start_data.totals.get(topic, 0.0),
        "end_value": end_data.totals.get(topic, 0.0),
        "start_sources": len(start_raw_sources),
        "end_sources": len(end_raw_sources),
    }
    if catalog is None:
        entry.update({
            "coverage_state": "unverified",
            "start_covered_value": None,
            "end_covered_value": None,
            "start_covered_sources": None,
            "end_covered_sources": None,
            "start_coverage_gaps": None,
            "end_coverage_gaps": None,
            "comparable_sources": None,
            "comparable_start_value": None,
            "comparable_end_value": None,
            "caveats": [
                "no coverage history was supplied, so missing observations "
                "cannot be distinguished from uncovered sources",
            ],
        })
        return entry

    start_covered_sources = list(start_data.covered_sources)
    end_covered_sources = list(end_data.covered_sources)
    comparable_sources = list(comparable)
    entry.update({
        "coverage_state": "evaluated",
        "start_covered_value": _covered_total(start_data, topic, start_covered_sources),
        "end_covered_value": _covered_total(end_data, topic, end_covered_sources),
        "start_covered_sources": start_covered_sources,
        "end_covered_sources": end_covered_sources,
        "start_coverage_gaps": list(start_data.gaps),
        "end_coverage_gaps": list(end_data.gaps),
        "comparable_sources": comparable_sources,
        "comparable_start_value": _covered_total(start_data, topic, comparable_sources),
        "comparable_end_value": _covered_total(end_data, topic, comparable_sources),
        "caveats": _caveats(
            start_data, end_data,
            set(start_covered_sources), set(end_covered_sources),
        ),
    })
    return entry


def _caveats(start_data, end_data, start_covered, end_covered):
    notes = []
    assumed = sorted(
        (set(start_data.assumed_sources) & start_covered)
        | (set(end_data.assumed_sources) & end_covered)
    )
    if assumed:
        subject = "it is" if len(assumed) == 1 else "they are"
        possessive = "its" if len(assumed) == 1 else "their"
        notes.append(
            "no coverage history was recorded for "
            + ", ".join(assumed)
            + f"; {subject} assumed covered for the whole period, so "
            f"{possessive} silence cannot be verified as a real zero"
        )
    gaps = {
        "start": {gap["source_id"]: gap for gap in start_data.gaps},
        "end": {gap["source_id"]: gap for gap in end_data.gaps},
    }
    handled = set()
    for source_id in sorted(end_covered - start_covered):
        handled.add(source_id)
        start_gap = gaps["start"].get(source_id)
        if start_gap is not None and start_gap["state"] == PARTIAL:
            notes.append(
                f"source {source_id} was only partially covered in the start "
                f"period ({start_gap['reason']}), so it is excluded from the "
                "comparable range"
            )
        else:
            notes.append(
                f"source {source_id} is newly in scope and cannot be compared; "
                "its contribution is excluded from the comparable range"
            )
    for source_id in sorted(start_covered - end_covered):
        handled.add(source_id)
        end_gap = gaps["end"].get(source_id)
        if end_gap is not None and end_gap["state"] == PARTIAL:
            notes.append(
                f"source {source_id} was only partially covered in the end "
                f"period ({end_gap['reason']}), so its silence is unknown "
                "rather than zero"
            )
        else:
            notes.append(
                f"source {source_id} left the scope, so its silence in the end "
                "period is unknown rather than zero"
            )
    for source_id in sorted(set(gaps["start"]) | set(gaps["end"])):
        if source_id in handled:
            continue
        reasons = []
        for label, gap in (("start", gaps["start"].get(source_id)),
                           ("end", gaps["end"].get(source_id))):
            if gap is not None:
                reasons.append(f"{label} period: {gap['reason']}")
        notes.append(
            f"source {source_id} was not fully covered in the compared periods "
            f"({'; '.join(reasons)}); its silence is unknown rather than zero"
        )
    if not start_covered:
        notes.append(
            "no sources were covered for the whole start period, so the start "
            "period has no covered total"
        )
    if not end_covered:
        notes.append(
            "no sources were covered for the whole end period, so the end "
            "period has no covered total"
        )
    return notes


def _period_label(period):
    if (
        isinstance(period, (tuple, list))
        and len(period) == 2
        and all(isinstance(part, int) for part in period)
    ):
        return f"{period[0]:04d}-{period[1]:02d}"
    start, end = period
    return f"{start.isoformat()}..{end.isoformat()}"
