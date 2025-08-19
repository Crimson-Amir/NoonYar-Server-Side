from datetime import datetime, timedelta
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from hashlib import md5
from fastapi import HTTPException
from private import REFRESH_SECRET_KEY, SECRET_KEY, ALGORITHM, REFRESH_TOKEN_EXP_MIN, ACCESS_TOKEN_EXP_MIN


def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=ACCESS_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict, expires_delta: timedelta = timedelta(minutes=REFRESH_TOKEN_EXP_MIN)):
    to_encode = data.copy()
    expire = datetime.now() + expires_delta
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, REFRESH_SECRET_KEY, algorithm=ALGORITHM)

def hash_password_md5(password: str) -> str:
    password_bytes = password.encode()
    md5_hash = md5()
    md5_hash.update(password_bytes)
    return md5_hash.hexdigest()


def decode_token(token: str, key=SECRET_KEY) -> dict:
    try:
        return jwt.decode(token, key, algorithms=ALGORITHM)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Decod failed: Token expired")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Decod failed: Invalid token")