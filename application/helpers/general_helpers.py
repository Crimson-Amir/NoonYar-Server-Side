from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def seconds_until_midnight_iran():
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())
