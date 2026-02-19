from datetime import datetime, timedelta
import time
import json
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


def _safe_json_map(raw_value):
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _get_grouped_urgent_breads_for_tickets(bakery_id: int, ticket_ids: list[int], bread_names: dict) -> dict[int, dict[str, dict[str, int]]]:
    if not ticket_ids:
        return {}
    ticket_set = {int(x) for x in ticket_ids}
    grouped: dict[int, dict[str, dict[str, int]]] = {}
    with SessionLocal() as db:
        rows = crud.get_today_urgent_bread_logs(db, bakery_id)

    for row in rows or []:
        if str(getattr(row, "status", "")) == "CANCELLED":
            continue
        tid_raw = getattr(row, "ticket_id", None)
        if tid_raw is None:
            continue
        try:
            tid = int(tid_raw)
        except Exception:
            continue
        if tid not in ticket_set:
            continue

        urgent_map = _safe_json_map(getattr(row, "original_breads_json", None))
        breads_named = {}
        for bid_raw, count in (urgent_map or {}).items():
            try:
                count_int = int(count)
            except Exception:
                count_int = 0
            if count_int <= 0:
                continue
            try:
                bid_int = int(bid_raw)
            except Exception:
                bid_int = None
            name = bread_names.get(str(bid_int), str(bid_int)) if bid_int is not None else str(bid_raw)
            breads_named[str(name)] = int(breads_named.get(str(name), 0)) + int(count_int)

        if not breads_named:
            continue
        grouped.setdefault(int(tid), {})[str(getattr(row, "urgent_id", ""))] = {
            "breads": breads_named,
            "is_prepared": str(getattr(row, "status", "")) == "DONE",
            "reason": "",
        }

    return grouped


async def _fill_urgent_reasons_from_redis(r, bakery_id: int, grouped: dict[int, dict[str, dict]]) -> dict[int, dict[str, dict]]:
    urgent_ids: list[str] = []
    for by_id in (grouped or {}).values():
        urgent_ids.extend([str(uid) for uid in (by_id or {}).keys()])

    if not urgent_ids:
        return grouped

    pipe = r.pipeline()
    for uid in urgent_ids:
        pipe.hget(redis_helper.get_urgent_item_key(bakery_id, uid), "reason")
    reason_rows = await pipe.execute()

    for uid, raw in zip(urgent_ids, reason_rows):
        txt = raw.decode() if isinstance(raw, (bytes, bytearray)) else (str(raw) if raw else "")
        if not txt:
            continue
        for by_id in (grouped or {}).values():
            if uid in by_id:
                by_id[uid]["reason"] = txt
                break

    return grouped

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
    note = str(customer.note or "").strip()

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

    bread_ids_sorted = sorted(breads_type.keys())
    counts_list = [int(bread_requirements.get(bid, 0)) for bid in bread_ids_sorted]
    customer_ticket_id = await algorithm.Algorithm.new_reservation(reservation_dict, counts_list, r, bakery_id)

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
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, bread_requirements, customer_in_upcoming_customer, customer_token, note)

    await mqtt_client.publish_ticket_job(
        request,
        bakery_id=bakery_id,
        ticket_id=customer_ticket_id,
        token=customer_token,
        print_ticket=True,
        show_on_display=False,
    )

    # Telegram log: new ticket
    bread_names = await redis_helper.get_bakery_bread_names(r)
    ticket_msg = endpoint_helper.format_admin_event_message(
        event_title="New Ticket Created",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": customer_ticket_id,
            "token": customer_token,
            "show_on_display": show_on_display,
        },
        bread_requirements={
            (bread_names.get(str(bid), str(bid)) if bread_names else str(bid)): int(count)
            for bid, count in bread_requirements.items()
        },
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

    bread_names = await redis_helper.get_bakery_bread_names(r)
    breads_by_name = {}
    for bid, count in user_detail.items():
        try:
            count_int = int(count)
        except Exception:
            count_int = 0
        if count_int <= 0:
            continue
        key = bread_names.get(str(bid), str(bid)) if bread_names else str(bid)
        breads_by_name[str(key)] = int(breads_by_name.get(str(key), 0)) + int(count_int)

    urgent_grouped = _get_grouped_urgent_breads_for_tickets(
        bakery_id, [int(customer_id)], bread_names
    )
    urgent_grouped = await _fill_urgent_reasons_from_redis(r, bakery_id, urgent_grouped)
    urgent_breads = urgent_grouped.get(int(customer_id), {})

    logger.info(f"{FILE_NAME}:serve_ticket", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "user_detail": user_detail,
    })

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    try:
        await redis_helper.cleanup_urgent_items_for_ticket(
            r,
            bakery_id,
            int(customer_id),
            statuses=("DONE", "PENDING", "PROCESSING"),
        )
    except Exception:
        pass

    return {
        "breads": breads_by_name,
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

    bread_names = await redis_helper.get_bakery_bread_names(r)
    breads_by_name = {}
    for bid, count in user_detail.items():
        try:
            count_int = int(count)
        except Exception:
            count_int = 0
        if count_int <= 0:
            continue
        key = bread_names.get(str(bid), str(bid)) if bread_names else str(bid)
        breads_by_name[str(key)] = int(breads_by_name.get(str(key), 0)) + int(count_int)

    urgent_grouped = _get_grouped_urgent_breads_for_tickets(
        bakery_id, [int(customer_id)], bread_names
    )
    urgent_grouped = await _fill_urgent_reasons_from_redis(r, bakery_id, urgent_grouped)
    urgent_breads = urgent_grouped.get(int(customer_id), {})

    logger.info(f"{FILE_NAME}:serve_ticket_by_token", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "token": token_value,
        "user_detail": user_detail,
    })

    # Telegram log: serve ticket by token
    serve_msg = endpoint_helper.format_admin_event_message(
        event_title="Ticket Served By Token",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": customer_id,
            "customer_id": customer.id,
            "token": token_value,
        },
        bread_requirements={**breads_by_name, **({f"urgent::{k}": v for k, v in urgent_breads.items()} if urgent_breads else {})},
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:serve_ticket_by_token", serve_msg)

    await redis_helper.add_served_ticket(r, bakery_id, customer_id)

    try:
        await redis_helper.cleanup_urgent_items_for_ticket(
            r,
            bakery_id,
            int(customer_id),
            statuses=("DONE", "PENDING", "PROCESSING"),
        )
    except Exception:
        pass

    urgent_breads = {
        str(uid): {**(item or {}), "is_prepared": True}
        for uid, item in (urgent_breads or {}).items()
    }

    return {
        "original_breads": {"breads": breads_by_name, "is_prepared": True, "note": str(getattr(customer, "note", "") or "")},
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

    breads_by_customer = defaultdict(list)
    for bread_value in all_breads:
        if ':' not in str(bread_value):
            continue
        try:
            parts = str(bread_value).split(':')
            if len(parts) < 2:
                continue
            timestamp_str = parts[0]
            cid_str = parts[-1]
            customer_id = int(cid_str)
            breads_by_customer[customer_id].append(float(timestamp_str))
        except Exception:
            continue

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
        breads_by_customer[int(tid)] = sorted(([0.0] * base_total) + list(existing))

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

    await mqtt_client.call_customer(request, bakery_id, customer_id)
    
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

    db_waitlist_task = tasks.send_ticket_to_wait_list.delay(customer_id, bakery_id, "manual_endpoint")
    logger.info(
        "Queued DB wait-list sync task",
        extra={"bakery_id": int(bakery_id), "customer_id": int(customer_id), "task_id": db_waitlist_task.id},
    )

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
    wait_list_msg = endpoint_helper.format_admin_event_message(
        event_title="Ticket Sent To Wait List",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": customer_id,
            "next_ticket_id": next_ticket_id,
        },
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
    current_served_key = redis_helper.REDIS_KEY_CURRENT_SERVED.format(bakery_id)
    urgent_prep_key = redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id)
    urgent_queue_key = redis_helper.REDIS_KEY_URGENT_QUEUE.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)
    bread_name_key = redis_helper.REDIS_KEY_BREAD_NAMES

    pipe = r.pipeline()
    pipe.get(prep_state_key)
    pipe.get(current_served_key)
    pipe.get(urgent_prep_key)
    pipe.zrange(urgent_queue_key, 0, 0)
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(bread_name_key)
    pipe.zrange(order_key, 0, -1)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')  # Get all breads to count per customer
    pipe.smembers(base_done_key)

    prep_state_str, current_served_raw, urgent_processing_raw, urgent_next_raw, time_per_bread, reservations_map, bread_names_raw, order_ids, all_breads, base_done_raw = await pipe.execute()

    def _as_text(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return v.decode()
        return str(v)

    prep_state_str = _as_text(prep_state_str)
    current_served_id = 0
    current_served_str = _as_text(current_served_raw)
    if current_served_str:
        try:
            current_served_id = int(current_served_str)
        except Exception:
            current_served_id = 0

    # ============================================================
    # PARSE: Convert Redis data to usable format
    # ============================================================
    order_ids = [int(_as_text(x)) for x in order_ids] if order_ids else []
    time_per_bread = {_as_text(k): int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    bread_ids_sorted = sorted(time_per_bread.keys())

    bread_names = {}
    if bread_names_raw:
        for k, v in (bread_names_raw or {}).items():
            kt = _as_text(k)
            if kt is None:
                continue
            try:
                bread_names[str(kt)] = _as_text(v)
            except Exception:
                continue

    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)

    # Count breads already made per customer
    breads_per_customer = {}
    for bread_value in all_breads:
        bread_value = _as_text(bread_value)
        if not bread_value or ':' not in bread_value:
            continue
        try:
            parts = str(bread_value).split(':')
            if len(parts) < 2:
                continue
            cid = int(parts[-1])
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

    urgent_by_ticket = {}
    if time_per_bread:
        urgent_by_ticket = await redis_helper.get_urgent_breads_by_ticket(r, bakery_id, time_per_bread)

    db_breads_cache = {}

    def _get_db_bread_counts(ticket_id: int) -> list[int]:
        tid = int(ticket_id)
        cached = db_breads_cache.get(tid)
        if cached is None:
            with SessionLocal() as db:
                breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [int(tid)])
            cached = breads_map_db.get(int(tid), {})
            db_breads_cache[int(tid)] = cached

        out_counts = []
        for bid in bread_ids_sorted:
            try:
                bid_int = int(bid)
            except Exception:
                bid_int = None
            if bid_int is None:
                out_counts.append(0)
            else:
                out_counts.append(int(cached.get(int(bid_int), 0)))
        return out_counts

    def _to_name_map(counts_by_bread_id: dict) -> dict:
        out = {}
        for bid_raw, count in (counts_by_bread_id or {}).items():
            try:
                bid_int = int(bid_raw)
            except Exception:
                bid_int = None
            name = bread_names.get(str(bid_int), str(bid_raw)) if bid_int is not None else str(bid_raw)
            if int(count) <= 0:
                continue
            out[str(name)] = int(out.get(str(name), 0)) + int(count)
        return out

    def _counts_to_name_map(counts_list: list[int]) -> dict:
        out = {}
        for bid, count in zip(bread_ids_sorted, counts_list[: len(bread_ids_sorted)]):
            if int(count) <= 0:
                continue
            try:
                name = bread_names.get(str(bid), str(bid))
            except Exception:
                name = str(bid)
            out[str(name)] = int(out.get(str(name), 0)) + int(count)
        return out

    def _base_breads_by_name(ticket_id: int) -> dict:
        if not ticket_id or str(ticket_id) not in (reservations_map or {}):
            return {}
        try:
            counts = [int(x) for x in str(reservations_map[str(ticket_id)]).split(',') if str(x) != ""]
        except Exception:
            counts = []

        if counts and all(int(x) == 0 for x in counts):
            counts = _get_db_bread_counts(int(ticket_id))

        out = {}
        for bid, count in zip(bread_ids_sorted, counts):
            if int(count) <= 0:
                continue
            try:
                bid_int = int(bid)
            except Exception:
                bid_int = None
            name = bread_names.get(str(bid_int), str(bid)) if bid_int is not None else str(bid)
            out[str(name)] = int(count)
        return out

    urgent_id = None
    if urgent_processing_raw:
        urgent_id = _as_text(urgent_processing_raw)
    elif urgent_next_raw:
        urgent_id = _as_text(urgent_next_raw[0])

    # Determine active ticket based on prep_state (non-preemptive urgent behavior)
    state_customer_id = None
    state_progress = 0
    state_active = False
    active_normal_customer_id = None
    if prep_state_str and order_ids:
        try:
            parts = str(prep_state_str).split(':', 1)
            state_customer_id = int(parts[0]) if parts and parts[0] else None
            state_progress = int(parts[1]) if len(parts) > 1 and str(parts[1]) != "" else 0
        except ValueError:
            state_customer_id = None

    if state_customer_id and state_customer_id in order_ids and str(state_customer_id) in (reservations_map or {}):
        try:
            counts = list(map(int, reservations_map[str(state_customer_id)].split(',')))
        except Exception:
            counts = []
        needed = sum(counts) if counts else 0
        already_made = max(int(breads_per_customer.get(state_customer_id, 0)), int(state_progress or 0))
        if already_made < needed:
            state_active = True
            active_normal_customer_id = int(state_customer_id)
        else:
            urgent_original_extra = await redis_helper.get_urgent_original_counts_for_ticket(
                r, bakery_id, state_customer_id, time_per_bread
            )
            if urgent_original_extra:
                state_active = True

    note_map: dict[int, str] = {}
    candidate_note_ticket_ids: list[int] = []
    if state_customer_id:
        candidate_note_ticket_ids.append(int(state_customer_id))
    if active_normal_customer_id:
        candidate_note_ticket_ids.append(int(active_normal_customer_id))
    if order_ids:
        candidate_note_ticket_ids.extend([int(x) for x in order_ids])
    candidate_note_ticket_ids = sorted({int(x) for x in candidate_note_ticket_ids if int(x) > 0})
    if candidate_note_ticket_ids:
        with SessionLocal() as db:
            note_map = crud.get_customer_notes_by_ticket_ids_today(db, bakery_id, candidate_note_ticket_ids)

    # If urgent is currently processing / next, only show it if it belongs to the active ticket.
    if urgent_id and time_per_bread and state_active and state_customer_id:
        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
        pipe_u = r.pipeline()
        pipe_u.hget(urgent_item_key, "ticket_id")
        pipe_u.hget(urgent_item_key, "original_breads")
        pipe_u.hget(urgent_item_key, "reason")
        ticket_id_raw, original_raw, reason_raw = await pipe_u.execute()
        reason_text = _as_text(reason_raw) or ""

        if ticket_id_raw and int(ticket_id_raw) == int(state_customer_id):
            original_counts = []
            if original_raw:
                original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
            if len(original_counts) < len(bread_ids_sorted):
                original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))

            if any(int(x) > 0 for x in original_counts[: len(bread_ids_sorted)]):
                urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
                tid = int(ticket_id_raw)
                return {
                    "customer_id": tid,
                    "original_breads": {"breads": _base_breads_by_name(tid), "is_prepared": bool(tid in base_done_ids), "note": str(note_map.get(int(tid), ""))},
                    "urgent_breads": (await _fill_urgent_reasons_from_redis(r, bakery_id, _get_grouped_urgent_breads_for_tickets(bakery_id, [int(tid)], bread_names))).get(int(tid), {}),
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": urgent_id,
                }

    working_customer_preview = None
    if state_active and state_customer_id:
        working_customer_preview = int(state_customer_id)
    elif order_ids:
        for customer_id in order_ids:
            if str(customer_id) not in reservations_map:
                continue
            try:
                counts = list(map(int, reservations_map[str(customer_id)].split(',')))
            except Exception:
                counts = []
            needed = sum(counts) if counts else 0
            already_made = int(breads_per_customer.get(int(customer_id), 0))
            if int(customer_id) in base_done_ids:
                already_made = max(int(already_made), int(needed))
            if int(already_made) < int(needed):
                working_customer_preview = int(customer_id)
                break

    can_show_urgent = False
    if urgent_id and time_per_bread and (not active_normal_customer_id):
        # If we are not in the middle of an already-started normal ticket,
        # urgent must take priority over previewing/starting a new normal ticket.
        # This prevents switching to a newly added ticket while urgent exists.
        can_show_urgent = True

    if can_show_urgent:
        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
        pipe_u = r.pipeline()
        pipe_u.hget(urgent_item_key, "ticket_id")
        pipe_u.hget(urgent_item_key, "original_breads")
        pipe_u.hget(urgent_item_key, "reason")
        ticket_id_raw, original_raw, reason_raw = await pipe_u.execute()
        reason_text = _as_text(reason_raw) or ""

        original_counts = []
        if original_raw:
            original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
        if len(original_counts) < len(bread_ids_sorted):
            original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))

        if any(int(x) > 0 for x in original_counts[: len(bread_ids_sorted)]):
            urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
            tid = int(ticket_id_raw) if ticket_id_raw else 0
            return {
                "customer_id": tid,
                "original_breads": {"breads": _base_breads_by_name(tid) if tid > 0 else {}, "is_prepared": bool(tid > 0 and tid in base_done_ids), "note": str(note_map.get(int(tid), "")) if tid > 0 else ""},
                "urgent_breads": (await _fill_urgent_reasons_from_redis(r, bakery_id, _get_grouped_urgent_breads_for_tickets(bakery_id, [int(tid)], bread_names))).get(int(tid), {}) if tid > 0 else {str(urgent_id): {"breads": _counts_to_name_map(original_counts), "is_prepared": False, "reason": reason_text}},
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
                already_made = max(int(breads_per_customer.get(state_customer_id, 0)), int(state_bread_count or 0))
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

    late_note_ids = []
    if working_customer_preview:
        late_note_ids.append(int(working_customer_preview))
    if working_customer_id:
        late_note_ids.append(int(working_customer_id))
    late_note_ids = [int(x) for x in late_note_ids if int(x) > 0 and int(x) not in note_map]
    if late_note_ids:
        with SessionLocal() as db:
            note_map.update(crud.get_customer_notes_by_ticket_ids_today(db, bakery_id, late_note_ids))

    if working_customer_id:
        tid = int(working_customer_id)
        urgent_for_ticket = urgent_by_ticket.get(int(tid), {}) or {}
        response = {
            "customer_id": tid,
            "original_breads": {"breads": _base_breads_by_name(tid), "is_prepared": bool(tid in base_done_ids), "note": str(note_map.get(int(tid), ""))},
            "urgent_breads": (await _fill_urgent_reasons_from_redis(r, bakery_id, _get_grouped_urgent_breads_for_tickets(bakery_id, [int(tid)], bread_names))).get(int(tid), {}),
            "next_customer": False,
            "urgent": False,
        }
    else:
        if urgent_id and time_per_bread:
            urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
            pipe_u = r.pipeline()
            pipe_u.hget(urgent_item_key, "ticket_id")
            pipe_u.hget(urgent_item_key, "original_breads")
            pipe_u.hget(urgent_item_key, "reason")
            ticket_id_raw, original_raw, reason_raw = await pipe_u.execute()
            reason_text = _as_text(reason_raw) or ""

            original_counts = []
            if original_raw:
                original_counts = [int(x) for x in _as_text(original_raw).split(",") if str(x) != ""]
            if len(original_counts) < len(bread_ids_sorted):
                original_counts = original_counts + [0] * (len(bread_ids_sorted) - len(original_counts))

            if any(int(x) > 0 for x in original_counts[: len(bread_ids_sorted)]):
                urgent_breads = {bid: int(count) for bid, count in zip(bread_ids_sorted, original_counts)}
                tid = int(ticket_id_raw) if ticket_id_raw else 0
                return {
                    "customer_id": tid,
                    "original_breads": {"breads": _base_breads_by_name(tid) if tid > 0 else {}, "is_prepared": bool(tid > 0 and tid in base_done_ids), "note": str(note_map.get(int(tid), "")) if tid > 0 else ""},
                    "urgent_breads": (await _fill_urgent_reasons_from_redis(r, bakery_id, _get_grouped_urgent_breads_for_tickets(bakery_id, [int(tid)], bread_names))).get(int(tid), {}) if tid > 0 else {str(urgent_id): {"breads": _counts_to_name_map(original_counts), "is_prepared": False, "reason": reason_text}},
                    "next_customer": False,
                    "urgent": True,
                    "urgent_id": urgent_id,
                }
        response = {
            "has_customer": False,
            "belongs_to_customer": False,
            "urgent": False,
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

    # 1. BRAIN: Decision & Archive
    await redis_helper.rebuild_prep_state(r, bakery_id)

    # 2. FETCH LOCK
    pipe = r.pipeline()
    pipe.get(redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id))
    pipe.get(redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id))
    prep_raw, u_prep_raw = await pipe.execute()

    def _t(v): return v.decode() if isinstance(v, (bytes, bytearray)) else (str(v) if v else None)
    response = None

    # --- EXECUTION: URGENT ---
    if u_prep_raw:
        urgent_id = _t(u_prep_raw)
        time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
        urgent_state = await redis_helper.consume_one_urgent_bread(r, bakery_id, time_per_bread)
        
        if urgent_state:
            tasks.log_urgent_remaining.delay(bakery_id, urgent_id, urgent_state.get("remaining_by_type") or {}, bool(urgent_state.get("done")))
            
            u_item_key = redis_helper.get_urgent_item_key(bakery_id, urgent_id)
            pipe_u = r.pipeline()
            pipe_u.hget(u_item_key, "ticket_id")
            pipe_u.hget(u_item_key, "original_breads")
            tid_raw, orig_raw = await pipe_u.execute()
            
            # Register Urgent Bread (ticket-linked or standalone as empty ticket=0)
            tid = int(_t(tid_raw)) if tid_raw else 0
            b_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
            b_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)

            pipe_m = r.pipeline()
            pipe_m.get(b_time_key)
            pipe_m.zrevrange(b_key, 0, 0, withscores=True)
            b_time_s_raw, last_b_data = await pipe_m.execute()

            b_time_s = int(b_time_s_raw or 0)
            idx = int(last_b_data[0][1]) + 1 if last_b_data else 1
            now_ts = int(time.time())
            cook_ts = now_ts + b_time_s
            ttl = seconds_until_midnight_iran()

            await r.zadd(b_key, {f"{cook_ts}:{idx}:{tid}": idx})
            await r.expire(b_key, ttl)

            b_ids = sorted(time_per_bread.keys())
            orig_c = [int(x) for x in _t(orig_raw).split(",") if x] if orig_raw else []
            if len(orig_c) < len(b_ids): orig_c += [0] * (len(b_ids) - len(orig_c))
            
            response = {
                "customer_id": int(_t(tid_raw)) if tid_raw else 0,
                "customer_breads": {bid: c for bid, c in zip(b_ids, orig_c)},
                "next_customer": False,
                "urgent": True,
                "urgent_id": urgent_id,
            }

    # --- EXECUTION: NORMAL ---
    elif prep_raw:
        prep_str = _t(prep_raw)
        if prep_str and ':' in prep_str:
            ticket_id, current_progress = map(int, prep_str.split(':'))
            
            # --- Bake Normal Bread ---
            b_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
            b_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
            last_t_key = redis_helper.REDIS_KEY_LAST_BREAD_TIME.format(bakery_id)
            diff_key = redis_helper.REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id)
            
            pipe_m = r.pipeline()
            pipe_m.get(b_time_key)
            pipe_m.zrevrange(b_key, 0, 0, withscores=True)
            pipe_m.get(last_t_key)
            b_time_s_raw, last_b_data, last_ts = await pipe_m.execute()
            
            b_time_s = int(b_time_s_raw or 0)
            idx = int(last_b_data[0][1]) + 1 if last_b_data else 1
            now_ts = int(time.time())
            cook_ts = now_ts + b_time_s
            ttl = seconds_until_midnight_iran()
            
            pipe_w = r.pipeline(transaction=True)
            pipe_w.zadd(b_key, {f"{cook_ts}:{idx}:{ticket_id}": idx})
            pipe_w.expire(b_key, ttl)
            pipe_w.set(last_t_key, now_ts, ex=ttl)
            if last_ts: pipe_w.zadd(diff_key, {str(idx): now_ts - int(float(_t(last_ts)))})
            pipe_w.set(redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id), f"{ticket_id}:{current_progress + 1}", ex=ttl)
            await pipe_w.execute()

            tasks.save_bread_to_db.delay(ticket_id, bakery_id, cook_ts)

            # --- RESPONSE FIX: Send Total (Base + Urgent) Counts ---
            time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
            total_reqs = await redis_helper.get_tickets_total_bread_counts(r, bakery_id, [ticket_id], time_per_bread)
            
            response = {
                "customer_id": ticket_id,
                "customer_breads": total_reqs.get(ticket_id, {}),
                "next_customer": False,
            }

    if response is None:
        response = {"has_customer": False, "belongs_to_customer": False}

    await redis_helper.rebuild_prep_state(r, bakery_id)

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
