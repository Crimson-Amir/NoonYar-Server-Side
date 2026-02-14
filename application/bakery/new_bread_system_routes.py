"""
New Bread System API Endpoints
These endpoints implement the new bakery queue algorithm with:
- Parity-based slot assignment
- State tracking (WAITING, BAKING, READY, DELIVERED)
- Urgent bread injection
- Edit/Cancel functionality
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Dict, List
from pydantic import BaseModel

from application.helpers import endpoint_helper, redis_helper, token_helpers
from application.bread_system_integration import (
    get_bread_system_integration,
    new_ticket_with_new_system,
    inject_urgent_with_new_system,
    edit_ticket_with_new_system,
    cancel_ticket_with_new_system,
    put_bread_in_oven_with_new_system,
    deliver_ticket_with_new_system,
    get_baker_status_with_new_system,
    get_dashboard_with_new_system,
)
from application import crud, tasks, schemas, mqtt_client
from application.database import SessionLocal
from application.logger_config import logger


FILE_NAME = "bakery:new_bread_system"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/bread-system',
    tags=['new_bread_system']
)


# --- Request/Response Models ---


class NewTicketRequest(BaseModel):
    bakery_id: int
    bread_requirements: Dict[str, int]


class TicketNumberRequest(BaseModel):
    bakery_id: int
    ticket_number: int


class EditTicketRequest(BaseModel):
    bakery_id: int
    ticket_number: int
    new_bread_requirements: Dict[str, int]


class UrgentBreadRequest(BaseModel):
    bakery_id: int
    ticket_number: int
    bread_requirements: Dict[str, int]


class PutBreadRequest(BaseModel):
    bakery_id: int
    ticket_number: int = None


# --- Endpoints ---


@router.post('/new_ticket')
@handle_errors
async def new_ticket_endpoint(
    request: Request,
    payload: NewTicketRequest,
):
    """
    Create a new ticket using the new bread system algorithm.
    Uses parity-based slot assignment for singles and multis.
    """
    bakery_id = payload.bakery_id
    bread_requirements = payload.bread_requirements

    r = request.app.state.redis

    # Validate bread requirements
    if any(int(v) < 0 for v in bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(int(v) for v in bread_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Ticket should have at least one bread")

    # Get bakery time per bread for validation
    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail="This bakery does not have any bread configured")

    if set(time_per_bread.keys()) != set(bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="Invalid bread types")

    # Create ticket using new system
    ticket_number, message = await new_ticket_with_new_system(
        r, bakery_id, bread_requirements, time_per_bread
    )

    if ticket_number is None:
        raise HTTPException(status_code=400, detail=message)

    # Also add to existing Redis/DB system for compatibility
    customer_token = f"T{ticket_number:04d}"
    success = await redis_helper.add_customer_to_reservation_dict(
        r, bakery_id, ticket_number, bread_requirements, time_per_bread
    )

    if not success:
        # Rollback new system ticket if Redis add fails
        integration = await get_bread_system_integration(bakery_id)
        await integration.cancel_ticket(r, ticket_number)
        raise HTTPException(status_code=400, detail=f"Ticket {ticket_number} already exists in Redis")

    # Register in database
    tasks.register_new_customer.delay(ticket_number, bakery_id, bread_requirements, False, customer_token)

    # Notify via MQTT
    await mqtt_client.update_has_customer_in_queue(request, bakery_id)
    await mqtt_client.notify_new_ticket(request, bakery_id, ticket_number, customer_token)

    logger.info(f"{FILE_NAME}:new_ticket", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
        "bread_requirements": bread_requirements,
    })

    return {
        "status": "success",
        "ticket_number": ticket_number,
        "message": message,
        "token": customer_token,
    }


@router.post('/inject_urgent')
@handle_errors
async def inject_urgent_endpoint(
    request: Request,
    payload: UrgentBreadRequest,
):
    """
    Inject urgent bread for an existing ticket.
    Urgent orders take priority over normal queue.
    """
    bakery_id = payload.bakery_id
    ticket_number = payload.ticket_number
    bread_requirements = payload.bread_requirements

    r = request.app.state.redis

    # Validate
    if any(int(v) < 0 for v in bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(int(v) for v in bread_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Urgent item should have at least one bread")

    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail="This bakery does not have any bread configured")

    if set(time_per_bread.keys()) != set(bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="Invalid bread types")

    # Inject urgent bread
    success, message = await inject_urgent_with_new_system(
        r, bakery_id, ticket_number, bread_requirements, time_per_bread
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Also update existing urgent system for compatibility
    urgent_id = await redis_helper.create_urgent_item(
        r, bakery_id, ticket_number, bread_requirements, time_per_bread
    )

    tasks.log_urgent_inject.delay(bakery_id, urgent_id, ticket_number, bread_requirements)

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:inject_urgent", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
        "urgent_id": urgent_id,
        "bread_requirements": bread_requirements,
    })

    return {
        "status": "success",
        "message": message,
        "urgent_id": urgent_id,
    }


@router.put('/edit_ticket')
@handle_errors
async def edit_ticket_endpoint(
    request: Request,
    payload: EditTicketRequest,
):
    """
    Edit a ticket that is still in the normal queue.
    Cannot edit tickets that are being processed or finished.
    Single tickets cannot be upgraded to multi-bread.
    """
    bakery_id = payload.bakery_id
    ticket_number = payload.ticket_number
    new_requirements = payload.new_bread_requirements

    r = request.app.state.redis

    # Validate
    if any(int(v) < 0 for v in new_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(int(v) for v in new_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Ticket should have at least one bread")

    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail="This bakery does not have any bread configured")

    # Edit ticket
    success, message = await edit_ticket_with_new_system(
        r, bakery_id, ticket_number, new_requirements, time_per_bread
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Update Redis for compatibility
    bread_ids = list(time_per_bread.keys())
    encoded_reservation = ",".join(str(int(new_requirements.get(bid, 0))) for bid in bread_ids)

    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    ttl = redis_helper.seconds_until_midnight_iran()
    await r.hset(res_key, str(ticket_number), encoded_reservation)

    # Update database
    with SessionLocal() as db:
        crud.update_customer_breads_for_ticket_today(db, bakery_id, ticket_number, new_requirements)

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:edit_ticket", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
        "new_requirements": new_requirements,
    })

    return {
        "status": "success",
        "message": message,
    }


@router.delete('/cancel_ticket')
@handle_errors
async def cancel_ticket_endpoint(
    request: Request,
    payload: TicketNumberRequest,
):
    """
    Cancel a ticket and burn its slot.
    Cannot cancel tickets that are being processed or finished.
    """
    bakery_id = payload.bakery_id
    ticket_number = payload.ticket_number

    r = request.app.state.redis

    # Cancel ticket
    success, message = await cancel_ticket_with_new_system(r, bakery_id, ticket_number)

    if not success:
        raise HTTPException(status_code=400, detail=message)

    # Remove from Redis for compatibility
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    pipe = r.pipeline()
    pipe.hdel(res_key, str(ticket_number))
    pipe.zrem(order_key, str(ticket_number))
    await pipe.execute()

    # Update database
    with SessionLocal() as db:
        crud.delete_customer_by_ticket_id_today(db, bakery_id, ticket_number)

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:cancel_ticket", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
    })

    return {
        "status": "success",
        "message": message,
    }


@router.post('/put_bread_in_oven')
@handle_errors
async def put_bread_in_oven_endpoint(
    request: Request,
    payload: PutBreadRequest,
):
    """
    Put the next waiting bread into the oven.
    If ticket_number is provided, it will prioritize that ticket.
    Otherwise, follows the current baker task assignment.
    """
    bakery_id = payload.bakery_id
    ticket_number = payload.ticket_number

    r = request.app.state.redis

    # Put bread in oven
    result = await put_bread_in_oven_with_new_system(r, bakery_id, ticket_number)

    # Start baking timer
    if result.get("current_ticket"):
        ticket_num = result["current_ticket"]
        # The timer is started in the integration function
        tasks.start_bread_baking_timer.delay(bakery_id, ticket_num, 0)

    logger.info(f"{FILE_NAME}:put_bread_in_oven", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
        "result": result,
    })

    return result


@router.post('/deliver_ticket')
@handle_errors
async def deliver_ticket_endpoint(
    request: Request,
    payload: TicketNumberRequest,
):
    """
    Deliver a ticket when all its breads are ready.
    """
    bakery_id = payload.bakery_id
    ticket_number = payload.ticket_number

    r = request.app.state.redis

    # Deliver ticket
    result = await deliver_ticket_with_new_system(r, bakery_id, ticket_number)

    if "error" in result.get("message", "").lower():
        raise HTTPException(status_code=400, detail=result["message"])

    # Update database
    with SessionLocal() as db:
        crud.consume_breads_for_customer_today(db, bakery_id, ticket_number)

    # Update Redis
    await redis_helper.add_served_ticket(r, bakery_id, ticket_number)

    logger.info(f"{FILE_NAME}:deliver_ticket", extra={
        "bakery_id": bakery_id,
        "ticket_number": ticket_number,
        "result": result,
    })

    return result


@router.get('/baker_status/{bakery_id}')
@handle_errors
async def baker_status_endpoint(
    request: Request,
    bakery_id: int,
):
    """
    Get current baker status and active task.
    """
    r = request.app.state.redis

    status = await get_baker_status_with_new_system(r, bakery_id)

    return status


@router.get('/dashboard/{bakery_id}')
@handle_errors
async def dashboard_endpoint(
    request: Request,
    bakery_id: int,
):
    """
    Get full dashboard with all tickets, queues, and system status.
    """
    r = request.app.state.redis

    dashboard = await get_dashboard_with_new_system(r, bakery_id)

    return dashboard


@router.get('/ticket_status/{bakery_id}/{ticket_number}')
@handle_errors
async def ticket_status_endpoint(
    request: Request,
    bakery_id: int,
    ticket_number: int,
):
    """
    Get detailed status for a specific ticket.
    """
    r = request.app.state.redis

    integration = await get_bread_system_integration(bakery_id)
    status = await integration.get_ticket_status(r, ticket_number)

    return status
