"""
Pure date-range helpers for the pre-scan "which period should this scan
cover" option (Dashboard). Kept dependency-free (no Outlook/DB imports) so
the period math is unit-testable on its own.
"""
from datetime import datetime


def get_this_month_range(now=None):
    """
    'This month' = day 1 of the current month at 00:00:00 through right
    now (inclusive), per the "received" timestamp.
    """
    now = now or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).replace(day=1)
    return start, now


def get_custom_range(start_date, end_date):
    """
    Normalizes a user-picked from/to period. `end_date` is treated as
    inclusive of the whole day: if it carries no time-of-day component
    (i.e. it's midnight, as a date-only picker would produce), it's
    extended to 23:59:59.999999 so records received later that same day
    are still included.
    """
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    if (end_date.hour, end_date.minute, end_date.second, end_date.microsecond) == (0, 0, 0, 0):
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    return start_date, end_date
