from fastapi import APIRouter, Depends, HTTPException, Request
from application import crud, schemas
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from application.helpers import endpoint_helper
from application.logger_config import logger

FILE_NAME = 'admin:manage'
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/admin',
    tags=['hardware_communication']
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
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access only")

    return is_admin

@router.post('/new', response_model=schemas.NewAdminResult)
@handle_errors
async def new_admin(admin: schemas.NewAdminRequirement, db: Session = Depends(endpoint_helper.get_db), is_admin= Depends(require_admin)):
    try:
        new = crud.register_new_admin(db, admin.user_id, admin.status)
    except IntegrityError:
        # Likely foreign key violation: user_id does not exist in user_detail
        db.rollback()
        raise HTTPException(status_code=404, detail="User ID does not exist")

    logger.info(f"{FILE_NAME}:new_admin", extra={"user_id": admin.user_id, "status": admin.status, "add_by_admin_id": is_admin.admin_id})
    return new

@router.delete('/remove/{admin_id}')
@handle_errors
async def remove_admin(admin_id: int, db: Session = Depends(endpoint_helper.get_db), is_admin = Depends(require_admin)):
    result = crud.remove_admin(db, admin_id)
    if result:
        logger.info(f"{FILE_NAME}:remove_admin", extra={"admin_id": admin_id, "removed_by_admin_id": is_admin.admin_id})
        return {"status": "admin removed!"}

    raise HTTPException(
        status_code=404,
        detail="Admin ID does not exist"
    )