from fastapi import APIRouter, Depends, HTTPException, Request
from kombu.transport.virtual import binding_key_t

import crud
import schemas, tasks
from sqlalchemy.orm import Session
from database import SessionLocal

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

router = APIRouter(
    prefix='/manage',
    tags=['management']
)

def require_admin(
    request: Request,
    db: Session = Depends(get_db)
):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    is_admin = crud.is_user_admin(db, user_id)
    if not is_admin or not is_admin.active:
        raise HTTPException(status_code=403, detail="Admin access only")

    return user_id

@router.post('/add_bakery', response_model=schemas.AddBakeryResult)
async def add_bakery(bakery: schemas.AddBakery, db: Session = Depends(get_db), _:int = Depends(require_admin)):
    try:
        bakery = crud.add_bakery(db, bakery)
        return bakery
    except Exception as e:
        db.rollback()
        return {'status': 'NOK', 'msg': f'error: {type(e)}: {str(e)}'}


@router.post('/add_bread', response_model=schemas.AddBreadResult)
async def add_bread(bread: schemas.AddBread, db: Session = Depends(get_db), _:int = Depends(require_admin)):
    try:
        bread_id = crud.add_bread(db, bread)
        return bread_id
    except Exception as e:
        db.rollback()
        return {'status': 'NOK', 'msg': f'error: {type(e)}: {str(e)}'}


@router.post('/bakery_bread')
async def bakery_bread(data: schemas.Initialize, _: int = Depends(require_admin)):
    tasks.initialize.delay(data.bakery_id, data.bread_type_and_cook_time)
    return {'status': 'Processing'}

