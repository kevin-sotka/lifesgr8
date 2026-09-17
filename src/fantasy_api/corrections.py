"""
The NFL stat correction window.

Stat corrections for a week are typically published by the Thursday after it
ends. Until then a finished week is not final: ingest keeps refetching it, and a
score that disagrees with Yahoo is a warning rather than a build failure.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

CORRECTION_DAYS = 4  # a week ending Monday is final from Friday


def is_final(week_end: Optional[str], today: date) -> bool:
    if not week_end:
        return False
    try:
        end = date.fromisoformat(week_end)
    except ValueError:
        return False
    return today > end + timedelta(days=CORRECTION_DAYS)
