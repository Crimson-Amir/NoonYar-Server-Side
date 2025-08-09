import hashlib
import datetime

def hash_otp(code: str) -> str:
    return hashlib.sha256(str(code).encode()).hexdigest()[:24]

def get_expiry(minutes=10):
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)

def set_cookie(response, key, value, max_age):
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=max_age
    )
