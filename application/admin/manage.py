from fastapi import APIRouter, Depends, HTTPException, Request
from application import crud, schemas
from sqlalchemy.orm import Session
from application.database import SessionLocal
from application.logger_config import logger

FILE_NAME = 'admin:manage'

router = APIRouter(
    prefix='/admin',
    tags=['hardware_communication']
)
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()


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
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access only")

    return user_id

@router.post('/new', response_model=schemas.NewAdminResult)
async def new_admin(admin: schemas.NewAdminRequirement, db: Session = Depends(get_db), _: int = Depends(require_admin)):
    try:
        new = crud.register_new_admin(db, admin)
        logger.info(f"{FILE_NAME}:new_admin", extra={"user_id": admin.user_id, "status": admin.status})
        return new
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete('/remove/{admin_id}')
async def remove_admin(admin_id: int, db: Session = Depends(get_db), _: int = Depends(require_admin)):
    try:
        result = crud.remove_admin(db, admin_id)
        if result:
            logger.info(f"{FILE_NAME}:remove_admin", extra={"admin_id": admin_id})
            return {"status": "admin removed!"}

        raise HTTPException(
            status_code=404,
            detail="Admin ID does not exist"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )