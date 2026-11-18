import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .catalog import COVERAGE_POLICY


@dataclass(frozen=True)
class AnalysisReport:
    """A report frozen at creation time.

    ``coverage`` and ``comparison`` are deep-copied so later catalog edits or
    source movements cannot rewrite what an already-issued report said.
    ``source_ids`` records the scope the report used.
    """

    report_id: str
    title: str
    period_start: str
    period_end: str
    source_ids: tuple[str, ...]
    measures: dict[str, float]
    created_at: str
    coverage: dict | None = field(default=None)
    comparison: dict | None = field(default=None)
    participation: tuple[dict, ...] | None = field(default=None)
    policy: str | None = field(default=None)


def create_report(report_id, title, period_start, period_end, source_ids, measures,
                  *, coverage=None, comparison=None, participation=None, policy=None):
    return AnalysisReport(
        report_id=report_id, title=title, period_start=period_start, period_end=period_end,
        source_ids=tuple(sorted(set(source_ids))), measures=copy.deepcopy(dict(measures)),
        created_at=datetime.now(timezone.utc).isoformat(),
        coverage=copy.deepcopy(coverage),
        comparison=copy.deepcopy(comparison),
        participation=tuple(copy.deepcopy(tuple(participation)))
        if participation is not None else None,
        policy=policy,
    )


def create_coverage_report(report_id, title, result):
    """Build an immutable report from ``compare_periods_with_snapshot`` output.

    The coverage snapshot is stored alongside the comparison, so reviewers can
    trace each participating or excluded source and the reason from the report
    itself, without re-running the analysis against a catalog that has since
    changed.
    """
    coverage = result["coverage"]
    scope = set(coverage["start"]["in_scope_sources"])
    scope.update(coverage["end"]["in_scope_sources"])
    measures = {
        f"{topic}.{name}": value
        for topic, entry in result["comparison"].items()
        for name, value in entry.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return create_report(
        report_id,
        title,
        coverage["start"]["period_start"],
        coverage["end"]["period_end"],
        scope,
        measures,
        coverage=coverage,
        comparison=result["comparison"],
        participation=result.get("participation"),
        policy=result.get("policy", COVERAGE_POLICY),
    )
