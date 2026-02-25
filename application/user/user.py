from fastapi import APIRouter, Request, HTTPException
import json
from fastapi.responses import RedirectResponse
from application.helpers import endpoint_helper, redis_helper, token_helpers
from application.algorithm import Algorithm
from application.auth import decode_token
from application.database import SessionLocal
from application import crud, schemas

router = APIRouter(
    prefix='',
    tags=['user']
)

FILE_NAME = "user:user"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)




async def _calculate_ready_status_for_res(r, bakery_id: int, user_breads: dict | None, bread_time_for_calc: dict, reservation_keys: list, reservation_number: int, reservation_dict_for_calc: dict):
    """Isolated adapter for /res: supports legacy tuple or new dict return from helper."""
    result = await redis_helper.calculate_ready_status(
        r, bakery_id, user_breads, bread_time_for_calc, reservation_keys, reservation_number, reservation_dict_for_calc
    )

    if isinstance(result, dict):
        return (
            bool(result.get("ready", False)),
            bool(result.get("accurate_time", True)),
            result.get("wait_until"),
        )

    if isinstance(result, (list, tuple)) and len(result) >= 3:
        return bool(result[0]), bool(result[1]), result[2]

    raise HTTPException(status_code=500, detail="Unexpected ready-status result format")

def _parse_count_vector(value):
    """Normalize Redis/DB count vector that may be a CSV string or python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [int(x) for x in value]
    if isinstance(value, tuple):
        return [int(x) for x in value]

    txt = str(value).strip()
    if not txt:
        return []

    # Handle serialized list strings like "[1, 2, 0]"
    if txt.startswith("[") and txt.endswith("]"):
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, list):
                return [int(x) for x in parsed]
        except Exception:
            pass

    return [int(x) for x in txt.split(',') if str(x).strip() != ""]


def _as_text(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode()
        except Exception:
            return None
    return str(v)


def _count_breads_by_ticket(all_breads):
    breads_per_customer = {}
    for bread_value in all_breads or []:
        bread_value = _as_text(bread_value)
        if not bread_value or ':' not in bread_value:
            continue
        try:
            parts = str(bread_value).split(':')
            if len(parts) < 2:
                continue
            ticket_id = int(parts[-1])
        except Exception:
            continue
        breads_per_customer[ticket_id] = breads_per_customer.get(ticket_id, 0) + 1
    return breads_per_customer


async def _resolve_current_working_ticket_id(
    r,
    bakery_id: int,
    reservation_dict: dict[int, list[int]],
    all_breads,
    prep_state_raw,
    urgent_processing_raw,
    base_done_raw,
    current_served_raw,
):
    breads_per_customer = _count_breads_by_ticket(all_breads)
    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)

    locked_normal_ticket_id = None
    selected_normal_ticket_id = None
    selected_normal_bread_count = 0
    current_served_id = 0

    current_served_str = _as_text(current_served_raw)
    if current_served_str:
        try:
            current_served_id = int(current_served_str)
        except Exception:
            current_served_id = 0

    prep_state_str = _as_text(prep_state_raw)
    if prep_state_str and ':' in str(prep_state_str):
        try:
            parts = str(prep_state_str).split(':', 1)
            selected_normal_ticket_id = int(parts[0])
            if len(parts) > 1 and str(parts[1]) != "":
                selected_normal_bread_count = int(parts[1])
        except Exception:
            selected_normal_ticket_id = None

    if selected_normal_ticket_id is not None:
        base_needed_total = sum(int(x) for x in (reservation_dict.get(int(selected_normal_ticket_id)) or []))
        baked_total = int(breads_per_customer.get(int(selected_normal_ticket_id), 0))
        baked_total = max(int(baked_total), int(selected_normal_bread_count or 0))
        if int(selected_normal_ticket_id) in base_done_ids:
            baked_total = max(int(baked_total), int(base_needed_total))
        started_normal = int(baked_total) > 0 or (
            int(current_served_id or 0) > 0 and int(current_served_id) == int(selected_normal_ticket_id)
        )
        if int(base_needed_total) > 0 and baked_total < int(base_needed_total) and started_normal:
            locked_normal_ticket_id = int(selected_normal_ticket_id)

    urgent_processing_id = _as_text(urgent_processing_raw)
    if locked_normal_ticket_id is not None:
        return int(locked_normal_ticket_id)

    if urgent_processing_id:
        urgent_item_key = redis_helper.get_urgent_item_key(bakery_id, str(urgent_processing_id))
        ticket_id_raw = await r.hget(urgent_item_key, "ticket_id")
        try:
            return int(_as_text(ticket_id_raw)) if ticket_id_raw else None
        except Exception:
            return None

    return selected_normal_ticket_id

@router.get('/')
async def root(): return RedirectResponse('/home')

@router.api_route('/home', methods=['POST', 'GET'])
@handle_errors
async def home(request: Request):
    data = decode_token(request)
    return {'status': 'OK', 'data': data}


@router.get("/res/{bakery_id}/{token_value}")
@handle_errors
async def queue_check(
    request: Request,
    bakery_id: int,
    token_value: str,
):
    """Public queue status endpoint that resolves the customer by daily token."""
    r = request.app.state.redis

    with SessionLocal() as db:
        customer = crud.get_customer_by_token_today(db, bakery_id, token_value)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for token")

    t = customer.ticket_id
    customer_id = customer.id

    # Redis keys
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    urgent_prep_key = redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id)
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)
    current_served_key = redis_helper.REDIS_KEY_CURRENT_SERVED.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    pipe.hget(wait_list_key, t)
    pipe.sismember(served_key, t)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    pipe.get(prep_state_key)
    pipe.get(urgent_prep_key)
    pipe.smembers(base_done_key)
    pipe.get(current_served_key)
    time_per_bread_raw, reservations_map, bread_names_raw, wait_list_hit, is_served_flag, all_breads, prep_state_raw, urgent_processing_raw, base_done_raw, current_served_raw = await pipe.execute()

    bread_time = {int(k): int(v) for k, v in time_per_bread_raw.items()}
    reservation_dict = {
        int(k): _parse_count_vector(v) for k, v in reservations_map.items()
    }
    bread_names = {int(k): v for k, v in bread_names_raw.items()}
    bread_ids_sorted = sorted(bread_time.keys())

    if not bread_time:
        return {'msg': 'bakery does not exist or does not have any bread'}

    # First, report served or wait-list status (200) if applicable
    is_served = bool(is_served_flag)
    if is_served:
        return {
            "message": "TICKET_IS_SERVED",
            "ticket_id": t,
            "customer_id": customer_id,
            "rated": customer.rating is not None,
            "rating": customer.rating,
        }

    if wait_list_hit is not None:
        wait_list_counts = _parse_count_vector(wait_list_hit)
        user_breads_persian = {
            bread_names.get(bid, str(bid)): count
            for bid, count in zip(bread_ids_sorted, wait_list_counts)
        }
        return {
            "message": "TICKET_IS_IN_WAIT_LIST",
            "ticket_id": t,
            "customer_id": customer_id,
            "user_breads": user_breads_persian,
        }

    if not reservation_dict:
        return {'msg': 'queue is empty'}

    reservation_keys = sorted(reservation_dict.keys())
    algorithm_instance = Algorithm()

    current_ticket_id = await _resolve_current_working_ticket_id(
        r=r,
        bakery_id=bakery_id,
        reservation_dict=reservation_dict,
        all_breads=all_breads,
        prep_state_raw=prep_state_raw,
        urgent_processing_raw=urgent_processing_raw,
        base_done_raw=base_done_raw,
        current_served_raw=current_served_raw,
    )

    is_user_exist = t in reservation_keys

    if not is_user_exist:
        raise HTTPException(status_code=404, detail="Ticket does not Exist")

    reservation_number = t if is_user_exist else reservation_keys[-1]

    people_in_queue = sum(1 for key in reservation_keys if key < reservation_number)

    average_bread_time = sum(bread_time.values()) // len(bread_time)

    bread_ids_sorted = sorted(bread_time.keys())
    time_per_bread_list = [bread_time[k] for k in bread_ids_sorted]

    in_queue_customers_time = await algorithm_instance.calculate_in_queue_customers_time(
        reservation_keys,
        reservation_number,
        reservation_dict,
        time_per_bread_list,
        r=r,
        bakery_id=bakery_id
    )

    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        reservation_keys,
        reservation_number,
        reservation_dict
    ) // 2 * average_bread_time

    urgent_by_ticket = await redis_helper.get_urgent_breads_by_ticket(r, bakery_id, {str(k): int(v) for k, v in bread_time.items()})
    urgent_breads = urgent_by_ticket.get(int(t), {})

    user_breads_persian = user_breads = None
    if is_user_exist:

        calc_counts = reservation_dict[reservation_number]
        calc_user_breads = {bid: count for bid, count in zip(bread_ids_sorted, calc_counts)}

        display_counts = calc_counts
        if display_counts and all(int(x) == 0 for x in display_counts):
            with SessionLocal() as db:
                breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [reservation_number])
            bread_counts = breads_map_db.get(reservation_number, {})
            display_counts = [int(bread_counts.get(int(bid), 0)) for bid in bread_ids_sorted]

        user_breads_persian = {
            bread_names.get(bid, str(bid)): count
            for bid, count in zip(bread_ids_sorted, display_counts)
        }
        user_breads = {
            bid: count
            for bid, count in zip(bread_ids_sorted, calc_counts)
        }

    reservation_dict_for_calc = {
        int(k): (",".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))
        for k, v in reservation_dict.items()
    }

    bread_time_for_calc = {str(k): int(v) for k, v in bread_time.items()}

    ready, accurate_time, wait_until = await _calculate_ready_status_for_res(
        r, bakery_id, user_breads, bread_time_for_calc, reservation_keys, reservation_number, reservation_dict_for_calc
    )

    return {
        "ready": ready,
        "accurate_time": accurate_time,
        "wait_until": wait_until,
        "people_in_queue": people_in_queue,
        "empty_slot_time_avg": empty_slot_time,
        "in_queue_customers_time": in_queue_customers_time,
        "user_breads": user_breads_persian,
        "urgent_breads": urgent_breads,
        "current_ticket_id": current_ticket_id,
        "ticket_id": t,
        "customer_id": customer_id,
    }


@router.post("/rate")
@handle_errors
async def rate_customer(payload: schemas.RateRequest):
    with SessionLocal() as db:
        customer = crud.set_customer_rating(db, payload.customer_id, payload.rate)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    rate_msg = (
        f"Bakery ID: {customer.bakery_id}"
        f"\nTicket Number: {customer.ticket_id}"
        f"\nCustomer ID: {customer.id}"
        f"\nRate: {payload.rate}"
    )

    await endpoint_helper.report_to_admin("rate", f"{FILE_NAME}:rate_customer", rate_msg)

    return {
        "status": "OK" 
    }


@router.get("/queue_until_ticket_summary/{bakery_id}/{token_value}")
@handle_errors
async def queue_until_ticket_summary(
    request: Request,
    bakery_id: int,
    token_value: str,
):
    """Public endpoint: summary of queue up to and including ticket for a token."""
    r = request.app.state.redis

    with SessionLocal() as db:
        customer = crud.get_customer_by_token_today(db, bakery_id, token_value)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for token")

    t = customer.ticket_id

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hget(wait_list_key, t)
    pipe.sismember(served_key, t)
    time_per_bread_raw, reservations_map, wait_list_hit, is_served_flag = await pipe.execute()

    if not time_per_bread_raw:
        return {'msg': 'bakery does not exist or does not have any bread'}

    # First, report served or wait-list status (200) if applicable
    is_served = bool(is_served_flag)
    if is_served:
        return {
            "message": "TICKET_IS_SERVED",
            "ticket_id": t,
        }

    if wait_list_hit is not None:
        return {
            "message": "TICKET_IS_IN_WAIT_LIST",
            "ticket_id": t,
        }

    if not reservations_map:
        # Queue is empty, but user might be in wait list
        return {'msg': 'queue is empty'}

    reservation_dict = {
        int(k): _parse_count_vector(v) for k, v in reservations_map.items()
    }

    reservation_keys = sorted(reservation_dict.keys())

    if t not in reservation_keys:
        raise HTTPException(status_code=404, detail="Ticket does not Exist")

    included_tickets = [key for key in reservation_keys if key < t]

    people_in_queue_until_this_ticket = len(included_tickets)
    tickets_and_their_bread_count = {
        str(key): sum(reservation_dict[key]) for key in included_tickets
    }

    return {
        "people_in_queue_until_this_ticket": people_in_queue_until_this_ticket,
        "tickets_and_their_bread_count": tickets_and_their_bread_count,
    }


@router.get("/queue_all_ticket_summary/{bakery_id}")
@handle_errors
async def queue_all_ticket_summary(
    request: Request,
    bakery_id: int,
):
    """Public endpoint: summary of entire queue (all tickets) for a bakery."""
    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    urgent_prep_key = redis_helper.REDIS_KEY_URGENT_PREP_STATE.format(bakery_id)
    base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)
    current_served_key = redis_helper.REDIS_KEY_CURRENT_SERVED.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    pipe.hgetall(wait_list_key)
    pipe.smembers(served_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    pipe.get(prep_state_key)
    pipe.get(urgent_prep_key)
    pipe.smembers(base_done_key)
    pipe.get(current_served_key)
    time_per_bread_raw, reservations_map, bread_names_raw, wait_list_map, served_set, all_breads, prep_state_raw, urgent_processing_raw, base_done_raw, current_served_raw = await pipe.execute()

    if not time_per_bread_raw:
        return {'msg': 'bakery does not exist or does not have any bread'}

    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    } if reservations_map else {}

    wait_list_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in wait_list_map.items()
    } if wait_list_map else {}

    reservation_keys = sorted(reservation_dict.keys())

    bread_ids_sorted = sorted(int(k) for k in time_per_bread_raw.keys())
    bread_names = {int(k): v for k, v in bread_names_raw.items()} if bread_names_raw else {}

    served_ids = set(int(x) for x in served_set) if served_set else set()
    wait_list_ids = set(wait_list_dict.keys())

    all_ticket_ids = sorted(set(reservation_keys) | wait_list_ids | served_ids)

    if not all_ticket_ids:
        return {'msg': 'queue is empty'}

    with SessionLocal() as db:
        token_map = crud.get_customer_tokens_by_ticket_ids_today(db, bakery_id, all_ticket_ids)
        note_map = crud.get_customer_notes_by_ticket_ids_today(db, bakery_id, all_ticket_ids)
        breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, all_ticket_ids)
        urgent_rows = crud.get_today_urgent_bread_logs(db, bakery_id)

    breads_per_customer = _count_breads_by_ticket(all_breads)

    base_done_ids = set(int(_as_text(x)) for x in (base_done_raw or []) if _as_text(x) is not None)

    urgent_active_by_ticket = await redis_helper.get_urgent_breads_by_ticket(
        r, bakery_id, {str(k): int(v) for k, v in time_per_bread_raw.items()}
    )

    current_working_ticket_id = await _resolve_current_working_ticket_id(
        r=r,
        bakery_id=bakery_id,
        reservation_dict=reservation_dict,
        all_breads=all_breads,
        prep_state_raw=prep_state_raw,
        urgent_processing_raw=urgent_processing_raw,
        base_done_raw=base_done_raw,
        current_served_raw=current_served_raw,
    )

    all_ticket_ids_set = set(int(x) for x in all_ticket_ids)
    if current_working_ticket_id is not None and int(current_working_ticket_id) not in all_ticket_ids_set:
        current_working_ticket_id = None
    def _safe_json_map(raw_value):
        if not raw_value:
            return {}
        try:
            payload = json.loads(raw_value)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    urgent_grouped_by_ticket = {}
    for row in urgent_rows or []:
        if str(getattr(row, "status", "")) == "CANCELLED":
            continue
        if getattr(row, "ticket_id", None) is None:
            continue
        try:
            tid_int = int(row.ticket_id)
        except Exception:
            continue
        if tid_int not in all_ticket_ids_set:
            continue

        urgent_map = _safe_json_map(getattr(row, "original_breads_json", None))
        named = {}
        for bid_raw, count in (urgent_map or {}).items():
            try:
                c = int(count)
            except Exception:
                c = 0
            if c <= 0:
                continue
            try:
                bid_int = int(bid_raw)
            except Exception:
                bid_int = None
            key = bread_names.get(int(bid_int), str(bid_int)) if bid_int is not None else str(bid_raw)
            named[str(key)] = int(named.get(str(key), 0)) + int(c)

        if named:
            urgent_grouped_by_ticket.setdefault(int(tid_int), {})[str(getattr(row, "urgent_id", ""))] = {
                "breads": named,
                "is_prepared": str(getattr(row, "status", "")) == "DONE",
                "reason": "",
            }

    urgent_ids_all = []
    for urgent_map in (urgent_grouped_by_ticket or {}).values():
        urgent_ids_all.extend([str(uid) for uid in (urgent_map or {}).keys()])
    if urgent_ids_all:
        pipe_reason = r.pipeline()
        for uid in urgent_ids_all:
            pipe_reason.hget(redis_helper.get_urgent_item_key(bakery_id, uid), "reason")
        reason_rows = await pipe_reason.execute()
        for uid, reason_raw in zip(urgent_ids_all, reason_rows):
            reason_text = _as_text(reason_raw) or ""
            if not reason_text:
                continue
            for ticket_map in (urgent_grouped_by_ticket or {}).values():
                if uid in ticket_map:
                    ticket_map[uid]["reason"] = reason_text
                    break

    result = {}
    for ticket_id in all_ticket_ids:
        counts = reservation_dict.get(ticket_id) or wait_list_dict.get(ticket_id)
        breads_by_name = {}

        urgent_breads = urgent_grouped_by_ticket.get(int(ticket_id), {})

        base_needed_total = 0
        if counts is not None:
            if counts and all(int(x) == 0 for x in counts):
                # This ticket's base breads are already complete (typically returned from wait list
                # due to urgent injection). Keep base breads visible for UI, but do not count them
                # as still-needed for readiness.
                base_needed_total = 0
            else:
                base_needed_total = sum(int(x) for x in counts)
        else:
            bread_counts = breads_map_db.get(ticket_id, {})
            base_needed_total = sum(int(v) for v in bread_counts.values())

        urgent_needed_total = 0
        urgent_active_raw = urgent_active_by_ticket.get(int(ticket_id), {})
        if urgent_active_raw:
            urgent_needed_total = sum(int(v) for v in urgent_active_raw.values())

        needed_total = int(base_needed_total) + int(urgent_needed_total)

        baked_total = int(breads_per_customer.get(int(ticket_id), 0))
        if int(ticket_id) in base_done_ids:
            baked_total = int(baked_total) + int(base_needed_total)

        has_active_urgent = bool(urgent_active_raw)

        if ticket_id in served_ids:
            status = "TICKET_IS_SERVED"
        elif ticket_id in wait_list_ids:
            status = "TICKET_IS_IN_WAIT_LIST"
        elif current_working_ticket_id is not None and int(ticket_id) == int(current_working_ticket_id):
            status = "CURRENTLY_WORKING"
        elif has_active_urgent:
            # If urgent breads still exist for this ticket, do not mark all prepared
            # solely from raw bread counters; keep queue-based state for UI.
            status = "IN_QUEUE"
        elif baked_total >= max(int(needed_total), 0) and int(needed_total) > 0:
            status = "ALL_BREADS_PREPARED"
        else:
            status = "IN_QUEUE"

        if counts is not None:
            if len(counts) != len(bread_ids_sorted):
                raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

            display_counts = counts
            if display_counts and all(int(x) == 0 for x in display_counts):
                bread_counts = breads_map_db.get(ticket_id, {})
                display_counts = [int(bread_counts.get(int(bid), 0)) for bid in bread_ids_sorted]

            for bid, count in zip(bread_ids_sorted, display_counts):
                if int(count) <= 0:
                    continue
                breads_by_name[bread_names.get(int(bid), str(bid))] = int(count)
        else:
            # Usually served tickets: reservation was removed from Redis.
            bread_counts = breads_map_db.get(ticket_id, {})
            for bid, count in bread_counts.items():
                if int(count) <= 0:
                    continue
                breads_by_name[bread_names.get(int(bid), str(bid))] = int(count)

        original_is_prepared = bool(int(ticket_id) in base_done_ids or ticket_id in served_ids or ticket_id in wait_list_ids or status == "ALL_BREADS_PREPARED")

        result[str(ticket_id)] = {
            "token": token_map.get(ticket_id),
            "original_breads": {
                "breads": breads_by_name,
                "is_prepared": original_is_prepared,
                "note": str(note_map.get(int(ticket_id), "")),
            },
            "urgent_breads": urgent_breads,
            "status": status,
        }

    return result
