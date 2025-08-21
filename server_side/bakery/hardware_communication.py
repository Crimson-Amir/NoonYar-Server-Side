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

    r = request.app.state.redis

    breads_type = await algorithm.get_bakery_time_per_bread(r, customer.bakery_id)
    if breads_type.keys() != customer.bread_requirements.keys():
        raise HTTPException(status_code=400, detail="Invalid bread types")

    reservation_dict = await algorithm.get_bakery_reservations(r, customer.bakery_id)
    customer_ticket_id = algorithm.Algorithm.new_reservation(reservation_dict, customer.bread_requirements.values())

    if await algorithm.customer_exists_in_reservations(r, customer.bakery_id, customer_ticket_id):
        tasks.log_and_report_error(
            "hardware_communication: new_customer",
            ValueError(f"Ticket {customer_ticket_id} already exists"),
            {
                "customer_ticket_id": customer_ticket_id,
                "bakery_id": customer.bakery_id
            }
        )
        raise HTTPException(status_code=400, detail=f"Ticket {customer_ticket_id} already exists")

    await algorithm.add_customer_to_reservation_dict(r, customer.bakery_id, customer_ticket_id, customer.bread_requirements)
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

    r = request.app.state.redis
    current_ticket_id = await algorithm.get_current_ticket_id(r, ticket.bakery_id)

    if current_ticket_id is None:
        raise HTTPException(status_code=404, detail={"error": "No tickets in queue"})

    if current_ticket_id != ticket.customer_ticket_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid ticket number",
                "current_ticket_id": current_ticket_id,
            }
        )
    detail = await algorithm.get_customer_reservation_detail(r, ticket.bakery_id, current_ticket_id)
    await algorithm.remove_customer_from_reservation_dict(r, ticket.bakery_id, current_ticket_id)
    next_ticket_id = await algorithm.get_current_ticket_id(r, ticket.bakery_id) or None
    tasks.next_ticket_process.delay(ticket.customer_ticket_id, ticket.bakery_id)
    return {'status': 'successful', 'next_ticket_id': next_ticket_id, "detail": detail}


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

