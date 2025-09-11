from fastapi import Request, Depends, Response, APIRouter, HTTPException, Cookie
from fastapi.responses import RedirectResponse
import schemas, crud, tasks, private
from sqlalchemy.orm import Session
from auth import create_access_token, create_refresh_token, hash_password_md5, decode_token
from database import SessionLocal
from datetime import timedelta
from logger_config import logger
import random, time
from helpers import token_helpers, endpoint_helper

FILE_NAME = "user:authentication.py"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)


router = APIRouter(
    prefix='/auth',
    tags=['authentication']
)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

@router.post("/verify-signup-otp")
@handle_errors
async def verify_signup_otp(request: Request, response: Response, data: schemas.LogInValue):
    otp_store = token_helpers.OTPStore(request.app.state.redis)
    if not await otp_store.verify_otp(data.phone_number, data.code):
        raise HTTPException(status_code=400, detail="OTP not found or incorrect")

    token = create_access_token(
        data={"phone_number": data.phone_number, "purpose": "signup"},
        expires_delta=timedelta(minutes=private.SIGN_UP_TEMPORARY_TOKEN_EXP_MIN)
    )

    token_helpers.set_cookie(response, "temporary_sign_up_token", token, private.SIGN_UP_TEMPORARY_TOKEN_EXP_MIN * 60)
    logger.info(f"{FILE_NAME}:verify-signup-otp", extra={"phone_number": data.phone_number, "code": data.code})
    return {"status": "OK"}

@router.post('/sign-up/')
@handle_errors
async def create_user(user: schemas.SignUpRequirement, request: Request, response: Response, temporary_sign_up_token: str = Cookie(None), db: Session = Depends(get_db)):
    if not temporary_sign_up_token:
        raise HTTPException(status_code=400, detail="No token found")

    payload = decode_token(temporary_sign_up_token)

    if payload.get("purpose") != "signup":
        raise HTTPException(status_code=400, detail="Invalid token purpose")

    phone_number = payload.get("phone_number")
    if not phone_number or phone_number != user.phone_number:
        raise HTTPException(status_code=400, detail=f"Invalid token")

    db_user = crud.get_user_by_phone_number(db, phone_number)
    if db_user:
        raise HTTPException(status_code=400, detail="this user already exists!")

    create_user_db = crud.create_user(db, user)

    user_data = {
        "first_name": create_user_db.first_name,
        "user_id": create_user_db.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)

    token_helpers.set_cookie(response, "access_token", access_token, private.ACCESS_TOKEN_EXP_MIN * 60)
    token_helpers.set_cookie(response, "refresh_token", cr_refresh_token, private.REFRESH_TOKEN_EXP_MIN * 60)

    response.delete_cookie("temporary_sign_up_token")

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    extra = {"phone_number": user.phone_number, "first_name": user.first_name,
              'last_name': user.last_name, 'password': user.password,
              "ip_address": client_ip, "user_agent": user_agent}

    logger.info(f"{FILE_NAME}:create_user", extra=extra)
    msg = "👤 New User Registered!\n"

    for key, value in extra.items():
        msg += f"\n{key}: {value}"

    tasks.report_to_admin_api.delay(msg, message_thread_id=private.NEW_USER_THREAD_ID)

    return {'msg': 'user created', 'user_id': create_user_db.user_id}


def generate_otp():
    return random.randint(1000, 9999)

@router.post('/enter-number')
@handle_errors
async def enter_number(user: schemas.LogInRequirement, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_phone_number(db, user.phone_number)
    code = str(generate_otp())
    next_step = "login"

    if not db_user:
        next_step = "sign-up"

    # request_hashed_password = hash_password_md5(user.password)
    # if request_hashed_password != db_user.hashed_password:
        # raise HTTPException(status_code=400, detail='password is not correct')

    task = tasks.send_otp.delay(user.phone_number, code)
    # TODO: REMOVE THIS IN PRODACTION
    tasks.report_to_admin_api.delay(f"OTP CODE: {code}")
    logger.info(f"{FILE_NAME}:enter_number", extra={"phone_number": user.phone_number})
    return {'status': 'OK', 'message': 'OTP sent', 'next_step': next_step ,'task_id': task.id}

@router.post('/verify-login')
@handle_errors
async def verify_login(request: Request, response: Response, data: schemas.LogInValue, db: Session = Depends(get_db)):
    otp_store = token_helpers.OTPStore(request.app.state.redis)

    if not await otp_store.verify_otp(data.phone_number, data.code):
        raise HTTPException(status_code=400, detail="OTP not found or incorrect")

    db_user = crud.get_user_by_phone_number(db, data.phone_number)

    if not db_user:
        raise HTTPException(status_code=401, detail="User does not exists")

    user_data = {
        "first_name": db_user.first_name,
        "user_id": db_user.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)

    token_helpers.set_cookie(response, "access_token", access_token, private.ACCESS_TOKEN_EXP_MIN * 60)
    token_helpers.set_cookie(response, "refresh_token", cr_refresh_token, private.REFRESH_TOKEN_EXP_MIN * 60)
    logger.info(f"{FILE_NAME}:verify_login", extra={"phone_number": data.phone_number, "code": data.code})

    return {'status': 'OK', 'user_id': db_user.user_id}

@router.post('/logout')
@handle_errors
async def logout(request: Request):
    redirect = RedirectResponse('/home/', status_code=303)
    blacklist = token_helpers.TokenBlacklist(request.app.state.redis)

    # Access token
    access_token = request.cookies.get("access_token")
    if access_token:
        payload = decode_token(access_token)
        exp = payload.get("exp")
        ttl = max(1, exp - int(time.time())) if exp else private.ACCESS_TOKEN_EXP_MIN * 60
        await blacklist.add(access_token, ttl)

    # Refresh token
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        payload = decode_token(refresh_token, private.REFRESH_SECRET_KEY)
        exp = payload.get("exp")
        ttl = max(1, exp - int(time.time())) if exp else private.REFRESH_TOKEN_EXP_MIN * 60
        await blacklist.add(refresh_token, ttl)

    # Clear cookies
    redirect.delete_cookie(key='access_token', httponly=True, samesite="lax")
    redirect.delete_cookie(key="refresh_token", httponly=True, samesite="lax")
    logger.info(f"{FILE_NAME}:logout")

    request.state.user = None
    return redirect