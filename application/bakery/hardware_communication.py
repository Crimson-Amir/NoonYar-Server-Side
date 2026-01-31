from datetime import datetime, timedelta
import time
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
    
    if show_on_display:
        existing_cs = await redis_helper.get_current_served(r, bakery_id)
        if customer_ticket_id > existing_cs:
            await redis_helper.set_current_served(r, bakery_id, customer_ticket_id)
    
    logger.info(f"{FILE_NAME}:new_cusomer", extra={"bakery_id": customer.bakery_id, "bread_requirements": bread_requirements, "customer_in_upcoming_customer": customer_in_upcoming_customer, "show_on_display": show_on_display, "token": customer_token})
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, bread_requirements, customer_in_upcoming_customer, customer_token)

    await mqtt_client.notify_new_ticket(request, bakery_id, customer_ticket_id, customer_token)

    # Telegram log: new ticket
    bread_names = await redis_helper.get_bakery_bread_names(r)
    bread_lines = []
    for bid, count in bread_requirements.items():
        name = bread_names.get(str(bid), str(bid)) if bread_names else str(bid)
        bread_lines.append(f"- {name} (id: {bid}): {count}")

    ticket_msg = (
        f"Bakery ID: {bakery_id}"
        f"\nTicket Number: {customer_ticket_id}"
        f"\nShow On Display: {show_on_display}"
        f"\nToken: {customer_token}"
        f"\n\nBread Requirements:\n" + "\n".join(bread_lines)
    )

    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:new_ticket", ticket_msg)

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

    if customer_reservations and all(int(x) == 0 for x in customer_reservations):
        with SessionLocal() as db:
            breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [int(customer_id)])
        bread_counts = breads_map_db.get(int(customer_id), {})
        customer_reservations = [int(bread_counts.get(int(bid), 0)) for bid in bread_ids]

    user_detail = {bid: count for bid, count in zip(bread_ids, customer_reservations)}

    urgent_by_ticket = await redis_helper.get_urgent_breads_by_ticket(r, bakery_id, time_per_bread)
    urgent_breads = urgent_by_ticket.get(int(customer_id), {})

    logger.info(f"{FILE_NAME}:serve_ticket", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "user_detail": user_detail,
    })

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    return {
        "user_detail": user_detail,
        "urgent_breads": urgent_breads,
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

    if customer_reservations and all(int(x) == 0 for x in customer_reservations):
        with SessionLocal() as db:
            breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [int(customer_id)])
        bread_counts = breads_map_db.get(int(customer_id), {})
        customer_reservations = [int(bread_counts.get(int(bid), 0)) for bid in bread_ids]

    user_detail = {bid: count for bid, count in zip(bread_ids, customer_reservations)}

    urgent_by_ticket = await redis_helper.get_urgent_breads_by_ticket(r, bakery_id, time_per_bread)
    urgent_breads = urgent_by_ticket.get(int(customer_id), {})

    logger.info(f"{FILE_NAME}:serve_ticket_by_token", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "token": token_value,
        "user_detail": user_detail,
    })

    # Telegram log: serve ticket by token
    serve_msg = (
        f"Bakery ID: {bakery_id}"
        f"\nTicket Number: {customer_id}"
        f"\nCustomer ID: {customer.id}"
        f"\nToken: {token_value}"
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:serve_ticket_by_token", serve_msg)

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    return {
        "user_detail": user_detail,
        "urgent_breads": urgent_breads,
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
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    baking_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)

    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, -1)
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    pipe1.get(baking_time_key)
    pipe1.zrangebyscore(breads_key, '-inf', '+inf')
    pipe1.smembers(base_done_key)
    order_ids_raw, time_per_bread, reservations_map, baking_time_s_raw, all_breads, base_done_raw = await pipe1.execute()

    if not order_ids_raw:
        await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
        return {"has_customer_in_queue": False}

    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})

    if not reservations_map:
        raise HTTPException(status_code=404, detail={"error": "reservation is empty"})

    def _as_text(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return v.decode()
        return str(v)

    time_per_bread = {str(k): int(v) for k, v in time_per_bread.items()}
    bread_ids_sorted = sorted(time_per_bread.keys())

    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)
    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0

    order_ids = [int(x) for x in order_ids_raw]
    reservation_dict = {int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()}
    reservation_keys = sorted(reservation_dict.keys())

    breads_by_customer = {}
    for bread_value in all_breads or []:
        bread_value = _as_text(bread_value)
        if not bread_value or ':' not in bread_value:
            continue
        try:
            ts_str, cid_str = bread_value.split(':')
            cid = int(cid_str)
            ts = float(ts_str)
        except Exception:
            continue
        breads_by_customer.setdefault(cid, []).append(ts)
    for cid in breads_by_customer:
        breads_by_customer[cid].sort()

    # Tickets that moved to wait list have their baked breads removed from Redis.
    # Use base_done marker to treat base breads as already baked (timestamp=0.0).
    for tid, counts in reservation_dict.items():
        if int(tid) not in base_done_ids:
            continue
        base_total = int(sum(int(x) for x in counts))
        if base_total <= 0:
            continue
        existing = breads_by_customer.get(int(tid), [])
        if len(existing) >= base_total:
            continue
        breads_by_customer[int(tid)] = sorted(([0.0] * (base_total - len(existing))) + list(existing))

    urgent_by_ticket = await redis_helper.get_urgent_breads_by_ticket(r, bakery_id, time_per_bread)
    urgent_remaining_time = await redis_helper.get_urgent_remaining_total_time(r, bakery_id, time_per_bread)

    # Precompute total breads needed per ticket (base + injected urgent totals)
    total_needed_by_ticket = {}
    base_detail_by_ticket = {}
    for tid, counts in reservation_dict.items():
        base_detail = {bid: int(c) for bid, c in zip(bread_ids_sorted, counts)}
        base_detail_by_ticket[int(tid)] = base_detail
        extra = urgent_by_ticket.get(int(tid), {}) or {}
        total_needed_by_ticket[int(tid)] = int(sum(base_detail.values())) + int(sum(int(v) for v in extra.values()))

    now = time.time()
    avg_cook_time = (sum(time_per_bread.values()) // len(time_per_bread)) if time_per_bread else 0

    def estimate_wait_until(tid: int) -> int:
        need_total = int(total_needed_by_ticket.get(int(tid), 0))
        if need_total <= 0:
            return 0

        breads_ts = breads_by_customer.get(int(tid), [])
        if breads_ts and len(breads_ts) >= need_total:
            ready_at = float(breads_ts[need_total - 1])
            return max(0, int(ready_at - now))

        # Case1: no breads exist at all
        if not all_breads:
            total_wait_s = int(baking_time_s)
            for key in reservation_keys:
                if int(key) > int(tid):
                    break
                base_counts = reservation_dict[int(key)]
                base_detail = {bid: int(c) for bid, c in zip(bread_ids_sorted, base_counts)}
                extra = urgent_by_ticket.get(int(key), {}) or {}
                eff = dict(base_detail)
                for k, v in extra.items():
                    eff[k] = int(eff.get(k, 0)) + int(v)
                total_wait_s += sum(int(count) * int(time_per_bread[str(bid)]) for bid, count in eff.items())
            if urgent_remaining_time:
                total_wait_s += int(urgent_remaining_time)
            return int(total_wait_s)

        # Case2: some breads exist, but none for this customer
        if not breads_ts:
            base_detail = dict(base_detail_by_ticket.get(int(tid), {}))
            extra = urgent_by_ticket.get(int(tid), {}) or {}
            eff = dict(base_detail)
            for k, v in extra.items():
                eff[k] = int(eff.get(k, 0)) + int(v)

            prep_time = sum(int(count) * int(time_per_bread[str(bid)]) for bid, count in eff.items())

            total_remaining_before = 0
            for cid in [key for key in reservation_keys if int(key) < int(tid)]:
                cid = int(cid)
                made = len(breads_by_customer.get(cid, []))
                needed = int(total_needed_by_ticket.get(cid, 0))
                if made >= needed:
                    continue
                if made > 0:
                    total_remaining_before += (needed - made) * int(avg_cook_time)
                else:
                    base_detail_prev = dict(base_detail_by_ticket.get(cid, {}))
                    extra_prev = urgent_by_ticket.get(cid, {}) or {}
                    eff_prev = dict(base_detail_prev)
                    for k, v in extra_prev.items():
                        eff_prev[k] = int(eff_prev.get(k, 0)) + int(v)
                    total_remaining_before += sum(
                        int(count) * int(time_per_bread[str(bid)])
                        for bid, count in eff_prev.items()
                    )

            total_wait_s = int(total_remaining_before) + int(prep_time) + int(baking_time_s)
            if urgent_remaining_time:
                total_wait_s += int(urgent_remaining_time)
            return int(total_wait_s)

        # Case3: partially prepared for this ticket
        remaining = need_total - len(breads_ts)
        active_types = []
        base_detail = dict(base_detail_by_ticket.get(int(tid), {}))
        extra = urgent_by_ticket.get(int(tid), {}) or {}
        eff = dict(base_detail)
        for k, v in extra.items():
            eff[k] = int(eff.get(k, 0)) + int(v)

        for bread_type, count in eff.items():
            if int(count) > 0:
                active_types.append(int(time_per_bread[str(bread_type)]))
        this_avg = (sum(active_types) // len(active_types)) if active_types else int(avg_cook_time)

        return int(remaining * this_avg + baking_time_s)

    # Pick the ticket that will be ready the soonest
    best_tid = None
    best_wait = None
    for tid in order_ids:
        if tid not in reservation_dict:
            continue
        w = int(estimate_wait_until(int(tid)))
        if best_wait is None or w < best_wait or (w == best_wait and int(tid) < int(best_tid)):
            best_tid = int(tid)
            best_wait = int(w)

    if best_tid is None:
        await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
        return {"has_customer_in_queue": False}

    calc_counts = reservation_dict[int(best_tid)]
    calc_user_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, calc_counts)}

    display_counts = list(calc_counts)
    if display_counts and all(int(x) == 0 for x in display_counts):
        with SessionLocal() as db:
            breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [int(best_tid)])
        bread_counts = breads_map_db.get(int(best_tid), {})
        display_counts = [int(bread_counts.get(int(bid), 0)) for bid in bread_ids_sorted]
    display_user_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, display_counts)}

    urgent_breads = urgent_by_ticket.get(int(best_tid), {})
    ready = bool(best_wait == 0)

    return {
        "ready": ready,
        "wait_until": int(best_wait) if best_wait is not None else 0,
        "has_customer_in_queue": True,
        "current_ticket_id": int(best_tid),
        "current_user_detail": display_user_breads,
        "urgent_breads": urgent_breads,
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

    best = await redis_helper.select_best_ticket_by_ready_time(r, int(bakery_id))
    if not best:
        raise HTTPException(status_code=404, detail={'status': 'The queue is empty'})
    customer_id = int(best["ticket_id"])

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

    # Update user-facing current ticket only when this ticket is ready to be served.
    await redis_helper.set_user_current_ticket(r, bakery_id, customer_id)
    
    # Consume breads for this customer before rebuilding prep_state
    removed = await redis_helper.consume_ready_breads(r, bakery_id, customer_id)
    
    # Rebuild prep_state immediately after removing customer to prevent race condition
    # where new_bread endpoint reads stale prep_state with removed customer ID
    await redis_helper.rebuild_prep_state(r, bakery_id)

    next_ticket_id, time_per_bread, upcoming_breads = await redis_helper.get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id)
    next_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, next_ticket_id, return_error=False)

    next_best = await redis_helper.select_best_ticket_by_ready_time(r, int(bakery_id))
    if next_best:
        next_ticket_id = int(next_best["ticket_id"])

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

    # Telegram log: ticket moved to wait list
    wait_list_msg = (
        f"Bakery ID: {bakery_id}"
        f"\nTicket Number: {customer_id}"
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:send_ticket_to_wait_list", wait_list_msg)
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
    urgent_prep_key = redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id)
    urgent_queue_key = redis_helper.REDIS_KEY_URGENT_QUEUE.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(prep_state_key)
    pipe.get(urgent_prep_key)
    pipe.zrange(urgent_queue_key, 0, 0)
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.zrange(order_key, 0, -1)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')  # Get all breads to count per customer
    pipe.smembers(base_done_key)

    prep_state_str, urgent_processing_raw, urgent_next_raw, time_per_bread, reservations_map, order_ids, all_breads, base_done_raw = await pipe.execute()

    def _as_text(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return v.decode()
        return str(v)

    prep_state_str = _as_text(prep_state_str)

    # ============================================================
    # PARSE: Convert Redis data to usable format
    # ============================================================
    order_ids = [int(_as_text(x)) for x in order_ids] if order_ids else []
    time_per_bread = {_as_text(k): int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    bread_ids_sorted = sorted(time_per_bread.keys())

    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)

    # Count breads already made per customer
    breads_per_customer = {}
    for bread_value in all_breads:
        bread_value = _as_text(bread_value)
        if bread_value and ':' in bread_value:
            try:
                cid = int(bread_value.split(':')[1])
            except Exception:
                continue
            breads_per_customer[cid] = breads_per_customer.get(cid, 0) + 1

    # If base is already complete for a ticket, treat its base breads as fully baked
    # even if breads were consumed from Redis when the ticket moved to wait list.
    for cid in base_done_ids:
        if str(cid) not in (reservations_map or {}):
            continue
        try:
            base_needed = sum(int(x) for x in str(reservations_map[str(cid)]).split(','))
        except Exception:
            continue
        breads_per_customer[int(cid)] = max(int(breads_per_customer.get(int(cid), 0)), int(base_needed))

    urgent_id = None
    if urgent_processing_raw:
        urgent_id = _as_text(urgent_processing_raw)
    elif urgent_next_raw:
        urgent_id = _as_text(urgent_next_raw[0])

    # Determine active ticket based on prep_state (non-preemptive urgent behavior)
    state_customer_id = None
    state_active = False
    if prep_state_str and order_ids:
        try:
            state_customer_id, _ = map(int, str(prep_state_str).split(':'))
        except ValueError:
            state_customer_id = None

    if state_customer_id and state_customer_id in order_ids and str(state_customer_id) in (reservations_map or {}):
        try:
            counts = list(map(int, reservations_map[str(state_customer_id)].split(',')))
        except Exception:
            counts = []
        needed = sum(counts) if counts else 0
        already_made = breads_per_customer.get(state_customer_id, 0)
        if already_made < needed:
            state_active = True
        else:
            urgent_original_extra = await redis_helper.get_urgent_original_counts_for_ticket(
                r, bakery_id, state_customer_id, time_per_bread
            )
            if urgent_original_extra:
                state_active = True

    # If urgent is currently processing / next, only show it if it belongs to the active ticket.
    if urgent_id and time_per_bread and state_active and state_customer_id:
        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
        pipe_u = r.pipeline()
        pipe_u.hget(urgent_item_key, "ticket_id")
        pipe_u.hget(urgent_item_key, "original_breads")
        ticket_id_raw, original_raw = await pipe_u.execute()

        if ticket_id_raw and int(ticket_id_raw) == int(state_customer_id):
            original_counts = []
            if original_raw:
                original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
            if len(original_counts) < len(bread_ids_sorted):
                original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))

            if any(int(x) > 0 for x in original_counts[: len(bread_ids_sorted)]):
                urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
                return {
                    "customer_id": int(ticket_id_raw),
                    "customer_breads": urgent_breads,
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": urgent_id,
                }

    # ============================================================
    # LOGIC: Determine which customer's breads are currently relevant
    #
    # For hardware restart, we want the customer whose breads are
    # *currently* in play (i.e. still baking). If the last bread for a
    # customer has already finished baking, we should move on to the
    # next incomplete ticket, just like new_bread does.
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

    def get_queue_working_customer():
        """Find the first incomplete ticket, similar to new_bread but without simulating a new bread."""
        if prep_state_str and order_ids:
            try:
                state_customer_id, state_bread_count = map(int, prep_state_str.split(':'))
            except ValueError:
                state_customer_id = None
            if state_customer_id and state_customer_id in order_ids and str(state_customer_id) in reservations_map:
                needed = get_customer_needs(state_customer_id)
                already_made = breads_per_customer.get(state_customer_id, 0)
                if already_made < needed:
                    return state_customer_id

        if order_ids:
            for customer_id in order_ids:
                if str(customer_id) not in reservations_map:
                    continue
                needed = get_customer_needs(customer_id)
                already_made = breads_per_customer.get(customer_id, 0)

                if already_made < needed:
                    return customer_id
        return None

    working_customer_id = get_queue_working_customer()

    if state_active and state_customer_id:
        working_customer_id = state_customer_id

    if working_customer_id:
        response = {
            "customer_id": working_customer_id,
            "customer_breads": get_customer_breads_dict(working_customer_id),
            "next_customer": False,
        }
    else:
        if urgent_id and time_per_bread:
            urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
            pipe_u = r.pipeline()
            pipe_u.hget(urgent_item_key, "ticket_id")
            pipe_u.hget(urgent_item_key, "original_breads")
            ticket_id_raw, original_raw = await pipe_u.execute()

            original_counts = []
            if original_raw:
                original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
            if len(original_counts) < len(bread_ids_sorted):
                original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))

            if any(int(x) > 0 for x in original_counts[: len(bread_ids_sorted)]):
                urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
                return {
                    "customer_id": int(ticket_id_raw) if ticket_id_raw else -1,
                    "customer_breads": urgent_breads,
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": urgent_id,
                }
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
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(prep_state_key)
    pipe.get(baking_time_key)
    pipe.get(last_bread_time_key)
    pipe.zrevrange(breads_key, 0, 0, withscores=True)  # Get last bread with highest score
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.zrange(order_key, 0, -1)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')  # Get all breads to count per customer
    pipe.smembers(base_done_key)

    prep_state_str, baking_time_s_raw, last_bread_time, last_bread_data, \
        time_per_bread, reservations_map, order_ids, all_breads, base_done_raw = await pipe.execute()

    def _as_text(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return v.decode()
        return str(v)

    # ============================================================
    # PARSE: Convert Redis data to usable format
    # ============================================================
    prep_state_str = _as_text(prep_state_str)
    order_ids = [int(_as_text(x)) for x in order_ids] if order_ids else []
    time_per_bread = {_as_text(k): int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    bread_ids_sorted = sorted(time_per_bread.keys())
    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)
    
    # Count breads already made per customer
    breads_per_customer = {}
    for bread_value in all_breads:
        bread_value = _as_text(bread_value)
        if bread_value and ':' in bread_value:
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
            if customer_id in base_done_ids:
                already_made = max(int(already_made), int(needed))
            if already_made < needed:
                return customer_id
        return None

    did_urgent_bread = False
    update_prep_state = True
    current_served_candidate = None
    working_customer_id = None
    breads_made = 0
    last_completed_customer = None
    bread_belongs_to = 0
    response = None

    urgent_prep_key = redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id)
    urgent_queue_key = redis_helper.REDIS_KEY_URGENT_QUEUE.format(bakery_id)
    urgent_processing_raw = await r.get(urgent_prep_key)
    if urgent_processing_raw and time_per_bread:
        urgent_id = _as_text(urgent_processing_raw)
        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)

        pipe_u = r.pipeline()
        pipe_u.hget(urgent_item_key, "ticket_id")
        pipe_u.hget(urgent_item_key, "original_breads")
        pipe_u.hget(urgent_item_key, "created_at")
        ticket_id_raw, original_raw, created_at_raw = await pipe_u.execute()

        urgent_state = await redis_helper.consume_one_urgent_bread(r, bakery_id, time_per_bread)
        if urgent_state:
            tasks.log_urgent_remaining.delay(
                bakery_id,
                urgent_id,
                urgent_state.get("remaining_by_type") or {},
                bool(urgent_state.get("done")),
            )
        did_urgent_bread = True
        update_prep_state = False
        bread_belongs_to = int(ticket_id_raw) if ticket_id_raw else 0

        if urgent_processing_raw:
            original_counts = []
            if original_raw:
                original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
            if len(original_counts) < len(bread_ids_sorted):
                original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))
            urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
            urgent_customer_id = int(ticket_id_raw) if ticket_id_raw else -1

            if urgent_state and urgent_state.get("done"):
                next_urgent_id = None
                if ticket_id_raw:
                    next_urgent_id = await redis_helper.start_next_urgent_for_ticket_if_available(
                        r, bakery_id, int(ticket_id_raw)
                    )
                if not next_urgent_id:
                    next_urgent_id = await redis_helper.start_next_urgent_if_available(r, bakery_id)
                if next_urgent_id and str(next_urgent_id) != str(urgent_id):
                    tasks.log_urgent_processing.delay(bakery_id, str(next_urgent_id))
                    next_item_key = redis_helper.get_urgent_item_key(bakery_id, str(next_urgent_id))
                    pipe_n = r.pipeline()
                    pipe_n.hget(next_item_key, "ticket_id")
                    pipe_n.hget(next_item_key, "original_breads")
                    next_ticket_id_raw, next_original_raw = await pipe_n.execute()

                    next_counts = []
                    if next_original_raw:
                        next_counts = [int(x) for x in _as_text(next_original_raw).split(",") if str(x) != ""]
                    if len(next_counts) < len(bread_ids_sorted):
                        next_counts = next_counts + [0] * (len(bread_ids_sorted) - len(next_counts))
                    next_urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, next_counts)}
                    response = {
                        "customer_id": int(next_ticket_id_raw) if next_ticket_id_raw else -1,
                        "customer_breads": next_urgent_breads,
                        "next_customer": False,
                        "urgent": True,
                        "urgent_id": str(next_urgent_id),
                    }
                else:
                    next_customer = find_next_incomplete_customer(after_customer_id=None)
                    if next_customer:
                        response = {
                            "customer_id": next_customer,
                            "customer_breads": get_customer_breads_dict(next_customer),
                            "next_customer": True,
                        }
                        current_served_candidate = next_customer
                    else:
                        response = {
                            "has_customer": False,
                            "belongs_to_customer": True,
                        }
            else:
                response = {
                    "customer_id": urgent_customer_id,
                    "customer_breads": urgent_breads,
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": str(urgent_id),
                }

    if response is None:
        # Determine which customer to work on
        # Priority: Continue with customer from prep_state if they're still incomplete
        # Otherwise: Find first incomplete customer from beginning of queue
        if prep_state_str and order_ids:
            try:
                state_customer_id, _ = map(int, str(prep_state_str).split(':'))
            except ValueError:
                state_customer_id = None

            if state_customer_id and state_customer_id in order_ids:
                needed = get_customer_needs(state_customer_id)
                already_made = breads_per_customer.get(state_customer_id, 0)
                if int(state_customer_id) in base_done_ids:
                    already_made = max(int(already_made), int(needed))
                if already_made < needed:
                    working_customer_id = state_customer_id
                    breads_made = already_made
                else:
                    urgent_original_extra = await redis_helper.get_urgent_original_counts_for_ticket(
                        r, bakery_id, state_customer_id, time_per_bread
                    )
                    if urgent_original_extra:
                        working_customer_id = state_customer_id
                        breads_made = already_made
                    else:
                        last_completed_customer = state_customer_id

        if working_customer_id is None and order_ids:
            for customer_id in order_ids:
                needed = get_customer_needs(customer_id)
                already_made = breads_per_customer.get(customer_id, 0)
                if int(customer_id) in base_done_ids:
                    already_made = max(int(already_made), int(needed))
                if already_made < needed:
                    working_customer_id = customer_id
                    breads_made = already_made
                    break
                else:
                    last_completed_customer = customer_id

        if working_customer_id is None:
            urgent_id = await redis_helper.start_next_urgent_if_available(r, bakery_id)
            if urgent_id and time_per_bread:
                tasks.log_urgent_processing.delay(bakery_id, str(urgent_id))
                urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, str(urgent_id))
                pipe_u = r.pipeline()
                pipe_u.hget(urgent_item_key, "ticket_id")
                pipe_u.hget(urgent_item_key, "original_breads")
                ticket_id_raw, original_raw = await pipe_u.execute()

                urgent_state = await redis_helper.consume_one_urgent_bread(r, bakery_id, time_per_bread)
                if urgent_state:
                    tasks.log_urgent_remaining.delay(
                        bakery_id,
                        str(urgent_id),
                        urgent_state.get("remaining_by_type") or {},
                        bool(urgent_state.get("done")),
                    )
                did_urgent_bread = True
                update_prep_state = False
                bread_belongs_to = int(ticket_id_raw) if ticket_id_raw else 0

                original_counts = []
                if original_raw:
                    original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
                if len(original_counts) < len(bread_ids_sorted):
                    original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))
                urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}

                response = {
                    "customer_id": int(ticket_id_raw) if ticket_id_raw else -1,
                    "customer_breads": urgent_breads,
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": str(urgent_id),
                }
            else:
                bread_belongs_to = 0
                response = {
                    "has_customer": False,
                    "belongs_to_customer": False,
                }
        else:
            bread_belongs_to = working_customer_id
            customer_needs = get_customer_needs(working_customer_id)

            # Restart-safe: if base breads are already complete but this ticket has its own urgent,
            # the next baked bread must be urgent (not an extra base bread).
            if breads_made >= customer_needs:
                own_urgent_id = await redis_helper.start_next_urgent_for_ticket_if_available(
                    r, bakery_id, working_customer_id
                )
                if own_urgent_id and time_per_bread:
                    tasks.log_urgent_processing.delay(bakery_id, str(own_urgent_id))
                    urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, str(own_urgent_id))
                    pipe_u = r.pipeline()
                    pipe_u.hget(urgent_item_key, "ticket_id")
                    pipe_u.hget(urgent_item_key, "original_breads")
                    ticket_id_raw, original_raw = await pipe_u.execute()

                    urgent_state = await redis_helper.consume_one_urgent_bread(r, bakery_id, time_per_bread)
                    if urgent_state:
                        tasks.log_urgent_remaining.delay(
                            bakery_id,
                            str(own_urgent_id),
                            urgent_state.get("remaining_by_type") or {},
                            bool(urgent_state.get("done")),
                        )
                    did_urgent_bread = True
                    update_prep_state = False
                    bread_belongs_to = int(ticket_id_raw) if ticket_id_raw else 0

                    original_counts = []
                    if original_raw:
                        original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
                    if len(original_counts) < len(bread_ids_sorted):
                        original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))
                    urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}

                    response = {
                        "customer_id": int(ticket_id_raw) if ticket_id_raw else -1,
                        "customer_breads": urgent_breads,
                        "next_customer": False,
                        "urgent": True,
                        "urgent_id": str(own_urgent_id),
                    }
                    is_customer_done = False
                else:
                    breads_made += 1
                    is_customer_done = breads_made >= customer_needs
            else:
                breads_made += 1
                is_customer_done = breads_made >= customer_needs

            if is_customer_done:
                breads_per_customer[working_customer_id] = breads_made

                own_urgent_id = await redis_helper.start_next_urgent_for_ticket_if_available(
                    r, bakery_id, working_customer_id
                )
                if own_urgent_id and time_per_bread:
                    tasks.log_urgent_processing.delay(bakery_id, str(own_urgent_id))
                    urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, str(own_urgent_id))
                    pipe_u = r.pipeline()
                    pipe_u.hget(urgent_item_key, "ticket_id")
                    pipe_u.hget(urgent_item_key, "original_breads")
                    ticket_id_raw, original_raw = await pipe_u.execute()

                    original_counts = []
                    if original_raw:
                        original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
                    if len(original_counts) < len(bread_ids_sorted):
                        original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))
                    urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}

                    response = {
                        "customer_id": int(ticket_id_raw) if ticket_id_raw else -1,
                        "customer_breads": urgent_breads,
                        "next_customer": False,
                        "urgent": True,
                        "urgent_id": str(own_urgent_id),
                    }
                else:
                    urgent_id = await redis_helper.start_next_urgent_if_available(r, bakery_id)
                    if urgent_id and time_per_bread:
                        tasks.log_urgent_processing.delay(bakery_id, str(urgent_id))
                        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, str(urgent_id))
                        pipe_u = r.pipeline()
                        pipe_u.hget(urgent_item_key, "ticket_id")
                        pipe_u.hget(urgent_item_key, "original_breads")
                        ticket_id_raw, original_raw = await pipe_u.execute()

                        original_counts = []
                        if original_raw:
                            original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
                        if len(original_counts) < len(bread_ids_sorted):
                            original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))
                        urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}

                        response = {
                            "customer_id": int(ticket_id_raw) if ticket_id_raw else -1,
                            "customer_breads": urgent_breads,
                            "next_customer": False,
                            "urgent": True,
                            "urgent_id": str(urgent_id),
                        }
                    else:
                        next_customer = find_next_incomplete_customer(after_customer_id=None)
                        if next_customer:
                            response = {
                                "customer_id": next_customer,
                                "customer_breads": get_customer_breads_dict(next_customer),
                                "next_customer": True,
                            }
                            current_served_candidate = next_customer
                        else:
                            await redis_helper.set_display_flag(r, bakery_id)
                            response = {
                                "has_customer": False,
                                "belongs_to_customer": True,
                            }
            else:
                response = {
                    "customer_id": working_customer_id,
                    "customer_breads": get_customer_breads_dict(working_customer_id),
                    "next_customer": False,
                }

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

    # Update prep_state (do not change it while baking urgent breads)
    if update_prep_state:
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
            "urgent": bool(did_urgent_bread),
            "breads_made": breads_made if (working_customer_id and update_prep_state) else None,
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
