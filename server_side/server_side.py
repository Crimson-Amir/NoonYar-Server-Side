from datetime import datetime
from fastapi import FastAPI, Request, Depends, HTTPException, Cookie, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import jwt, schemas, crud, pytz, tasks
from database import SessionLocal
from sqlalchemy.orm import Session
from auth import create_access_token, create_refresh_token, hash_password_md5
from private import REFRESH_SECRET_KEY, SECRET_KEY

verification_codes = {}

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount('/statics', StaticFiles(directory='statics'), name='static')

class TokenBlackList:
    def __init__(self): self.black_list = set()
    def add(self, token): self.black_list.add(token)
    def is_blacklisted(self, token): return token in self.black_list

token_black_list = TokenBlackList()

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

async def decode_access_token(request):
    if not request.state.user:
        return

    decode_data = jwt.decode(request.state.user, SECRET_KEY, algorithms=["HS256"])
    return decode_data


@app.post("/refresh-token")
def refresh_token(request: Request, response: Response, refresh_token_attr: str = Cookie(None)):
    if not refresh_token_attr:
        raise HTTPException(status_code=401, detail="Refresh token is missing")
    try:
        payload = jwt.decode(refresh_token_attr, REFRESH_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id"),
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        new_access_token = create_access_token(data={"first_name": payload.get("first_name"), "user_id": payload.get("user_id")})
        request.state.user = payload
        response.set_cookie(key="access_token", value=new_access_token, httponly=True, secure=True, max_age=3600)
        return {"access_token": new_access_token}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    exception_paths = ["/sign-up/"]

    if request.url.path in exception_paths:
        response = await call_next(request)
        return response

    token = request.cookies.get("access_token")
    request.state.user = None

    async def generate_new_token():
        get_refresh_token = request.cookies.get("refresh_token")
        if get_refresh_token:
            try:
                refresh_payload = jwt.decode(get_refresh_token, REFRESH_SECRET_KEY, algorithms=["HS256"])
                new_access_token = create_access_token(data={"first_name": refresh_payload.get("first_name"),
                                                             "user_id": refresh_payload.get("user_id")})
                request.state.user = new_access_token.encode()
                new_response = await call_next(request)
                new_response.set_cookie(key="access_token", value=new_access_token, httponly=True, secure=True, max_age=3600)
                return new_response

            except jwt.ExpiredSignatureError:
                return RedirectResponse('/sign-up/')

            except jwt.InvalidTokenError:
                return RedirectResponse('/sign-up/')

    if token and token not in token_black_list.black_list:
        try:
            jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            request.state.user = token.encode()
        except jwt.ExpiredSignatureError:
            response = await generate_new_token()
            if response: return response
        except jwt.InvalidTokenError:
            return RedirectResponse('/sign-up/')

    else:
        response = await generate_new_token()
        if response: return response

    response = await call_next(request)
    return response

@app.get('/')
async def root(): return RedirectResponse('/home')

@app.api_route('/home', methods=['POST', 'GET'])
async def home(request: Request):
    data = await decode_access_token(request)
    return {'status': 'OK', 'data': data}

@app.post('/sign-up/', response_model=schemas.SignUpReturn)
async def create_user(user: schemas.SignUpRequirement, response: Response, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_phone_number(db, user.phone_number)
    if db_user: return {'status': 'NOK', 'msg': 'this email already exists!'}

    create_user_db = crud.create_user(db, user)

    user_data = {
        "first_name": create_user_db.first_name,
        "user_id": create_user_db.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)

    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=True, max_age=3600)
    response.set_cookie(key="refresh_token", value=cr_refresh_token, httponly=True, secure=True, max_age=2_592_000)

    return create_user_db


@app.post('/login', response_model=schemas.LogInRequirement)
async def login(response: Response, user: schemas.LogInRequirement = None, db: Session = Depends(get_db)):

    db_user = crud.get_user_by_phone_number(db, user.phone_number)
    if not db_user:
        return {'status': 'NOK', 'msg': 'phone number does not exist'}

    request_hashed_password = hash_password_md5(user.password)
    if request_hashed_password != db_user.hashed_password:
        return {'status': 'NOK', 'msg': 'password is not correct'}

    user_data = {
        "first_name": user.first_name,
        "user_id": user.user_id
    }

    access_token = create_access_token(data=user_data)
    cr_refresh_token = create_refresh_token(data=user_data)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=True, max_age=3600)
    response.set_cookie(key="refresh_token", value=cr_refresh_token, httponly=True, secure=True, max_age=2_592_000)

    return db_user

@app.post('/logout')
async def logout(request: Request):
    token = request.cookies.get('access_token')
    redirect = RedirectResponse('/home/')
    if token:
        token_black_list.add(token)
        redirect.delete_cookie(key='access_token', httponly=True)
        redirect.delete_cookie(key="refresh_token", httponly=True)
    return redirect

@app.get("/res/")
async def queue_check(b: int, r: int):
    bakery_id = b
    reservation_number = r
    return {"b": b, "r": r}


@app.post('/nc')
async def new_customer(request: Request, customer: schemas.NewCustomerRequirement, db: Session = Depends(get_db)):
    tasks.register_new_customer.delay(db, customer.customer_id, customer.bakery_id, customer.bread_requirements)
    return {'status': 'Processing'}