from fastapi import APIRouter, Depends, HTTPException, Request
from kombu.transport.virtual import binding_key_t
import crud, algorithm
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
        raise HTTPException(
            status_code=500,
            detail=f'Error: {type(e).__name__}: {str(e)}'
        )


@router.post('/add_bread', response_model=schemas.BreadID)
async def add_bread(bread: schemas.AddBread, db: Session = Depends(get_db), _:int = Depends(require_admin)):
    try:
        bread_id = crud.add_bread(db, bread)
        return bread_id
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f'Error: {type(e).__name__}: {str(e)}'
        )


@router.post('/bakery_bread')
async def bakery_bread(
        request: Request,
        data: schemas.Initialize,
        db: Session = Depends(get_db),
        _: int = Depends(require_admin)
):
    crud.delete_all_corresponding_bakery_bread(db, data.bakery_id)
    crud.add_bakery_bread_entries(db, data.bakery_id, data.bread_type_id_and_cook_time)
    db.commit()
    await algorithm.reset_time_per_bread(request.app.state.redis, data.bakery_id)
    return {'status': 'successfully updated'}


@router.put('/add_single_bread_to_bakery')
async def add_single_bread_to_bakery(
    request: Request,
    data: schemas.AddSingleBreadToBakery,
    db: Session = Depends(get_db),
    _: int = Depends(require_admin)
):
    try:
        crud.add_single_bread_to_bakery(db, data.bakery_id, data.bread_id, data.cook_time_s)
        await algorithm.reset_time_per_bread(request.app.state.redis, data.bakery_id)
        return {'status': 'successfully added'}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f'Error: {type(e).__name__}: {str(e)}'
        )


@router.delete('/remove_single_bread_from_bakery/{bakery_id}/{bread_id}')
async def remove_single_bread_from_bakery(
    request: Request,
    bakery_id: int,
    bread_id: int,
    db: Session = Depends(get_db),
    _: int = Depends(require_admin)
):
    try:
        remove_entry = crud.remove_single_bread_from_bakery(db, bakery_id, bread_id)
        await algorithm.reset_time_per_bread(request.app.state.redis, bakery_id)
        if remove_entry:
            return {'status': 'Successfully deleted'}
        return {'status': 'No entry found'}

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f'Error: {type(e).__name__}: {str(e)}'
        )