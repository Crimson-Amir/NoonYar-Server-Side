from fastapi import APIRouter, Depends, HTTPException, Request
from application.logger_config import logger
from sqlalchemy.orm import Session
from application.helpers import database_helper, endpoint_helper, redis_helper
from application import mqtt_client, crud, schemas
from sqlalchemy.exc import IntegrityError

FILE_NAME = "bakery:management"
handle_errors = endpoint_helper.db_transaction(FILE_NAME)

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
    logger.info(f"{FILE_NAME}:add_bakery", extra={"bakery_name": bakery.name, "location": bakery.location})
    return bakery

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
    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    await mqtt_client.update_time_per_bread(request, data.bakery_id, new_config)
    logger.info(f"{FILE_NAME}:bakery_bread", extra={"bakery_id": data.bakery_id, "bread_type_id_and_cook_time": data.bread_type_id_and_cook_time})
    return {'status': 'successfully updated'}


@router.put('/update_bakery_single_bread')
@handle_errors
async def update_bakery_single_bread(
    request: Request,
    data: schemas.ModifySingleBreadToBakery,
    db: Session = Depends(endpoint_helper.get_db),
    _: int = Depends(require_admin)
):
    try:
        crud.add_single_bread_to_bakery(db, data.bakery_id, data.bread_id, data.cook_time_s)
        state = 'add'
    except IntegrityError:
        db.rollback()
        crud.update_bread_bakery(db, data.bakery_id, data.bread_id, data.cook_time_s)
        state = 'update'

    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    await mqtt_client.update_time_per_bread(request, data.bakery_id, new_config)
    logger.info(f"{FILE_NAME}:add_single_bread_to_bakery", extra={"bread_id": data.bread_id, "cook_time_s": data.cook_time_s})

    return {'status': 'successful', 'state': state}


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
    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, bakery_id)
    if remove_entry:
        await mqtt_client.update_time_per_bread(request, bakery_id, new_config)
        logger.info(f"{FILE_NAME}:remove_single_bread_from_bakery", extra={"bakery_id": bakery_id, "bread_id": bread_id})
        return {'status': 'Successfully deleted'}
    return {'status': 'No entry found'}


@router.post('/add_bread', response_model=schemas.BreadID)
@handle_errors
async def add_bread(request: Request, bread: schemas.AddBread, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bread_id = crud.add_bread(db, bread)
    await redis_helper.reset_bread_names(request.app.state.redis)
    logger.info(f"{FILE_NAME}:add_bread", extra={"read_name": bread.name})
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
    logger.info(f"{FILE_NAME}:change_bread_names", extra={"bread_id_and_names": data.bread_id_and_names})
    return {'status': 'successfully updated'}


@router.post('/upcoming/add')
@handle_errors
async def add_notify_bakery_bread(
        request: Request,
        data: schemas.ModifyBakeryBreadNotify,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    try:
        entry = crud.add_upcoming_bread_to_bakery(db, data.bakery_id, data.bread_id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail='Bread-notify already exists')
    except database_helper.BreadDoesNotExist as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    logger.info(f"{FILE_NAME}:add_upcoming_bread", extra={"bakery_id": data.bakery_id, "bread_id": data.bread_id})
    await redis_helper.add_upcoming_bread_to_bakery(r, data.bakery_id, data.bread_id)
    return {'status': 'successfully added'}

@router.delete('/upcoming/remove/{bakery_id}/{bread_id}')
@handle_errors
async def remove_notify_bakery_bread(
        request: Request,
        bakery_id: int,
        bread_id: int,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    removed = crud.remove_upcoming_bread_from_bakery(db, bakery_id, bread_id)
    logger.info(f"{FILE_NAME}:remove_upcoming_bread", extra={"bakery_id": bakery_id, "bread_id": bread_id, "removed": removed})
    if removed:
        await redis_helper.remove_upcoming_bread_from_bakery(r, bakery_id, bread_id)
    return {"status": "removed" if removed else "not_found"}


@router.get('/upcoming/list/{bakery_id}')
@handle_errors
async def list_notify_bakery_bread(
        request: Request,
        bakery_id: int,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    bread_ids = await redis_helper.get_bakery_upcoming_breads(r, bakery_id)
    return {'bread_ids': bread_ids}

