"""Расчёт временного окна, которое попадает в консолидацию."""

from datetime import datetime, timedelta, timezone

from app.schemas.summary import SummaryPeriod


def period_for_interval(interval, now=None, custom_days=None):
    """Вернуть период от текущего момента назад на выбранное число дней.

    Важный принцип: период считается в одном месте, чтобы кнопочный UI,
    сохранённые настройки и ручной запрос использовали одинаковую логику.
    """
    now = now or datetime.now(timezone.utc)

    if interval == "daily":
        days = 1
    elif interval == "weekly":
        days = 7
    elif interval == "custom":
        if custom_days is None or not 1 <= custom_days <= 30:
            raise ValueError("custom_days must be between 1 and 30")
        days = custom_days
    else:
        raise ValueError(f"Unknown interval: {interval}")

    return SummaryPeriod(now - timedelta(days=days), now)
