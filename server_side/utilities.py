import hashlib
import datetime

def hash_otp(code: str) -> str:
    return hashlib.sha256(str(code).encode()).hexdigest()[:24]

def get_expiry(minutes=10):
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)