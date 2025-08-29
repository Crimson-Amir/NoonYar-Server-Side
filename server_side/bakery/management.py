from fastapi import APIRouter, Depends, HTTPException, Request
import crud
import schemas
from sqlalchemy.orm import Session
from helpers import endpoint_helper, redis_helper

handle_errors = endpoint_helper.handle_endpoint_errors("bakery:management")

router = APIRouter(
    prefix='/manage',
    tags=['management']
)

def require_admin(
    request: Request,
    db: Session = Depends(endpoint_helper.get_db)
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
@handle_errors
async def add_bakery(bakery: schemas.AddBakery, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bakery = crud.add_bakery(db, bakery)
    return bakery


@router.post('/add_bread', response_model=schemas.BreadID)
@handle_errors
async def add_bread(request: Request, bread: schemas.AddBread, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bread_id = crud.add_bread(db, bread)
    await redis_helper.reset_bread_names(request.app.state.redis)
    return bread_id


@router.put('/change_bread_names')
@handle_errors
async def change_bread_names(
        request: Request,
        data: schemas.ChangeBreadName,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    crud.edit_bread_names(db, data.bread_id_and_names)
    await redis_helper.reset_bread_names(request.app.state.redis)
    return {'status': 'successfully updated'}



@router.post('/bakery_bread')
@handle_errors
async def bakery_bread(
        request: Request,
        data: schemas.Initialize,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    crud.delete_all_corresponding_bakery_bread(db, data.bakery_id)
    crud.add_bakery_bread_entries(db, data.bakery_id, data.bread_type_id_and_cook_time)
    db.commit()
    await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    return {'status': 'successfully updated'}


@router.put('/add_single_bread_to_bakery')
@handle_errors
async def add_single_bread_to_bakery(
    request: Request,
    data: schemas.AddSingleBreadToBakery,
    db: Session = Depends(endpoint_helper.get_db),
    _: int = Depends(require_admin)
):
    crud.add_single_bread_to_bakery(db, data.bakery_id, data.bread_id, data.cook_time_s)
    await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    return {'status': 'successfully added'}


@router.delete('/remove_single_bread_from_bakery/{bakery_id}/{bread_id}')
@handle_errors
async def remove_single_bread_from_bakery(
    request: Request,
    bakery_id: int,
    bread_id: int,
    db: Session = Depends(endpoint_helper.get_db),
    _: int = Depends(require_admin)
):
    remove_entry = crud.remove_single_bread_from_bakery(db, bakery_id, bread_id)
    await redis_helper.reset_bakery_metadata(request.app.state.redis, bakery_id)
    if remove_entry:
        return {'status': 'Successfully deleted'}
    return {'status': 'No entry found'}