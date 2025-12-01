from fastapi import APIRouter, HTTPException, Depends, Form
from sqlalchemy.orm import Session
from application import crud, schemas
from application.helpers import endpoint_helper

FILE_NAME = 'admin:init'
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/admin',
    tags=['admin init']
)

@router.post("/init")
@handle_errors
async def init_admin(
    admin: schemas.SignUpRequirement,
    db: Session = Depends(endpoint_helper.get_db)
):
    existing_admin = crud.get_first_admin(db)
    if existing_admin:
        raise HTTPException(status_code=400, detail="Admin already exists")
    user = crud.create_user(db, admin)
    admin = crud.register_new_admin(db, user.user_id, True)

    logger.info(f"{FILE_NAME}:init_admin")
    msg = "👤 Admin Init Succesful!\n\nphone number: {admin.phone_number}"

    tasks.report_to_admin_api.delay(msg, message_thread_id=settings.inf)

    return {"message": "Admin initialized successfully", "admin_id": admin.admin_id}
