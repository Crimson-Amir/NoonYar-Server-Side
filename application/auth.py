from datetime import datetime, timedelta
import jwt, hashlib
from jwt import ExpiredSignatureError, InvalidTokenError
from hashlib import md5
from fastapi import HTTPException
from application.setting import settings

def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=settings.ACCESS_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.ACCESS_TOKEN_SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict, expires_delta: timedelta = timedelta(minutes=settings.REFRESH_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.REFRESH_TOKEN_SECRET_KEY, algorithm=settings.ALGORITHM)

def hash_password_md5(password: str) -> str:
    password_bytes = password.encode()
    md5_hash = md5()
    md5_hash.update(password_bytes)
    return md5_hash.hexdigest()


def decode_token(token: str, key=settings.ACCESS_TOKEN_SECRET_KEY) -> dict:
    try:
        return jwt.decode(token, key, algorithms=settings.ALGORITHM)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Decod failed: Token expired")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Decod failed: Invalid token")


def hash_otp(code: int) -> str:
    return hashlib.sha256(str(code).encode()).hexdigest()[:24]


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


class TokenBlacklist:
    def __init__(self, r):
        self.r = r
    async def add(self, token: str, ttl: int):
        await self.r.set(token, 1, ex=ttl, nx=True)
    async def is_blacklisted(self, token: str) -> bool:
        return await self.r.exists(token) == 1


def set_cookie(response, key, value, max_age):
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        secure=False, # TODO: TRUE FOR HTTPS
        samesite="lax",
        max_age=max_age
    )

