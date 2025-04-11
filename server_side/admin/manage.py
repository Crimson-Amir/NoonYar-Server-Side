from fastapi import APIRouter, Depends, HTTPException, Request
import crud
import schemas, tasks
from sqlalchemy.orm import Session
from database import SessionLocal

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
    if not is_admin or not is_admin.active:
        raise HTTPException(status_code=403, detail="Admin access only")

    return user_id

@router.post('/new', response_model=schemas.NewAdminResult)
async def new_admin(admin: schemas.NewAdminRequirement, db: Session = Depends(get_db), _: int = Depends(require_admin)):
    try:
        new = crud.register_new_admin(db, admin)
        return new
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))