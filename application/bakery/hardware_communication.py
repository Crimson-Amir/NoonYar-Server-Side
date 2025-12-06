from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Request, Depends
from application.helpers.general_helpers import seconds_until_midnight_iran, generate_daily_customer_token
from application.helpers import endpoint_helper, redis_helper, token_helpers
from application import tasks, algorithm, mqtt_client, crud, schemas
from application.logger_config import logger
from application.database import SessionLocal

FILE_NAME = "bakery:hardware_communication"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/hc',
    tags=['hardware_communication']
)

def validate_token(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid or missing Authorization header")
    return authorization[len("Bearer "):]

@router.post('/new_ticket')
@handle_errors
async def new_ticket(
        request: Request,
        customer: schemas.NewCustomerRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = customer.bakery_id

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid bakery token")

    r = request.app.state.redis
    bread_requirements = customer.bread_requirements

    bread_count = sum(customer.bread_requirements.values())

    if any(v < 0 for v in customer.bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if bread_count <= 0:
        raise HTTPException(status_code=400, detail="Ticket should have at least one bread")

    breads_type, reservation_dict, upcoming_set = await redis_helper.get_bakery_runtime_state(r, bakery_id)
    if breads_type.keys() != bread_requirements.keys():
        raise HTTPException(status_code=400, detail="Invalid bread types")

    if not reservation_dict:
        reservation_dict = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=breads_type)

    customer_ticket_id = await algorithm.Algorithm.new_reservation(reservation_dict, bread_requirements.values(), r, bakery_id)

    customer_token = generate_daily_customer_token(bakery_id, customer_ticket_id)

    success = await redis_helper.add_customer_to_reservation_dict(
        r, customer.bakery_id, customer_ticket_id, bread_requirements, time_per_bread=breads_type
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Ticket {customer_ticket_id} already exists")

    # customer_in_upcoming_customer = await redis_helper.maybe_add_customer_to_upcoming_zset(
    #     r, customer.bakery_id, customer_ticket_id, bread_requirements, upcoming_members=upcoming_set
    # )
    #
    # if customer_in_upcoming_customer:
    #     await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id)

    customer_in_upcoming_customer = False

    await mqtt_client.update_has_customer_in_queue(request, bakery_id)

    # Check if we should show this customer on display.
    # This consumes the flag so only the *first* ticket after idle
    # returns show_on_display = True.
    show_on_display = await redis_helper.consume_display_flag(r, bakery_id)

    logger.info(f"{FILE_NAME}:new_cusomer", extra={"bakery_id": customer.bakery_id, "bread_requirements": bread_requirements, "customer_in_upcoming_customer": customer_in_upcoming_customer, "show_on_display": show_on_display, "token": customer_token})
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, bread_requirements, customer_in_upcoming_customer, customer_token)

    return {
        'customer_ticket_id': customer_ticket_id,
        'show_on_display': show_on_display,
        'token': customer_token,
    }


@router.put('/serve_ticket')
@handle_errors
async def serve_ticket(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hget(wait_list_key, str(customer_id))
    pipe1.hdel(wait_list_key, str(customer_id))

    time_per_bread, wait_list_reservations, remove_customer_from_wait_list= await pipe1.execute()

    if not remove_customer_from_wait_list or not wait_list_reservations:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ticket is not in Wait list",
            }
        )

    tasks.serve_wait_list_ticket.delay(customer_id, bakery_id)
    customer_reservations = list(map(int, wait_list_reservations.split(",")))

    bread_ids = list(time_per_bread.keys())

    if len(customer_reservations) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    user_detail = {bid: count for bid, count in zip(bread_ids, customer_reservations)}

    logger.info(f"{FILE_NAME}:serve_ticket", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "user_detail": user_detail,
    })

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    return {
        "user_detail": user_detail,
    }


@router.put('/serve_ticket_by_token')
@handle_errors
async def serve_ticket_by_token(
        request: Request,
        ticket: schemas.TicketByTokenRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id
    token_value = ticket.token

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    with SessionLocal() as db:
        customer = crud.get_customer_by_token_today(db, bakery_id, token_value)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for token")

    customer_id = customer.ticket_id

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hget(wait_list_key, str(customer_id))
    pipe1.hdel(wait_list_key, str(customer_id))

    time_per_bread, wait_list_reservations, remove_customer_from_wait_list = await pipe1.execute()

    if not remove_customer_from_wait_list or not wait_list_reservations:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ticket is not in Wait list",
            }
        )

    tasks.serve_wait_list_ticket.delay(customer_id, bakery_id)
    customer_reservations = list(map(int, wait_list_reservations.split(",")))

    bread_ids = list(time_per_bread.keys())

    if len(customer_reservations) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    user_detail = {bid: count for bid, count in zip(bread_ids, customer_reservations)}

    logger.info(f"{FILE_NAME}:serve_ticket_by_token", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "token": token_value,
        "user_detail": user_detail,
    })

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    return {
        "user_detail": user_detail,
    }


@router.get('/current_ticket/{bakery_id}')
@handle_errors
async def current_ticket(
        request: Request,
        bakery_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    current_ticket_id, time_per_bread, reservations_map = await pipe1.execute()

    if not current_ticket_id:
        await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
        return {"has_customer_in_queue": False}

    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})

    if not reservations_map:
        raise HTTPException(status_code=404, detail={"error": "reservation is empty"})

    time_per_bread = {k: int(v) for k, v in time_per_bread.items()}
    current_ticket_id = int(current_ticket_id[0])
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }
    reservation_keys = sorted(reservation_dict.keys())
    bread_ids_sorted = sorted(time_per_bread.keys())

    user_breads = {bid: count for bid, count in zip(bread_ids_sorted, reservation_dict[current_ticket_id])}

    ready, _, wait_until = await redis_helper.calculate_ready_status(
        r, bakery_id, user_breads, time_per_bread, reservation_keys, current_ticket_id, reservation_dict
    )

    # Update user-facing current ticket only when this ticket is ready to be served.
    if ready:
        await redis_helper.set_user_current_ticket(r, bakery_id, current_ticket_id)

    return {
        "ready": ready,
        "wait_until": wait_until,
        "has_customer_in_queue": True,
        "current_ticket_id": current_ticket_id,
        "current_user_detail": user_breads
    }


@router.put('/send_current_ticket_to_wait_list/{bakery_id}')
@handle_errors
async def send_ticket_to_wait_list(
        request: Request,
        bakery_id,
        token: str = Depends(validate_token)
):

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)

    current_ticket_id_raw = await r.zrange(order_key, 0, 0)

    if not current_ticket_id_raw:
        raise HTTPException(status_code=404, detail={'status': 'The queue is empty'})
    customer_id = int(current_ticket_id_raw[0])

    pipe = r.pipeline()
    pipe.hget(res_key, str(customer_id))
    pipe.hdel(res_key, customer_id)
    pipe.zrem(order_key, customer_id)
    current_customer_reservation, r1, r2 = await pipe.execute()

    if not bool(r1 and r2):
        reservation_list = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False)
        if not reservation_list:
            raise HTTPException(status_code=404, detail={'status': 'The queue is empty'})
        status, current_customer_reservation = await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)
        if not status: raise HTTPException(status_code=401, detail="invalid customer_id")

    queue_state = await redis_helper.load_queue_state(r, bakery_id)
    queue_state.mark_ticket_served(customer_id)
    await redis_helper.save_queue_state(r, bakery_id, queue_state)

    await redis_helper.add_customer_to_wait_list(r, bakery_id, customer_id, reservations_str=current_customer_reservation)
    
    # Consume breads for this customer before rebuilding prep_state
    removed = await redis_helper.consume_ready_breads(r, bakery_id, customer_id)
    
    # Rebuild prep_state immediately after removing customer to prevent race condition
    # where new_bread endpoint reads stale prep_state with removed customer ID
    await redis_helper.rebuild_prep_state(r, bakery_id)

    next_ticket_id, time_per_bread, upcoming_breads = await redis_helper.get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id)
    next_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, next_ticket_id, return_error=False)
    next_user_detail = {}
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    if next_ticket_id:
        customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, next_ticket_id)
        customer_reservation = await redis_helper.get_current_cusomter_detail(r, bakery_id, next_ticket_id, time_per_bread, customer_reservation)
        next_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservation)

    tasks.send_ticket_to_wait_list.delay(customer_id, bakery_id)

    if any(bread in time_per_bread.keys() for bread in upcoming_breads):
        await redis_helper.remove_customer_from_upcoming_customers(r, bakery_id, customer_id)
        tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    # Mark breads as consumed in the database as well
    with SessionLocal() as db:
        consumed_count = crud.consume_breads_for_customer_today(db, bakery_id, customer_id)
        logger.info(f"Marked {consumed_count} breads as consumed in DB for ticket {customer_id}")

    logger.info(f"Removed {removed} breads for ticket {customer_id}")
    logger.info(f"{FILE_NAME}:send_ticket_to_wait_list", extra={"bakery_id": bakery_id, "customer_id": customer_id})
    return {
        "next_ticket_id": next_ticket_id,
        "next_user_detail": next_user_detail
    }


@router.get('/is_ticket_in_wait_list/{bakery_id}/{customer_id}')
@handle_errors
async def is_ticket_in_wait_list(
        request: Request,
        bakery_id: int,
        customer_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis
    status = await redis_helper.is_ticket_in_wait_list(r, bakery_id, customer_id)
    return {
        "is_ticket_in_wait_list": status
    }

@router.get('/current_cook_customer/{bakery_id}')
@handle_errors
async def current_cook_customer(
        bakery_id,
        request: Request,
        token: str = Depends(validate_token)
):
    """Read-only view: what customer_breads would the cook see if we called new_bread now?"""
    bakery_id = int(bakery_id)
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    # ============================================================
    # FETCH: Get all data in one pipeline (read-only)
    # ============================================================
    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(prep_state_key)
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.zrange(order_key, 0, -1)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')  # Get all breads to count per customer

    prep_state_str, time_per_bread, reservations_map, order_ids, all_breads = await pipe.execute()

    # ============================================================
    # PARSE: Convert Redis data to usable format
    # ============================================================
    order_ids = [int(x) for x in order_ids] if order_ids else []
    time_per_bread = {k: int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    bread_ids_sorted = sorted(time_per_bread.keys())

    # Count breads already made per customer
    breads_per_customer = {}
    for bread_value in all_breads:
        if ':' in bread_value:
            try:
                cid = int(bread_value.split(':')[1])
            except ValueError:
                continue
            breads_per_customer[cid] = breads_per_customer.get(cid, 0) + 1

    # ============================================================
    # LOGIC: Determine which customer's breads are currently relevant
    #
    # For hardware restart, we want the customer whose breads are
    # *currently* in play, not the one that would come *after* the
    # next bread is baked. To achieve this we:
    #   1) Prefer the customer of the most recently baked bread.
    #   2) If there is no bread yet (fresh start) or that customer is
    #      no longer in today's active reservations, fall back to the
    #      first incomplete reservation in the queue.
    # ============================================================
    def get_customer_needs(customer_id):
        """Get total breads needed for a customer."""
        if not customer_id or str(customer_id) not in reservations_map:
            return 0
        counts = list(map(int, reservations_map[str(customer_id)].split(',')))
        return sum(counts)

    def get_customer_breads_dict(customer_id):
        """Get bread type -> count mapping for a customer."""
        if not customer_id or str(customer_id) not in reservations_map:
            return {}
        counts = list(map(int, reservations_map[str(customer_id)].split(',')))
        return {bid: count for bid, count in zip(bread_ids_sorted, counts)}

    # 1) Try to use the customer of the most recently baked bread.
    last_customer_id = None
    if all_breads:
        last_value = all_breads[-1]
        if ':' in last_value:
            try:
                last_customer_id = int(last_value.split(':')[1])
            except ValueError:
                last_customer_id = None

    working_customer_id = None

    # Only trust last_customer_id if it still exists in today's
    # reservations (i.e., it's an active ticket, not an old one that
    # was removed from the queue).
    if last_customer_id is not None and str(last_customer_id) in reservations_map:
        working_customer_id = last_customer_id

    # 2) If there is no bread yet or the last bread's customer is not
    #    part of the active reservations anymore, fall back to the
    #    first incomplete customer in the queue.
    if working_customer_id is None and order_ids:
        for customer_id in order_ids:
            if str(customer_id) not in reservations_map:
                continue
            needed = get_customer_needs(customer_id)
            already_made = breads_per_customer.get(customer_id, 0)

            if already_made < needed:
                working_customer_id = customer_id
                break

    if working_customer_id:
        response = {
            "customer_id": working_customer_id,
            "customer_breads": get_customer_breads_dict(working_customer_id),
            "next_customer": False,
        }
    else:
        response = {
            "has_customer": False,
            "belongs_to_customer": False,
        }

    return response


@router.post('/new_bread/{bakery_id}')
@handle_errors
async def new_bread(
        bakery_id,
        request: Request,
        token: str = Depends(validate_token)
):
    bakery_id = int(bakery_id)
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    # ============================================================
    # FETCH: Get all data in one pipeline
    # ============================================================
    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    baking_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    last_bread_time_key = redis_helper.REDIS_KEY_LAST_BREAD_TIME.format(bakery_id)
    bread_diff_key = redis_helper.REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(prep_state_key)
    pipe.get(baking_time_key)
    pipe.get(last_bread_time_key)
    pipe.zrevrange(breads_key, 0, 0, withscores=True)  # Get last bread with highest score
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.zrange(order_key, 0, -1)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')  # Get all breads to count per customer

    prep_state_str, baking_time_s_raw, last_bread_time, last_bread_data, \
        time_per_bread, reservations_map, order_ids, all_breads = await pipe.execute()

    # ============================================================
    # PARSE: Convert Redis data to usable format
    # ============================================================
    order_ids = [int(x) for x in order_ids] if order_ids else []
    time_per_bread = {k: int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    bread_ids_sorted = sorted(time_per_bread.keys())
    
    # Count breads already made per customer
    breads_per_customer = {}
    for bread_value in all_breads:
        if ':' in bread_value:
            cid = int(bread_value.split(':')[1])
            breads_per_customer[cid] = breads_per_customer.get(cid, 0) + 1

    # ============================================================
    # LOGIC: Determine which customer we're working on
    # ============================================================
    def get_next_customer_after(customer_id):
        """Find the customer after the given one, or None if no more."""
        try:
            idx = order_ids.index(customer_id)
            return order_ids[idx + 1] if idx + 1 < len(order_ids) else None
        except (ValueError, IndexError):
            return None

    def get_customer_needs(customer_id):
        """Get total breads needed for a customer."""
        if not customer_id or str(customer_id) not in reservations_map:
            return 0
        counts = list(map(int, reservations_map[str(customer_id)].split(',')))
        return sum(counts)

    def get_customer_breads_dict(customer_id):
        """Get bread type -> count mapping for a customer."""
        if not customer_id or str(customer_id) not in reservations_map:
            return {}
        counts = list(map(int, reservations_map[str(customer_id)].split(',')))
        return {bid: count for bid, count in zip(bread_ids_sorted, counts)}
    
    def find_next_incomplete_customer(after_customer_id=None):
        """Find next incomplete customer in queue, optionally starting after a given customer."""
        start_idx = 0
        if after_customer_id:
            try:
                start_idx = order_ids.index(after_customer_id) + 1
            except ValueError:
                start_idx = 0
        
        for i in range(start_idx, len(order_ids)):
            customer_id = order_ids[i]
            needed = get_customer_needs(customer_id)
            already_made = breads_per_customer.get(customer_id, 0)
            if already_made < needed:
                return customer_id
        return None

    # Determine which customer to work on
    # Priority: Continue with customer from prep_state if they're still incomplete
    # Otherwise: Find first incomplete customer from beginning of queue
    working_customer_id = None
    breads_made = 0
    last_completed_customer = None

    if prep_state_str and order_ids:
        # Check if we're currently working on a customer
        state_customer_id, state_bread_count = map(int, prep_state_str.split(':'))
        
        # Verify this customer still exists in the queue and is incomplete
        if state_customer_id in order_ids:
            needed = get_customer_needs(state_customer_id)
            already_made = breads_per_customer.get(state_customer_id, 0)
            
            if already_made < needed:
                # Continue with this customer (don't jump to earlier insertions)
                working_customer_id = state_customer_id
                breads_made = already_made
            else:
                # This customer is now complete
                last_completed_customer = state_customer_id
    
    # If no valid customer from prep_state, scan from beginning
    if working_customer_id is None and order_ids:
        for customer_id in order_ids:
            needed = get_customer_needs(customer_id)
            already_made = breads_per_customer.get(customer_id, 0)
            
            if already_made < needed:
                # Found first incomplete customer
                working_customer_id = customer_id
                breads_made = already_made
                break
            else:
                # This customer is complete
                last_completed_customer = customer_id

    # ============================================================
    # ACTION: Make this bread
    # ============================================================
    bread_belongs_to = working_customer_id or 0
    breads_made += 1 if working_customer_id else 0

    # Determine response (what's next after this bread)
    customer_needs = get_customer_needs(working_customer_id)
    is_customer_done = breads_made >= customer_needs if working_customer_id else False

    current_served_candidate = None

    if is_customer_done:
        # Customer is complete after this bread - find next incomplete customer
        # We need to update breads_per_customer temporarily to reflect the bread we just made
        # so we can correctly find the next incomplete customer
        if working_customer_id:
            breads_per_customer[working_customer_id] = breads_made

        # Search from beginning to handle out-of-order insertions (e.g., tickets 1,3 done, then 2 added)
        next_customer = find_next_incomplete_customer(after_customer_id=None)

        if next_customer:
            # There is another incomplete customer after this one
            response = {
                "customer_id": next_customer,
                "customer_breads": get_customer_breads_dict(next_customer),
                "next_customer": True,
            }
            current_served_candidate = next_customer
        else:
            # No more incomplete customers: this bread was the last bread
            # of the last customer. System is now idle -> set display flag
            # so the next new_ticket can show breads on the cook display.
            if working_customer_id:
                await redis_helper.set_display_flag(r, bakery_id)

            response = {
                "has_customer": False,
                "belongs_to_customer": True,
            }
    elif working_customer_id:
        response = {
            "customer_id": working_customer_id,
            "customer_breads": get_customer_breads_dict(working_customer_id),
            "next_customer": False
        }
    else:
        response = {
            "has_customer": False,
            "belongs_to_customer": False,
        }

    # Update current_served boundary when we've just finished a customer
    # and are showing the next customer's breads to the baker. In this
    # situation, new tickets must not be assigned below that next
    # customer's ticket_id, even though their first bread has not been
    # baked yet.
    if current_served_candidate:
        existing_cs = await redis_helper.get_current_served(r, bakery_id)
        if current_served_candidate > existing_cs:
            await redis_helper.set_current_served(r, bakery_id, current_served_candidate)

    # ============================================================
    # TIMING: Calculate bread metadata
    # ============================================================
    # Get last bread index from sorted set (highest score)
    last_index = 0
    if last_bread_data:
        # last_bread_data format: [(b"timestamp:customer_id", score)]
        last_index = int(last_bread_data[0][1])

    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0
    now = datetime.now()
    now_ts = int(now.timestamp())
    bread_cook_date = int((now + timedelta(seconds=baking_time_s)).timestamp())
    bread_index = last_index + 1
    ttl = seconds_until_midnight_iran()

    time_diff = None
    if last_bread_time:
        time_diff = now_ts - int(float(last_bread_time))

    # ============================================================
    # WRITE: Save everything to Redis
    # ============================================================
    pipe = r.pipeline(transaction=True)

    # Save bread (only to Redis if it belongs to a customer)
    if bread_belongs_to != 0:
        bread_value = f"{bread_cook_date}:{bread_belongs_to}"
        pipe.zadd(breads_key, {bread_value: bread_index})
        pipe.expire(breads_key, ttl)

    # Always update bread tracking metadata
    pipe.set(last_bread_time_key, now_ts, ex=ttl)

    # Save timing stats
    if time_diff is not None:
        pipe.zadd(bread_diff_key, {str(bread_index): time_diff})
        pipe.expire(bread_diff_key, ttl)

    # Update prep_state
    if working_customer_id:
        # Working on a customer - save progress
        pipe.set(prep_state_key, f"{working_customer_id}:{breads_made}", ex=ttl)
    elif last_completed_customer:
        # No more customers, but keep last completed to prevent restart
        pipe.set(prep_state_key, f"{last_completed_customer}:{get_customer_needs(last_completed_customer)}", ex=ttl)
    else:
        # No customers at all
        pipe.delete(prep_state_key)

    await pipe.execute()

    # ============================================================
    # ASYNC: Save to database
    # ============================================================
    tasks.save_bread_to_db.delay(
        bread_belongs_to if bread_belongs_to != 0 else None,
        bakery_id,
        bread_cook_date
    )

    # ============================================================
    # LOG & RETURN
    # ============================================================
    logger.info(
        f"{FILE_NAME}:new_bread",
        extra={
            "bakery_id": bakery_id,
            "bread_index": bread_index,
            "belongs_to": bread_belongs_to,
            "breads_made": breads_made if working_customer_id else None,
            "customer_done": is_customer_done,
        }
    )

    # Add bread_index to response
    # response["bread_index"] = bread_index

    return response


@router.get('/hardware_init')
@handle_errors
async def hardware_initialize(request: Request, bakery_id: int):
    time_per_bread = await redis_helper.get_bakery_time_per_bread(request.app.state.redis, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})
    return time_per_bread
#
# @router.put('/timeout/update')
# @handle_errors
# async def update_timeout(
#         request: Request,
#         data: schemas.UpdateTimeoutRequest,
#         token: str = Depends(validate_token)
#  ):
#     bakery_id = data.bakery_id
#     if not token_helpers.verify_bakery_token(token, bakery_id):
#         raise HTTPException(status_code=401, detail="Invalid token")
#
#     with SessionLocal() as db:
#         with db.begin():
#             new_timeout = crud.update_timeout_second(db, bakery_id, data.seconds)
#             if new_timeout is None:
#                 raise HTTPException(status_code=404, detail='Bakery not found')
#
#     # Update Redis
#     r = request.app.state.redis
#     await redis_helper.update_timeout(r, bakery_id, new_timeout)
#
#     logger.info(f"{FILE_NAME}:update_timeout", extra={"bakery_id": bakery_id, "timeout_min": new_timeout})
#     return {"timeout_sec": new_timeout}
#
#
# @router.get('/upcoming/{bakery_id}')
# @handle_errors
# async def get_upcoming_customer(
#         request: Request,
#         bakery_id: int,
#         token: str = Depends(validate_token)
# ):
#     if not token_helpers.verify_bakery_token(token, bakery_id):
#         raise HTTPException(status_code=401, detail="Invalid token")
#
#     r = request.app.state.redis
#
#     cur_key = redis_helper.REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id)
#     zkey = redis_helper.REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
#
#     # Fetch both in one roundtrip
#     pipe = r.pipeline()
#     pipe.get(cur_key)
#     pipe.zrange(zkey, 0, 0)
#     cur_val, zmembers = await pipe.execute()
#
#     if cur_val:
#         customer_id = int(cur_val)
#     elif zmembers:
#         customer_id = int(zmembers[0])
#     else:
#         await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
#         return {"empty_upcoming": True}
#
#     time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
#     res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
#     baking_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
#     order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
#     upcoming_breads_key = redis_helper.REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
#
#     pipe = r.pipeline()
#     pipe.hgetall(time_key)
#     pipe.hgetall(res_key)
#     pipe.get(baking_time_key)
#     pipe.zrange(order_key, 0, -1)
#     pipe.smembers(upcoming_breads_key)
#     time_per_bread, reservations_map, baking_time_s_raw, order_ids, upcoming_breads = await pipe.execute()
#
#     if time_per_bread:
#         time_per_bread = {int(k): int(v) for k, v in time_per_bread.items()}
#
#     if not time_per_bread or not order_ids:
#         await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
#         return {"empty_upcoming": True}
#
#     reservation_str = reservations_map.get(str(customer_id)) if reservations_map else None
#
#     if not reservation_str:
#         await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
#         return {"empty_upcoming": True}
#
#     counts = [int(x) for x in reservation_str.split(',')]
#     keys = [int(x) for x in order_ids]
#     baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0
#
#     upcoming_breads_set = {int(x) for x in upcoming_breads}  # convert to int
#
#
#     reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservations_map.items()}
#
#     sorted_keys = sorted(time_per_bread.keys())
#     time_per_bread_list = [time_per_bread[k] for k in sorted_keys]
#     alg = algorithm.Algorithm()
#     max_bread_time = max(time_per_bread.values())
#     in_queue_time = await alg.calculate_in_queue_customers_time(
#         keys, customer_id, reservation_dict, time_per_bread_list, r=r, bakery_id=bakery_id
#     )
#
#     empty_slot_time = min(300, alg.compute_empty_slot_time(keys, customer_id, reservation_dict) * max_bread_time)
#     delivery_time_s = in_queue_time + empty_slot_time
#     preparation_time = alg.compute_bread_time(time_per_bread_list, counts)
#
#     notification_lead_time_s = preparation_time + baking_time_s
#     is_ready = delivery_time_s <= notification_lead_time_s
#
#     response = {
#         "empty_upcoming": False,
#         "ready_to_show": False
#     }
#
#     if is_ready and cur_val is None:
#         customer_breads = dict(zip(time_per_bread.keys(), counts))
#         upcoming_customer_breads = {
#             bread_id: qty
#             for bread_id, qty in customer_breads.items()
#             if bread_id in upcoming_breads_set
#         }
#         response['customer_id'] = customer_id
#         response["breads"] = upcoming_customer_breads
#         response['ready_to_show'] = True
#         response['preparation_time'] = preparation_time
#
#         await redis_helper.remove_customer_from_upcoming_customers_and_add_to_current_upcoming_customer(
#             r, bakery_id, customer_id, preparation_time
#         )
#         tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)
#
#     return response
