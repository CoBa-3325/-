from datetime import (
    datetime,
    timezone,
)

from app.services.period_service import (
    period_for_interval,
)


def test_daily():

    now = datetime(
        2026,
        1,
        8,
        tzinfo=timezone.utc,
    )

    period = period_for_interval(
        "daily",
        now,
    )

    assert (
        period.end - period.start
    ).days == 1


def test_weekly():

    now = datetime(
        2026,
        1,
        8,
        tzinfo=timezone.utc,
    )

    period = period_for_interval(
        "weekly",
        now,
    )

    assert (
        period.end - period.start
    ).days == 7


def test_custom():

    now = datetime(
        2026,
        1,
        8,
        tzinfo=timezone.utc,
    )

    period = period_for_interval(
        "custom",
        now,
        14,
    )

    assert (
        period.end - period.start
    ).days == 14