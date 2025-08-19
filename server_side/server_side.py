from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
import jwt
from auth import create_access_token
from private import REFRESH_SECRET_KEY, SECRET_KEY, ACCESS_TOKEN_EXP_MIN, ALGORITHM
from user import authentication, user
from bakery import hardware_communication, management
from admin import manage
import utilities
import redis.asyncio as redis
from contextlib import asynccontextmanager
from mqtt_client import start_mqtt

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url("redis://localhost:6379", decode_responses=True)
    start_mqtt()
    yield
    await app.state.redis.aclose()

app = FastAPI(lifespan=lifespan)

# templates = Jinja2Templates(directory="templates")
# app.mount('/statics', StaticFiles(directory='statics'), name='static')

app.include_router(authentication.router)
app.include_router(user.router)
app.include_router(hardware_communication.router)
app.include_router(management.router)
app.include_router(manage.router)

@app.middleware("http")
async def authenticate_request(request: Request, call_next):

    exception_paths = [
        "/auth/sign-up", "/auth/enter-number", "/auth/verify-login",
        "/auth/verify-signup-otp", "/hc", "/docs", "/auth/logout"
    ]

    if any(request.url.path.startswith(path) for path in exception_paths):
        return await call_next(request)

    request.state.user = None
    access_token = request.cookies.get("access_token")
    refresh_token = request.cookies.get("refresh_token")
    blacklist = utilities.TokenBlacklist(request.app.state.redis)

    if access_token:
        try:
            if await blacklist.is_blacklisted(access_token):
                return JSONResponse(status_code=403, content={"detail": "Access token blacklisted"})
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=ALGORITHM)
            request.state.user = payload
            return await call_next(request)
        except jwt.ExpiredSignatureError:
            pass
        except jwt.InvalidTokenError:
            return JSONResponse(status_code=401, content={"detail": "Invalid access token"})

    if refresh_token:
        try:
            if await blacklist.is_blacklisted(refresh_token):
                return JSONResponse(status_code=403, content={"detail": "Refresh token blacklisted"})
            refresh_payload = jwt.decode(refresh_token, REFRESH_SECRET_KEY, algorithms=ALGORITHM)
            new_token = create_access_token({
                "user_id": refresh_payload["user_id"],
                "first_name": refresh_payload["first_name"]
            })

            request.state.user = jwt.decode(new_token, SECRET_KEY, algorithms=["HS256"])
            response = await call_next(request)
            utilities.set_cookie(response, "access_token", new_token, ACCESS_TOKEN_EXP_MIN * 60)
            return response
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return JSONResponse(status_code=401, content={"detail": "Invalid or expierd refresh token"})

    return JSONResponse(status_code=401, content={"detail": "Unauthorized: No token found"})
