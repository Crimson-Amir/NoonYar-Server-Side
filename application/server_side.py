from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import jwt, asyncio
from logger_config import fastapi_listener
from auth import create_access_token
import aiomqtt
from setting import settings
from user import authentication, user
from bakery import hardware_communication, management
from admin import manage
import redis.asyncio as redis
from contextlib import asynccontextmanager
from mqtt_client import mqtt_handler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from helpers.token_helpers import TokenBlacklist, set_cookie
import tasks
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    fastapi_listener.start()
    app.state.mqtt_client = aiomqtt.Client(hostname=settings.MQTT_BROKER_HOST, port=settings.MQTT_BROKER_PORT)
    app.state.mqtt_task = asyncio.create_task(mqtt_handler(app))

    async def send_task_with_retry():
        max_attempts = 10
        delay = 2

        for attempt in range(1, max_attempts + 1):
            try:
                tasks.initialize_bakeries_redis_sets.delay(mid_night=False)
                break
            except Exception as e:
                if attempt < max_attempts:
                    print(f"✗ Failed to send task (attempt {attempt}/{max_attempts}): {e}")
                    await asyncio.sleep(delay)

    asyncio.create_task(send_task_with_retry())
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Tehran"))
    scheduler.add_job(
        tasks.initialize_bakeries_redis_sets.delay,
        CronTrigger(hour=0, minute=0, timezone=ZoneInfo("Asia/Tehran")),
        args=[True]
    )
    scheduler.start()

    yield
    fastapi_listener.stop()
    await app.state.redis.aclose()

    app.state.mqtt_task.cancel()
    try: await app.state.mqtt_task
    except asyncio.CancelledError: pass

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# templates = Jinja2Templates(directory="templates")
# application.mount('/statics', StaticFiles(directory='statics'), name='static')

app.include_router(authentication.router)
app.include_router(user.router)
app.include_router(hardware_communication.router)
app.include_router(management.router)
app.include_router(manage.router)

@app.middleware("http")
async def authenticate_request(request: Request, call_next):

    exception_paths = ["/auth/logout-successful", "/auth/sign-up", "/auth/enter-number", "/auth/verify-otp",
                       "/hc", "/docs", "/auth/logout"]

    if any(request.url.path.startswith(path) for path in exception_paths):
        return await call_next(request)

    request.state.user = None
    access_token = request.cookies.get("access_token")
    refresh_token = request.cookies.get("refresh_token")
    blacklist = TokenBlacklist(request.app.state.redis)

    if access_token:
        try:
            if await blacklist.is_blacklisted(access_token):
                return JSONResponse(status_code=403, content={"detail": "Access token blacklisted"})
            payload = jwt.decode(access_token, settings.SECRET_KEY, algorithms=settings.ALGORITHM)
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
            refresh_payload = jwt.decode(refresh_token, settings.REFRESH_SECRET_KEY, algorithms=settings.ALGORITHM)
            new_token = create_access_token({
                "user_id": refresh_payload["user_id"],
                "first_name": refresh_payload["first_name"]
            })

            request.state.user = jwt.decode(new_token, settings.SECRET_KEY, algorithms=["HS256"])
            response = await call_next(request)
            set_cookie(response, "access_token", new_token, settings.ACCESS_TOKEN_EXP_MIN * 60)
            return response
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return JSONResponse(status_code=401, content={"detail": "Invalid or expierd refresh token"})

    return JSONResponse(status_code=401, content={"detail": "Unauthorized: No token found"})
