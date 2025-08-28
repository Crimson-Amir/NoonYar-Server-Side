from fastapi import APIRouter, HTTPException, Header, Request, Depends
import crud, algorithm
from helpers import token_helpers, redis_helper
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
    if not token_helpers.verify_bakery_token(token, customer.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid bakery token")

    r = request.app.state.redis
    bakery_id = customer.bakery_id

    breads_type, reservation_dict = await redis_helper.fetch_metadata_and_reservations(r, bakery_id)

    if breads_type.keys() != customer.bread_requirements.keys():
        raise HTTPException(status_code=400, detail="Invalid bread types")

    if not reservation_dict:
        reservation_dict = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=breads_type)
        if not reservation_dict:
            raise HTTPException(status_code=404, detail={"error": "reservation not found in list"})

    customer_ticket_id = algorithm.Algorithm.new_reservation(reservation_dict, customer.bread_requirements.values())

    success = await redis_helper.add_customer_to_reservation_dict(
        r, customer.bakery_id, customer_ticket_id, customer.bread_requirements
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Ticket {customer_ticket_id} already exists")

    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, customer.bread_requirements)
    return {'status': 'successful', 'customer_ticket_id': customer_ticket_id}


@router.put('/nt')
async def next_ticket(
        request: Request,
        ticket: schemas.NextTicketRequirement,
        token: str = Depends(validate_token)
):

    if not verify_bakery_token(token, ticket.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    bakery_id = ticket.bakery_id

    current_user_detail, next_ticket_id, next_reservation_detail = (
        await redis_helper.next_ticket_opration(request.app.state.redis, bakery_id, customer_id)
    )
    tasks.next_ticket_process.delay(customer_id, bakery_id)

    return {
        'status': 'successful', "current_user_detail": current_user_detail,
        'next_ticket_id': next_ticket_id, "next_reservation_detail": next_reservation_detail
    }


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

