"""Настройки консолидации, привязанные к конкретному чату."""

from dataclasses import dataclass
from typing import Literal


IntervalType = Literal[
    "daily",
    "weekly",
    "custom",
]


@dataclass(slots=True)
class ChatSettings:
    chat_id: int

    enabled: bool = False

    interval_type: IntervalType = "weekly"

    custom_days: int | None = None

    summary_hour: int = 9

    timezone: str = "Europe/Moscow"