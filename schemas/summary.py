"""Временной диапазон для одной операции консолидации."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SummaryPeriod:
    start: datetime
    end: datetime