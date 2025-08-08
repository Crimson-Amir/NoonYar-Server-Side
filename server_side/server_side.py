from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import jwt
from auth import create_access_token
from private import REFRESH_SECRET_KEY, SECRET_KEY
from user import authentication, user
from bakery import hardware_communication, management
from admin import manage

verification_codes = {}

app = FastAPI()
# templates = Jinja2Templates(directory="templates")
# app.mount('/statics', StaticFiles(directory='statics'), name='static')

app.include_router(authentication.router)
app.include_router(user.router)
app.include_router(hardware_communication.router)
app.include_router(management.router)
app.include_router(manage.router)


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    exception_paths = ["/auth/sign-up", "/auth/enter-number", "/auth/verify-login", "/auth/verify-signup-otp", "/hc", "/docs"]

    if any(request.url.path.startswith(path) for path in exception_paths):
        return await call_next(request)

    request.state.user = None
    access_token = request.cookies.get("access_token")
    refresh_token = request.cookies.get("refresh_token")

    def unauthorized():
        return RedirectResponse("/auth/sign-up")

    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=["HS256"])
            request.state.user = payload
            return await call_next(request)
        except jwt.ExpiredSignatureError:
            pass
        except jwt.InvalidTokenError:
            return unauthorized()

    if refresh_token:
        try:
            refresh_payload = jwt.decode(refresh_token, REFRESH_SECRET_KEY, algorithms=["HS256"])
            new_token = create_access_token({
                "user_id": refresh_payload["user_id"],
                "first_name": refresh_payload["first_name"]
            })

            request.state.user = jwt.decode(new_token, SECRET_KEY, algorithms=["HS256"])
            response = await call_next(request)
            response.set_cookie(
                key="access_token",
                value=new_token,
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=3600
            )
            return response
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return unauthorized()

    return unauthorized()
