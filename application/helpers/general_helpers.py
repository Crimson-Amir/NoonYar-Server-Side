from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib


def seconds_until_midnight_iran():
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())


def generate_daily_customer_token(bakery_id: int, ticket_id: int) -> str:
    """Generate a short, per-day token for a customer.

    The token is derived from (bakery_id, ticket_id, local Tehran date) and
    encoded into at most 5 base36 characters so it is compact enough for QR
    codes while remaining stable for that day.
    """
    tz = ZoneInfo("Asia/Tehran")
    today = datetime.now(tz).date().isoformat()

    payload = f"{bakery_id}-{ticket_id}-{today}".encode("utf-8")
    digest = hashlib.sha1(payload).digest()

    # Map the first 4 bytes into the range [0, 36**5) and encode in base36.
    num = int.from_bytes(digest[:4], "big")
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    base = len(alphabet)
    max_val = base ** 5
    num = num % max_val

    chars = []
    for _ in range(5):
        num, rem = divmod(num, base)
        chars.append(alphabet[rem])

    token = "".join(reversed(chars)).lstrip("0")
    return token or "0"
