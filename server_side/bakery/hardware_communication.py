from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
import crud, algorithm
from utilities import verify_bakery_token
import schemas, tasks, algorithm
from database import SessionLocal

router = APIRouter(
    prefix='/hc',
    tags=['hardware_communication']
)

@router.post('/nc')
async def new_customer(
    request: Request,
    customer: schemas.NewCustomerRequirement,
    authorization: Optional[str] = Header(None)
):
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid or missing Authorization header")

    token = authorization[len("Bearer "):]
    if not verify_bakery_token(token, customer.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    breads_type = algorithm.get_bakery_time_per_bread(request.state.redis, customer.bakery_id)

    if sorted(breads_type.keys()) != sorted(customer.bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="invalid bread types")

    algorithm.add_customer_to_reservation_dict(request.state.redis, customer.bakery_id, customer.bread_requirements)


    tasks.register_new_customer.delay(customer.bakery_id, customer.bread_requirements)
    return {'status': 'Processing'}


@router.post('/nt')
async def next_ticket(
    ticket: schemas.NextTicketRequirement,
    authorization: Optional[str] = Header(None)
):
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid or missing Authorization header")

    token = authorization[len("Bearer "):]
    if not verify_bakery_token(token, ticket.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")
    tasks.next_ticket_process.delay(ticket.current_customer_id, ticket.bakery_id)
    return {'status': 'Processing'}


@router.get('/hardware_init')
async def hardware_initialize(bakery_id: int):
    db = SessionLocal()
    try:
        all_bakery_bread = crud.get_bakery_breads(db, bakery_id)
        bread_time = {}
        for bakery_bread in all_bakery_bread:
            bread_time[bakery_bread.bread_type_id] = bakery_bread.cook_time_s
        return bread_time
    finally:
        db.close()

