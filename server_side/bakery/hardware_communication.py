from fastapi import APIRouter, HTTPException, Header, Request, Depends
import crud, algorithm
from utilities import verify_bakery_token
import schemas, tasks, algorithm
from database import SessionLocal

router = APIRouter(
    prefix='/hc',
    tags=['hardware_communication']
)

def validate_token(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid or missing Authorization header")
    return authorization[len("Bearer "):]

@router.put('/nc')
async def new_customer(
    request: Request,
    customer: schemas.NewCustomerRequirement,
    token: str = Depends(validate_token)
):
    if not verify_bakery_token(token, customer.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid bakery token")

    breads_type = await algorithm.get_bakery_time_per_bread(request.app.state.redis, customer.bakery_id)
    if breads_type.keys() != customer.bread_requirements.keys():
        raise HTTPException(status_code=400, detail="Invalid bread types")

    reservation_dict = await algorithm.get_bakery_reservations(request.app.state.redis, customer.bakery_id)
    customer_ticket_id = algorithm.Algorithm.new_reservation(reservation_dict, customer.bread_requirements.values())

    await algorithm.add_customer_to_reservation_dict(request.app.state.redis, customer.bakery_id, customer_ticket_id, customer.bread_requirements)
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, customer.bread_requirements)
    return {'status': 'successful', 'customer_ticket_id': customer_ticket_id}


@router.post('/nt')
async def next_ticket(
    ticket: schemas.NextTicketRequirement,
    token: str = Depends(validate_token)
):

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

