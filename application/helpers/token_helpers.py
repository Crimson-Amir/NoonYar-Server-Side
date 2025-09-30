import hashlib, datetime
from application import crud
from application.database import SessionLocal
import logging

logging.getLogger("application")
bakery_token = {}

def hash_otp(code: int) -> str:
    return hashlib.sha256(str(code).encode()).hexdigest()[:24]

def get_expiry(minutes=10):
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)

def get_token(bakery_id):
    if bakery_id not in bakery_token:
        db = SessionLocal()
        try:
            bakery = crud.get_bakery(db, bakery_id)
            if not bakery:
                raise ValueError('No bakery found')
            bakery_token[bakery_id] = bakery.token
        finally:
            db.close()
    return bakery_token[bakery_id]

def set_cookie(response, key, value, max_age):
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        secure=False, # TODO: TRUE FOR HTTPS
        samesite="lax",
        max_age=max_age
    )

def verify_bakery_token(token: str, bakery_id: int) -> bool:
    return get_token(bakery_id) == token

class TokenBlacklist:
    def __init__(self, r):
        self.r = r
    async def add(self, token: str, ttl: int):
        await self.r.set(token, 1, ex=ttl, nx=True)
    async def is_blacklisted(self, token: str) -> bool:
        return await self.r.exists(token) == 1


class OTPStore:
    def __init__(self, r):
        self.r = r

    def set_otp(self, phone_number: str, otp: int, ttl: int = 300):
        hashed = hash_otp(otp)
        self.r.set(f"otp:{phone_number}", hashed, ex=ttl)
        # TODO: REMOVE THIS IN PRODUCTION
        self.r.set(f"otp_debug:{phone_number}", otp, ex=ttl)

    async def verify_otp(self, phone_number: str, otp: int) -> bool:
        key = f"otp:{phone_number}"
        hashed = await self.r.get(key)
        if not hashed:
            return False
        if hashed != hash_otp(otp):
            return False
        await self.r.delete(key)
        return True