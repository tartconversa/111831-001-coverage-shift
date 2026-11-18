import calendar
from dataclasses import dataclass
from datetime import date


def period_bounds(period):
    """Return the inclusive ``(start, end)`` dates for a period.

    ``period`` is either ``(year, month)`` or an explicit inclusive
    ``(start_date, end_date)`` pair.
    """
    if (
        isinstance(period, (tuple, list))
        and len(period) == 2
        and all(isinstance(part, int) for part in period)
    ):
        year, month = period
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    start, end = period
    if end < start:
        raise ValueError("period end must not be before start")
    return start, end


@dataclass(frozen=True)
class Source:
    source_id: str
    region: str
    language: str
    active: bool = True


@dataclass(frozen=True)
class CoverageSpan:
    """A period during which a source was part of the observation scope.

    Spans are inclusive. A source may have several spans over time, which
    models pauses, resumptions and regional moves. A gap between spans means
    the source was not covered, not that it reported zero.
    """

    source_id: str
    region: str
    start: date
    end: date | None = None
    note: str = ""

    def __post_init__(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("coverage span end must not be before start")

    def covers(self, day):
        return self.start <= day and (self.end is None or day <= self.end)

    def overlaps(self, start, end):
        return self.start <= end and (self.end is None or self.end >= start)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    source_id: str
    observed_on: date
    topic: str
    value: float
    region: str = "global"
