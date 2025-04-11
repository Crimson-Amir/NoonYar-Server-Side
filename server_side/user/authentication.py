
from fastapi import Request, Depends, Response, APIRouter, HTTPException
from fastapi.responses import RedirectResponse
import schemas, crud
from sqlalchemy.orm import Session
from auth import create_access_token, create_refresh_token, hash_password_md5
from database import SessionLocal

router = APIRouter(
    prefix='/auth',
    tags=['authentication']
)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class TokenBlackList:
    def __init__(self): self.black_list = set()
    def add(self, token): self.black_list.add(token)
    def is_blacklisted(self, token): return token in self.black_list

token_black_list = TokenBlackList()


@router.post('/sign-up/', response_model=schemas.SignUpReturn)
async def create_user(user: schemas.SignUpRequirement, response: Response, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_phone_number(db, user.phone_number)
    if db_user:
        raise HTTPException(status_code=400, detail="this email already exists!")

    create_user_db = crud.create_user(db, user)

    user_data = {
        "first_name": create_user_db.first_name,
        "user_id": create_user_db.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=3600
    )
    response.set_cookie(
        key="refresh_token",
        value=cr_refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=2_592_000
    )

    return create_user_db


@router.post('/login')
async def login(response: Response, user: schemas.LogInRequirement = None, db: Session = Depends(get_db)):

    db_user = crud.get_user_by_phone_number(db, user.phone_number)

    if not db_user:
        raise HTTPException(status_code=400, detail='phone number does not exist')

    request_hashed_password = hash_password_md5(user.password)
    if request_hashed_password != db_user.hashed_password:
        raise HTTPException(status_code=400, detail='password is not correct')

    user_data = {
        "first_name": db_user.first_name,
        "user_id": db_user.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=3600
    )
    response.set_cookie(
        key="refresh_token",
        value=cr_refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=2_592_000
    )

    return {'status': 'OK', 'user_id': db_user.user_id}


@router.post('/logout')
async def logout(request: Request):
    token = request.cookies.get('access_token')
    redirect = RedirectResponse('/home/')
    if token:
        token_black_list.add(token)
        redirect.delete_cookie(key='access_token', httponly=True, samesite="lax")
        redirect.delete_cookie(key="refresh_token", httponly=True, samesite="lax")
    request.state.user = None
    return redirect

