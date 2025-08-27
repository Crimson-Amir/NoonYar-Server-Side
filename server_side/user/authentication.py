from fastapi import Request, Depends, Response, APIRouter, HTTPException, Cookie
from fastapi.responses import RedirectResponse
import schemas, crud, tasks, private
from sqlalchemy.orm import Session
from auth import create_access_token, create_refresh_token, hash_password_md5, decode_token
from database import SessionLocal
from datetime import timedelta
import random, time
from helpers.token_helpers import TokenBlacklist, set_cookie, OTPStore, hash_otp

router = APIRouter(
    prefix='/auth',
    tags=['authentication']
)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

async def otp_verification(db, phone_number, user_code):
    otp = crud.get_otp(db, phone_number)
    if not otp:
        raise HTTPException(status_code=400, detail='OTP not found or expired')
    if otp.hashed_code != hash_otp(user_code):
        raise HTTPException(status_code=400, detail='OTP is not correct')
    return otp

@router.post("/verify-signup-otp")
async def verify_signup_otp(request: Request, response: Response, data: schemas.LogInValue):
    otp_store = OTPStore(request.app.state.redis)
    if not await otp_store.verify_otp(data.phone_number, data.code):
        raise HTTPException(status_code=400, detail="OTP not found or incorrect")

    token = create_access_token(
        data={"phone_number": data.phone_number, "purpose": "signup"},
        expires_delta=timedelta(minutes=private.SIGN_UP_TEMPORARY_TOKEN_EXP_MIN)
    )

    set_cookie(response, "temporary_sign_up_token", token, private.SIGN_UP_TEMPORARY_TOKEN_EXP_MIN * 60)
    return {"status": "OK"}

@router.post('/sign-up/')
async def create_user(user: schemas.SignUpRequirement, response: Response, temporary_sign_up_token: str = Cookie(None), db: Session = Depends(get_db)):
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

    set_cookie(response, "access_token", access_token, private.ACCESS_TOKEN_EXP_MIN * 60)
    set_cookie(response, "refresh_token", cr_refresh_token, private.REFRESH_TOKEN_EXP_MIN * 60)

    response.delete_cookie("temporary_sign_up_token")

    return {'msg': 'user created', 'user_id': create_user_db.user_id}


def generate_otp():
    return random.randint(1000, 9999)

@router.post('/enter-number')
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
    return {'status': 'OK', 'message': 'OTP sent', 'next_step': next_step ,'task_id': task.id}

@router.post('/verify-login')
async def verify_login(request: Request, response: Response, data: schemas.LogInValue, db: Session = Depends(get_db)):
    otp_store = OTPStore(request.app.state.redis)

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

    set_cookie(response, "access_token", access_token, private.ACCESS_TOKEN_EXP_MIN * 60)
    set_cookie(response, "refresh_token", cr_refresh_token, private.REFRESH_TOKEN_EXP_MIN * 60)

    return {'status': 'OK', 'user_id': db_user.user_id}

@router.post('/logout')
async def logout(request: Request):
    redirect = RedirectResponse('/home/', status_code=303)
    blacklist = TokenBlacklist(request.app.state.redis)

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

    request.state.user = None
    return redirect