from datetime import datetime, timedelta
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from hashlib import md5
from fastapi import HTTPException
from application.setting import settings

def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=settings.ACCESS_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict, expires_delta: timedelta = timedelta(minutes=settings.REFRESH_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.REFRESH_SECRET_KEY, algorithm=settings.ALGORITHM)

def hash_password_md5(password: str) -> str:
    password_bytes = password.encode()
    md5_hash = md5()
    md5_hash.update(password_bytes)
    return md5_hash.hexdigest()


def decode_token(token: str, key=settings.SECRET_KEY) -> dict:
    try:
        return jwt.decode(token, key, algorithms=settings.ALGORITHM)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Decod failed: Token expired")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Decod failed: Invalid token")