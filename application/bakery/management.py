from fastapi import APIRouter, Depends, HTTPException, Request
from application.logger_config import logger
from sqlalchemy.orm import Session
from application.helpers.general_helpers import seconds_until_midnight_iran
from application.helpers import database_helper, endpoint_helper, redis_helper
from application import mqtt_client, crud, schemas, tasks
from application import models
from sqlalchemy.exc import IntegrityError
import json
from datetime import datetime, time
import pytz

FILE_NAME = "bakery:management"
handle_errors = endpoint_helper.db_transaction(FILE_NAME)

router = APIRouter(
    prefix='/manage',
    tags=['management']
)

def require_admin(
    request: Request,
    db: Session = Depends(endpoint_helper.get_db)
):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    is_admin = crud.is_user_admin(db, user_id)
    if not is_admin or not is_admin.active:
        raise HTTPException(status_code=403, detail="Admin access only")

    return user_id


@router.post('/urgent/inject')
@handle_errors
async def urgent_inject(
        request: Request,
        payload: schemas.UrgentInjectRequirement,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = int(payload.bakery_id)
    ticket_id = int(payload.ticket_id) if payload.ticket_id is not None else None
    bread_requirements = payload.bread_requirements

    if any(int(v) < 0 for v in bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(int(v) for v in bread_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Urgent item should have at least one bread")

    r = request.app.state.redis
    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    if set(time_per_bread.keys()) != set(bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="Invalid bread types")

    if ticket_id is not None:
        customer = crud.get_customer_by_ticket_id_any_status(db, ticket_id, bakery_id)
        if not customer:
            raise HTTPException(status_code=404, detail={"error": "Ticket does not exist"})

        bread_ids_sorted = sorted(time_per_bread.keys())
        bread_counts_db = {int(b.bread_type_id): int(b.count) for b in (customer.bread_associations or [])}
        encoded_reservation_db = ",".join(
            str(int(bread_counts_db.get(int(bid), 0)))
            for bid in bread_ids_sorted
        )

        def _is_all_zeros(reservation_str) -> bool:
            if not reservation_str:
                return False
            try:
                parts = [p for p in str(reservation_str).split(",") if str(p) != ""]
                return bool(parts) and all(int(x) == 0 for x in parts)
            except Exception:
                return False

        res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
        order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
        wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
        served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
        base_done_key = redis_helper.REDIS_KEY_BASE_DONE.format(bakery_id)
        ttl = seconds_until_midnight_iran()

        pipe_check = r.pipeline()
        pipe_check.hexists(res_key, str(ticket_id))
        pipe_check.hget(res_key, str(ticket_id))
        pipe_check.hget(wait_list_key, str(ticket_id))
        pipe_check.sismember(base_done_key, str(ticket_id))
        in_queue, existing_reservation, wait_list_reservation, is_base_done = await pipe_check.execute()

        effective_reservation = None
        if wait_list_reservation and (not _is_all_zeros(wait_list_reservation)):
            effective_reservation = str(wait_list_reservation)
        elif existing_reservation and (not _is_all_zeros(existing_reservation)):
            effective_reservation = str(existing_reservation)
        else:
            effective_reservation = str(encoded_reservation_db)

        pipe = r.pipeline(transaction=True)
        pipe.srem(served_key, int(ticket_id))
        pipe.hdel(wait_list_key, str(ticket_id))
        if effective_reservation is not None:
            pipe.hset(res_key, str(ticket_id), effective_reservation)
        if bool(wait_list_reservation) or bool(is_base_done):
            pipe.sadd(base_done_key, str(ticket_id))
        pipe.zadd(order_key, {str(ticket_id): int(ticket_id)})
        pipe.expire(res_key, ttl)
        pipe.expire(order_key, ttl)
        pipe.expire(wait_list_key, ttl)
        pipe.expire(base_done_key, ttl)
        await pipe.execute()

        crud.update_customer_status_to_true(db, ticket_id, bakery_id)
        crud.update_wait_list_customer_status(db, ticket_id, bakery_id, False)
        db.commit()

    urgent_id = await redis_helper.create_urgent_item(
        r,
        bakery_id,
        ticket_id,
        bread_requirements,
        time_per_bread=time_per_bread,
    )

    tasks.log_urgent_inject.delay(bakery_id, urgent_id, ticket_id, bread_requirements)

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:urgent_inject", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
        "ticket_id": ticket_id,
        "bread_requirements": bread_requirements,
    })

    urgent_msg = endpoint_helper.format_admin_event_message(
        event_title="Urgent Bread Injected",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": ticket_id,
            "urgent_id": urgent_id,
        },
        bread_requirements=bread_requirements,
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:urgent_inject", urgent_msg)

    return {
        "status": "OK",
        "urgent_id": urgent_id,
    }


@router.put('/urgent/edit')
@handle_errors
async def urgent_edit(
        request: Request,
        payload: schemas.UrgentEditRequirement,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = int(payload.bakery_id)
    urgent_id = str(payload.urgent_id)
    bread_requirements = payload.bread_requirements

    if any(int(v) < 0 for v in bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(int(v) for v in bread_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Urgent item should have at least one bread")

    r = request.app.state.redis
    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    if set(time_per_bread.keys()) != set(bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="Invalid bread types")

    ok = await redis_helper.update_urgent_item_if_pending(r, bakery_id, urgent_id, bread_requirements, time_per_bread)
    if not ok:
        raise HTTPException(status_code=400, detail={"error": "Urgent item cannot be edited (not found or not pending)"})

    tasks.log_urgent_edit.delay(bakery_id, urgent_id, bread_requirements)

    logger.info(f"{FILE_NAME}:urgent_edit", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
        "bread_requirements": bread_requirements,
    })

    urgent_edit_msg = endpoint_helper.format_admin_event_message(
        event_title="Urgent Bread Edited",
        fields={"bakery_id": bakery_id, "urgent_id": urgent_id},
        bread_requirements=bread_requirements,
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:urgent_edit", urgent_edit_msg)

    return {"status": "OK"}


@router.delete('/urgent/delete')
@handle_errors
async def urgent_delete(
        request: Request,
        payload: schemas.UrgentDeleteRequirement,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = int(payload.bakery_id)
    urgent_id = str(payload.urgent_id)

    r = request.app.state.redis
    ok = await redis_helper.delete_urgent_item_if_pending(r, bakery_id, urgent_id)
    if not ok:
        raise HTTPException(status_code=400, detail={"error": "Urgent item cannot be deleted (not found or not pending)"})

    tasks.log_urgent_cancel.delay(bakery_id, urgent_id)

    logger.info(f"{FILE_NAME}:urgent_delete", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
    })

    urgent_delete_msg = endpoint_helper.format_admin_event_message(
        event_title="Urgent Bread Deleted",
        fields={"bakery_id": bakery_id, "urgent_id": urgent_id},
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:urgent_delete", urgent_delete_msg)

    return {"status": "OK"}


@router.get('/urgent/list/{bakery_id}')
@handle_errors
async def urgent_list(
        bakery_id: int,
        request: Request,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = int(bakery_id)
    r = request.app.state.redis
    items = await redis_helper.list_urgent_items(r, bakery_id)
    return {"items": items}


@router.post('/reset_today')
@handle_errors
async def reset_today(
        request: Request,
        payload: schemas.BakeryID,
        confirm: bool = False,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    if not confirm:
        raise HTTPException(status_code=400, detail={"error": "confirmation_required"})

    bakery_id = int(payload.bakery_id)

    tehran = pytz.timezone('Asia/Tehran')
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    breads_deleted = (
        db.query(models.Bread)
        .filter(models.Bread.bakery_id == bakery_id, models.Bread.enter_date >= midnight_utc)
        .delete(synchronize_session=False)
    )

    urgent_deleted = (
        db.query(models.UrgentBreadLog)
        .filter(models.UrgentBreadLog.bakery_id == bakery_id, models.UrgentBreadLog.register_date >= midnight_utc)
        .delete(synchronize_session=False)
    )

    snapshots_deleted = (
        db.query(models.QueueStateSnapshot)
        .filter(models.QueueStateSnapshot.bakery_id == bakery_id, models.QueueStateSnapshot.register_date >= midnight_utc)
        .delete(synchronize_session=False)
    )

    customers_deleted = (
        db.query(models.Customer)
        .filter(models.Customer.bakery_id == bakery_id, models.Customer.register_date >= midnight_utc)
        .delete(synchronize_session=False)
    )

    crud.update_all_customers_status_to_false(db, bakery_id)

    db.commit()

    r = request.app.state.redis
    await redis_helper.purge_bakery_data(r, bakery_id)
    await redis_helper.initialize_redis_sets(r, bakery_id)
    await redis_helper.initialize_redis_sets_only_12_oclock(r, bakery_id)

    await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
    await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)

    logger.info(f"{FILE_NAME}:reset_today", extra={
        "bakery_id": bakery_id,
        "customers_deleted": int(customers_deleted or 0),
        "breads_deleted": int(breads_deleted or 0),
        "urgent_deleted": int(urgent_deleted or 0),
        "snapshots_deleted": int(snapshots_deleted or 0),
    })

    reset_msg = endpoint_helper.format_admin_event_message(
        event_title="Bakery Reset Today",
        fields={
            "bakery_id": bakery_id,
            "customers_deleted": int(customers_deleted or 0),
            "breads_deleted": int(breads_deleted or 0),
            "urgent_deleted": int(urgent_deleted or 0),
            "snapshots_deleted": int(snapshots_deleted or 0),
        },
    )
    await endpoint_helper.report_to_admin("warning", f"{FILE_NAME}:reset_today", reset_msg)

    return {
        "status": "OK",
        "customers_deleted": int(customers_deleted or 0),
        "breads_deleted": int(breads_deleted or 0),
        "urgent_deleted": int(urgent_deleted or 0),
        "snapshots_deleted": int(snapshots_deleted or 0),
    }


@router.get('/urgent/history/{bakery_id}')
@handle_errors
async def urgent_history(
        bakery_id: int,
        request: Request,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    def _safe_json_map(raw_value):
        if not raw_value:
            return {}
        try:
            payload = json.loads(raw_value)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _sum_counts(m: dict) -> int:
        total = 0
        for v in (m or {}).values():
            try:
                total += int(v)
            except Exception:
                continue
        return int(total)

    bakery_id = int(bakery_id)
    rows = crud.get_today_urgent_bread_logs(db, bakery_id)
    items = []
    for row in rows:
        urgent_breads = _safe_json_map(row.original_breads_json)
        remaining_map = _safe_json_map(row.remaining_breads_json)

        remaining_total = _sum_counts(remaining_map)
        total_required = _sum_counts(urgent_breads)
        already_cooked = max(0, int(total_required) - int(remaining_total))

        items.append({
            "urgent_id": row.urgent_id,
            "bakery_id": int(row.bakery_id),
            "ticket_id": int(row.ticket_id) if row.ticket_id is not None else 0,
            "status": row.status,
            "urgent_breads": urgent_breads,
            "already_cooked": int(already_cooked),
            "remaining": int(remaining_total),
            "register_date": row.register_date.isoformat() if row.register_date else None,
            "update_date": row.update_date.isoformat() if row.update_date else None,
            "done_date": row.done_date.isoformat() if row.done_date else None,
            "cancel_date": row.cancel_date.isoformat() if row.cancel_date else None,
        })
    return {"items": items}


@router.put('/modify_ticket')
@handle_errors
async def modify_ticket(
        request: Request,
        payload: schemas.ModifyTicketRequirement,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = payload.bakery_id
    customer_ticket_id = payload.customer_ticket_id
    bread_requirements = payload.bread_requirements

    if any(v < 0 for v in bread_requirements.values()):
        raise HTTPException(status_code=400, detail="Bread values cannot be negative")

    if sum(bread_requirements.values()) <= 0:
        raise HTTPException(status_code=400, detail="Ticket should have at least one bread")

    r = request.app.state.redis

    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    if set(time_per_bread.keys()) != set(bread_requirements.keys()):
        raise HTTPException(status_code=400, detail="Invalid bread types")

    bread_ids = list(time_per_bread.keys())
    encoded_reservation = ",".join(str(int(bread_requirements.get(bid, 0))) for bid in bread_ids)

    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    all_breads = await r.zrangebyscore(breads_key, '-inf', '+inf')
    baked_count = 0
    suffix = f":{customer_ticket_id}"
    for bread_value in all_breads:
        if isinstance(bread_value, bytes):
            try:
                bread_value = bread_value.decode()
            except Exception:
                continue

        if isinstance(bread_value, str) and bread_value.endswith(suffix):
            baked_count += 1

    if baked_count > 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Ticket has started baking and cannot be modified",
                "baked_count": baked_count,
            }
        )

    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)

    pipe0 = r.pipeline()
    pipe0.hexists(res_key, str(customer_ticket_id))
    pipe0.hexists(wait_list_key, str(customer_ticket_id))
    pipe0.sismember(served_key, int(customer_ticket_id))
    pipe0.hget(res_key, str(customer_ticket_id))
    in_queue, in_wait_list, is_served, current_reservation_raw = await pipe0.execute()

    if bool(is_served):
        raise HTTPException(status_code=400, detail={"error": "Ticket is already served"})

    if not (in_queue or in_wait_list):
        await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        await redis_helper.get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        pipe_retry = r.pipeline()
        pipe_retry.hexists(res_key, str(customer_ticket_id))
        pipe_retry.hexists(wait_list_key, str(customer_ticket_id))
        pipe_retry.hget(res_key, str(customer_ticket_id))
        in_queue, in_wait_list, current_reservation_raw = await pipe_retry.execute()

    if not (in_queue or in_wait_list):
        raise HTTPException(status_code=404, detail={"error": "Ticket does not exist"})

    if bool(in_wait_list):
        raise HTTPException(status_code=400, detail={"error": "Ticket is in wait list and cannot be modified"})

    queue_state = await redis_helper.load_queue_state(r, bakery_id)
    ticket_state = queue_state.tickets.get(int(customer_ticket_id)) if getattr(queue_state, "tickets", None) else None
    original_ticket_kind = ticket_state.kind if ticket_state and getattr(ticket_state, "kind", None) in ("single", "multi") else None

    def _safe_total_from_reservation(raw_value) -> int | None:
        if raw_value is None:
            return None
        try:
            txt = raw_value.decode() if isinstance(raw_value, (bytes, bytearray)) else str(raw_value)
            counts = [int(x) for x in str(txt).split(',') if str(x) != ""]
            return int(sum(counts))
        except Exception:
            return None

    old_total_breads = _safe_total_from_reservation(current_reservation_raw)
    if old_total_breads is None:
        breads_map_db = crud.get_customer_breads_by_ticket_ids_today(db, bakery_id, [int(customer_ticket_id)])
        old_total_breads = int(sum((breads_map_db.get(int(customer_ticket_id), {}) or {}).values()))

    new_total_breads = int(sum(int(v) for v in bread_requirements.values()))
    if str(original_ticket_kind) == "single" and int(old_total_breads or 0) == 1 and int(new_total_breads) > 1:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Single-bread ticket cannot be modified to multiple breads",
                "current_total_breads": int(old_total_breads or 0),
                "requested_total_breads": int(new_total_breads),
                "original_ticket_kind": str(original_ticket_kind),
            },
        )

    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    order_ids = []
    reservations_map = {}
    prep_state_str = None

    pipe_work = r.pipeline()
    pipe_work.get(prep_state_key)
    pipe_work.hgetall(res_key)
    pipe_work.zrange(order_key, 0, -1)
    prep_state_raw, reservations_map, order_ids = await pipe_work.execute()

    if isinstance(prep_state_raw, bytes):
        try:
            prep_state_str = prep_state_raw.decode()
        except Exception:
            prep_state_str = None
    elif prep_state_raw is not None:
        prep_state_str = str(prep_state_raw)

    if not reservations_map or not order_ids:
        await redis_helper.get_bakery_reservations(
            r,
            bakery_id,
            fetch_from_redis_first=False,
            bakery_time_per_bread=time_per_bread,
        )
        pipe_work_retry = r.pipeline()
        pipe_work_retry.get(prep_state_key)
        pipe_work_retry.hgetall(res_key)
        pipe_work_retry.zrange(order_key, 0, -1)
        prep_state_raw, reservations_map, order_ids = await pipe_work_retry.execute()
        if isinstance(prep_state_raw, bytes):
            try:
                prep_state_str = prep_state_raw.decode()
            except Exception:
                prep_state_str = None
        elif prep_state_raw is not None:
            prep_state_str = str(prep_state_raw)

    def _as_text(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return v.decode()
        return str(v)

    order_ids = [int(_as_text(x)) for x in order_ids] if order_ids else []
    reservations_map = {_as_text(k): _as_text(v) for k, v in (reservations_map or {}).items()}

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

    def _get_customer_needs(ticket_id: int):
        if not ticket_id or str(ticket_id) not in reservations_map:
            return 0
        try:
            counts = list(map(int, reservations_map[str(ticket_id)].split(',')))
        except Exception:
            return 0
        return sum(counts)

    def _get_queue_working_customer():
        if prep_state_str and order_ids:
            try:
                state_customer_id, _ = map(int, str(prep_state_str).split(':'))
            except Exception:
                state_customer_id = None
            if state_customer_id and state_customer_id in order_ids and str(state_customer_id) in reservations_map:
                needed = _get_customer_needs(state_customer_id)
                already_made = breads_per_customer.get(state_customer_id, 0)
                if already_made < needed:
                    return state_customer_id

        for cid in order_ids:
            if str(cid) not in reservations_map:
                continue
            needed = _get_customer_needs(cid)
            already_made = breads_per_customer.get(cid, 0)
            if already_made < needed:
                return cid
        return None

    working_customer_id = _get_queue_working_customer()
    if working_customer_id is not None and int(working_customer_id) == int(customer_ticket_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Ticket is currently being prepared and cannot be modified",
                "customer_ticket_id": int(customer_ticket_id),
            },
        )

    ttl = seconds_until_midnight_iran()
    pipe = r.pipeline()
    pipe.hset(res_key, str(customer_ticket_id), encoded_reservation)
    pipe.zadd(order_key, {str(customer_ticket_id): int(customer_ticket_id)})
    pipe.expire(res_key, ttl)
    pipe.expire(order_key, ttl)
    await pipe.execute()

    upcoming_breads = await redis_helper.get_bakery_upcoming_breads(r, bakery_id)
    if upcoming_breads:
        should_be_upcoming = any((bid in upcoming_breads and int(count) > 0) for bid, count in bread_requirements.items())
        if should_be_upcoming:
            await redis_helper.maybe_add_customer_to_upcoming_zset(
                r, bakery_id, customer_ticket_id, bread_requirements, upcoming_members=set(upcoming_breads)
            )
        else:
            await redis_helper.remove_customer_from_upcoming_customers(r, bakery_id, customer_ticket_id)

    ok = crud.update_customer_breads_for_ticket_today(db, bakery_id, customer_ticket_id, bread_requirements)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "Customer not found"})

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:modify_ticket", extra={
        "bakery_id": bakery_id,
        "customer_ticket_id": customer_ticket_id,
        "in_queue": bool(in_queue),
        "in_wait_list": bool(in_wait_list),
        "baked_count": baked_count,
        "bread_requirements": bread_requirements,
    })

    modify_msg = endpoint_helper.format_admin_event_message(
        event_title="Ticket Modified",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": customer_ticket_id,
            "location": "queue",
            "baked_count": baked_count,
        },
        bread_requirements=bread_requirements,
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:modify_ticket", modify_msg)

    return {
        "status": "OK",
        "customer_ticket_id": customer_ticket_id,
        "location": "queue",
    }


@router.put('/remove_ticket')
@handle_errors
async def remove_ticket(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = ticket.bakery_id
    customer_ticket_id = int(ticket.customer_ticket_id)
    r = request.app.state.redis

    time_per_bread = await redis_helper.get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    queue_state = await redis_helper.load_queue_state(r, bakery_id)
    _, _, _, _, _, redis_current_served = await redis_helper.get_slots_state(r, bakery_id)
    effective_current_served = max(int(queue_state.current_served or 0), int(redis_current_served or 0))
    if customer_ticket_id <= effective_current_served:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Ticket cannot be removed after it is served/baking progressed",
                "current_served": effective_current_served,
            },
        )

    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    upcoming_customers_key = redis_helper.REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    current_upcoming_key = redis_helper.REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    user_current_ticket_key = redis_helper.REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id)

    pipe0 = r.pipeline()
    pipe0.hexists(res_key, str(customer_ticket_id))
    pipe0.hexists(wait_list_key, str(customer_ticket_id))
    pipe0.sismember(served_key, int(customer_ticket_id))
    pipe0.get(current_upcoming_key)
    pipe0.get(user_current_ticket_key)
    in_queue, in_wait_list, is_served, current_upcoming_raw, user_current_raw = await pipe0.execute()

    exists_in_db = crud.get_customer_by_ticket_id_any_status(db, customer_ticket_id, bakery_id) is not None
    if not (in_queue or in_wait_list or bool(is_served) or exists_in_db):
        raise HTTPException(status_code=404, detail={"error": "Ticket does not exist"})

    all_breads = await r.zrangebyscore(breads_key, '-inf', '+inf')
    suffix = f":{customer_ticket_id}"
    to_remove_breads = []
    for bread_value in all_breads:
        if isinstance(bread_value, bytes):
            try:
                bread_value = bread_value.decode()
            except Exception:
                continue
        if isinstance(bread_value, str) and bread_value.endswith(suffix):
            to_remove_breads.append(bread_value)

    pipe = r.pipeline()
    pipe.hdel(res_key, str(customer_ticket_id))
    pipe.zrem(order_key, str(customer_ticket_id))
    pipe.hdel(wait_list_key, str(customer_ticket_id))
    pipe.srem(served_key, int(customer_ticket_id))
    pipe.zrem(upcoming_customers_key, int(customer_ticket_id))

    if current_upcoming_raw is not None and str(current_upcoming_raw) == str(customer_ticket_id):
        pipe.delete(current_upcoming_key)

    if user_current_raw is not None and str(user_current_raw) == str(customer_ticket_id):
        pipe.delete(user_current_ticket_key)

    if to_remove_breads:
        pipe.zrem(breads_key, *to_remove_breads)

    await pipe.execute()

    numbers_to_free = {customer_ticket_id}
    for num, t in list(queue_state.tickets.items()):
        if int(num) == customer_ticket_id or (t.parent_ticket is not None and int(t.parent_ticket) == customer_ticket_id):
            numbers_to_free.add(int(num))

    for num in numbers_to_free:
        # Burn removed ticket numbers permanently so allocator never reuses them.
        queue_state.tickets.pop(int(num), None)
        if hasattr(queue_state, "consumed_numbers"):
            queue_state.consumed_numbers.add(int(num))

    await redis_helper.save_queue_state(r, bakery_id, queue_state)

    crud.delete_customer_by_ticket_id_today(db, bakery_id, customer_ticket_id)

    try:
        await redis_helper.cleanup_urgent_items_for_ticket(
            r,
            bakery_id,
            int(customer_ticket_id),
            statuses=("DONE", "PENDING", "PROCESSING"),
        )
    except Exception:
        pass

    await redis_helper.get_bakery_reservations(
        r,
        bakery_id,
        fetch_from_redis_first=False,
        bakery_time_per_bread=time_per_bread,
    )
    await redis_helper.get_bakery_wait_list(
        r,
        bakery_id,
        fetch_from_redis_first=False,
        bakery_time_per_bread=time_per_bread,
    )
    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:remove_ticket", extra={
        "bakery_id": bakery_id,
        "customer_ticket_id": customer_ticket_id,
        "burned_numbers": sorted(list(numbers_to_free)),
        "removed_breads": len(to_remove_breads),
    })

    remove_msg = endpoint_helper.format_admin_event_message(
        event_title="Ticket Removed",
        fields={
            "bakery_id": bakery_id,
            "ticket_number": customer_ticket_id,
            "burned_numbers": sorted(list(numbers_to_free)),
            "removed_breads": len(to_remove_breads),
        },
    )
    await endpoint_helper.report_to_admin("ticket", f"{FILE_NAME}:remove_ticket", remove_msg)

    return {
        "status": "OK",
        "customer_ticket_id": customer_ticket_id,
        "burned_numbers": sorted(list(numbers_to_free)),
    }


@router.get('/bread_progress/{bakery_id}')
@handle_errors
async def bread_progress(
        bakery_id: int,
        request: Request,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin),
):
    bakery_id = int(bakery_id)
    required_normal = crud.get_today_total_required_breads(db, bakery_id)
    required_urgent = crud.get_today_total_required_urgent_breads(db, bakery_id)
    should_cook_total = int(required_normal) + int(required_urgent)

    cooked_normal = crud.get_today_total_baked_breads(db, bakery_id)
    cooked_urgent = crud.get_today_total_cooked_urgent_breads(db, bakery_id)
    cooked_total = int(cooked_normal) + int(cooked_urgent)

    remaining_total = int(should_cook_total) - int(cooked_total)
    if remaining_total < 0:
        remaining_total = 0

    return {
        "should_cook": int(should_cook_total),
        "already_cooked": int(cooked_total),
        "remaining": remaining_total,
        "normal": {
            "should_cook": int(required_normal),
            "already_cooked": int(cooked_normal),
        },
        "urgent": {
            "should_cook": int(required_urgent),
            "already_cooked": int(cooked_urgent),
        },
    }

@router.post('/new_bakery', response_model=schemas.AddBakeryResult)
@handle_errors
async def new_bakery(request: Request, bakery: schemas.AddBakery, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bakery = crud.add_bakery(db, bakery)
    logger.info(f"{FILE_NAME}:new_bakery", extra={"bakery_name": bakery.name, "location": bakery.location, "active": bakery.active, "baking_time_s": bakery.baking_time_s})
    if bakery.active:
        await redis_helper.initialize_redis_sets(request.app.state.redis, bakery.bakery_id)
    return bakery

@router.post('/modify_bakery')
@handle_errors
async def modify_bakery(request: Request, bakery: schemas.ModifyBakery, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bakery = crud.modify_bakery(db, bakery)
    if not bakery:
        raise HTTPException(status_code=404, detail='Bakery does not exist.')
    r = request.app.state.redis
    if bakery.active:
        await redis_helper.initialize_redis_sets(r, bakery.bakery_id)
    else:
        await redis_helper.purge_bakery_data(r, bakery.bakery_id)
    logger.info(
        f"{FILE_NAME}:modify_bakery",
        extra={c.name: getattr(bakery, c.name) for c in bakery.__table__.columns}
    )
    return {"status": "OK"}


@router.delete('/delete_bakery/{bakery_id}')
@handle_errors
async def delete_bakery(request: Request, bakery_id, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bakery = crud.delete_bakery(db, bakery_id)
    if not bakery:
        raise HTTPException(status_code=404, detail='Bakery does not exist.')
    await redis_helper.purge_bakery_data(request.app.state.redis, bakery_id)
    logger.info(f"{FILE_NAME}:delete_bakery", extra={"bakery_id": bakery_id})
    return {'status': 'OK'}


@router.post('/bakery_bread')
@handle_errors
async def bakery_bread(
        request: Request,
        data: schemas.Initialize,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    crud.delete_all_corresponding_bakery_bread(db, data.bakery_id)
    crud.add_bakery_bread_entries(db, data.bakery_id, data.bread_type_id_and_preparation_time)
    db.commit()
    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    await mqtt_client.update_time_per_bread(request, data.bakery_id, new_config)
    logger.info(f"{FILE_NAME}:bakery_bread", extra={"bakery_id": data.bakery_id, "bread_type_id_and_cook_time": data.bread_type_id_and_preparation_time})
    return {'status': 'OK'}


@router.put('/update_bakery_single_bread')
@handle_errors
async def update_bakery_single_bread(
        request: Request,
        data: schemas.ModifySingleBreadToBakery,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    # Validate that the bread exists
    bread = crud.get_bread_by_bread_id(db, bread_id=data.bread_id)

    if not bread:
        raise HTTPException(status_code=404, detail="Bread type not found")

    bakery = crud.get_bakery(db, data.bakery_id)

    if not bakery:
        raise HTTPException(status_code=404, detail="Bakery not found")

    existing = crud.get_bakery_bread(db, data.bakery_id, data.bread_id)

    if existing:
        crud.update_bread_bakery_no_commit(db, data.bakery_id, data.bread_id, data.preparation_time)
        state = 'update'
    else:
        crud.add_single_bread_to_bakery_no_commit(db, data.bakery_id, data.bread_id, data.preparation_time)
        state = 'add'

    db.commit()

    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, data.bakery_id)
    await mqtt_client.update_time_per_bread(request, data.bakery_id, new_config)
    logger.info(f"{FILE_NAME}:add_single_bread_to_bakery",
                extra={"bread_id": data.bread_id, "preparation_time": data.preparation_time})

    return {'status': 'OK', 'state': state}


@router.delete('/remove_single_bread_from_bakery/{bakery_id}/{bread_id}')
@handle_errors
async def remove_single_bread_from_bakery(
    request: Request,
    bakery_id: int,
    bread_id: int,
    db: Session = Depends(endpoint_helper.get_db),
    _: int = Depends(require_admin)
):
    remove_entry = crud.remove_single_bread_from_bakery(db, bakery_id, bread_id)
    new_config = await redis_helper.reset_bakery_metadata(request.app.state.redis, bakery_id)
    if remove_entry:
        await mqtt_client.update_time_per_bread(request, bakery_id, new_config)
        logger.info(f"{FILE_NAME}:remove_single_bread_from_bakery", extra={"bakery_id": bakery_id, "bread_id": bread_id})
        return {'status': 'OK'}
    return {'status': 'No entry found'}


@router.post('/add_bread', response_model=schemas.BreadID)
@handle_errors
async def add_bread(request: Request, bread: schemas.AddBread, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    try:
        bread_id = crud.add_bread(db, bread)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bread name already exists")
    await redis_helper.reset_bread_names(request.app.state.redis)
    logger.info(f"{FILE_NAME}:add_bread", extra={"bread_name": bread.name, "active": bread.active})
    return bread_id


@router.put('/change_bread_status')
@handle_errors
async def change_bread_status(request: Request, bread: schemas.ModifyBread, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    bread = crud.change_bread_status(db, bread)
    if not bread:
        raise HTTPException(status_code=404, detail='Bread does not exist')
    await redis_helper.reset_bread_names(request.app.state.redis)
    logger.info(f"{FILE_NAME}:change_bread_status", extra={"bread_id": bread.bread_id, 'active': bread.active})
    return {'status': 'OK'}


@router.delete('/delete_bread/{bread_id}')
@handle_errors
async def delete_bread(request: Request, bread_id: int, db: Session = Depends(endpoint_helper.get_db), _:int = Depends(require_admin)):
    result = crud.delete_bread(db, bread_id)
    if not result:
        raise HTTPException(status_code=404, detail="Bread does not exist")
    r = request.app.state.redis
    await redis_helper.reset_bread_names(r)

    # Deleting a bread type changes the active bread set, so each bakery's
    # time_per_bread and reservation encoding must be refreshed to avoid
    # length-mismatch issues in queue parsing.
    active_bakeries = crud.get_all_active_bakeries(db)
    for bakery in active_bakeries:
        new_config = await redis_helper.reset_bakery_metadata(r, bakery.bakery_id)
        await mqtt_client.update_time_per_bread(request, bakery.bakery_id, new_config)

        await redis_helper.get_bakery_reservations(
            r,
            bakery.bakery_id,
            fetch_from_redis_first=False,
            bakery_time_per_bread=new_config,
        )
        await redis_helper.get_bakery_wait_list(
            r,
            bakery.bakery_id,
            fetch_from_redis_first=False,
            bakery_time_per_bread=new_config,
        )
        await redis_helper.rebuild_prep_state(r, bakery.bakery_id)
    logger.info(f"{FILE_NAME}:delete_bread", extra={"bread_id": bread_id})
    return {"status": "OK"}


@router.put('/change_bread_names')
@handle_errors
async def change_bread_names(
        request: Request,
        data: schemas.ChangeBreadName,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    crud.edit_bread_names(db, data.bread_id_and_names)
    await redis_helper.reset_bread_names(request.app.state.redis)
    logger.info(f"{FILE_NAME}:change_bread_names", extra={"bread_id_and_names": data.bread_id_and_names})
    return {'status': 'OK'}


@router.post('/upcoming/add')
@handle_errors
async def add_notify_bakery_bread(
        request: Request,
        data: schemas.ModifyBakeryBreadNotify,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    try:
        entry = crud.add_upcoming_bread_to_bakery(db, data.bakery_id, data.bread_id)
        if entry is None:
            raise HTTPException(status_code=404, detail='Bread does not exist in bakery-bread table')
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail='Bread-notify already exists')

    logger.info(f"{FILE_NAME}:add_upcoming_bread", extra={"bakery_id": data.bakery_id, "bread_id": data.bread_id})
    await redis_helper.add_upcoming_bread_to_bakery(r, data.bakery_id, data.bread_id)
    return {'status': 'OK'}

@router.delete('/upcoming/remove/{bakery_id}/{bread_id}')
@handle_errors
async def remove_notify_bakery_bread(
        request: Request,
        bakery_id: int,
        bread_id: int,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    removed = crud.remove_upcoming_bread_from_bakery(db, bakery_id, bread_id)
    logger.info(f"{FILE_NAME}:remove_upcoming_bread", extra={"bakery_id": bakery_id, "bread_id": bread_id, "removed": removed})
    if removed:
        await redis_helper.remove_upcoming_bread_from_bakery(r, bakery_id, bread_id)
    return {"status": "OK" if removed else "not_found"}


@router.get('/upcoming/list/{bakery_id}')
@handle_errors
async def list_notify_bakery_bread(
        request: Request,
        bakery_id: int,
        db: Session = Depends(endpoint_helper.get_db),
        _: int = Depends(require_admin)
):
    r = request.app.state.redis
    bread_ids = await redis_helper.get_bakery_upcoming_breads(r, bakery_id)
    return {'bread_ids': bread_ids}
