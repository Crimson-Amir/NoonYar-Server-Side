from fastapi import APIRouter, Depends, HTTPException, Request
from application import crud, schemas
from sqlalchemy.orm import Session
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

    return user_id

@router.post('/new', response_model=schemas.NewAdminResult)
@handle_errors
async def new_admin(admin: schemas.NewAdminRequirement, db: Session = Depends(endpoint_helper.get_db), _: int = Depends(require_admin)):
    new = crud.register_new_admin(db, admin.user_id, admin.status)
    logger.info(f"{FILE_NAME}:new_admin", extra={"user_id": admin.user_id, "status": admin.status})
    return new

@router.delete('/remove/{admin_id}')
@handle_errors
async def remove_admin(admin_id: int, db: Session = Depends(endpoint_helper.get_db), _: int = Depends(require_admin)):
    result = crud.remove_admin(db, admin_id)
    if result:
        logger.info(f"{FILE_NAME}:remove_admin", extra={"admin_id": admin_id})
        return {"status": "admin removed!"}

    raise HTTPException(
        status_code=404,
        detail="Admin ID does not exist"
    )