import hashlib
import datetime
import crud
from database import SessionLocal


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

bakery_token = {}

def get_token(bakery_id):
    if bakery_id not in bakery_token:
        db = SessionLocal()
        try:
            bakery = crud.get_bakery(db, bakery_id)
            if not bakery: raise ValueError
            bakery_token[bakery_id] = bakery.token
        finally:
            db.close()
    return bakery_token[bakery_id]


def verify_bakery_token(token: str, bakery_id: int) -> bool:
    return get_token(bakery_id) == token


class TokenBlacklist:
    def __init__(self, r):
        self.r = r
    async def add(self, token: str, ttl: int):
        await self.r.set(token, 1, ex=ttl, nx=True)
    async def is_blacklisted(self, token: str) -> bool:
        return await self.r.exists(token) == 1
