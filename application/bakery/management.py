from fastapi import APIRouter, Depends, HTTPException, Request
from application.logger_config import logger
from sqlalchemy.orm import Session
from application.helpers.general_helpers import seconds_until_midnight_iran
from application.helpers import database_helper, endpoint_helper, redis_helper
from application import mqtt_client, crud, schemas
from sqlalchemy.exc import IntegrityError

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
        encoded_reservation = ",".join("0" for _ in bread_ids_sorted)

        res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
        order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
        wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
        served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
        ttl = seconds_until_midnight_iran()

        pipe = r.pipeline(transaction=True)
        pipe.srem(served_key, int(ticket_id))
        pipe.hdel(wait_list_key, str(ticket_id))
        pipe.hset(res_key, str(ticket_id), encoded_reservation)
        pipe.zadd(order_key, {str(ticket_id): int(ticket_id)})
        pipe.expire(res_key, ttl)
        pipe.expire(order_key, ttl)
        pipe.expire(wait_list_key, ttl)
        await pipe.execute()

        crud.update_customer_status_to_true(db, ticket_id, bakery_id)

    urgent_id = await redis_helper.create_urgent_item(
        r,
        bakery_id,
        ticket_id,
        bread_requirements,
        time_per_bread=time_per_bread,
    )

    await redis_helper.rebuild_prep_state(r, bakery_id)

    logger.info(f"{FILE_NAME}:urgent_inject", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
        "ticket_id": ticket_id,
        "bread_requirements": bread_requirements,
    })

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

    logger.info(f"{FILE_NAME}:urgent_edit", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
        "bread_requirements": bread_requirements,
    })

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

    logger.info(f"{FILE_NAME}:urgent_delete", extra={
        "bakery_id": bakery_id,
        "urgent_id": urgent_id,
    })

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
    in_queue, in_wait_list, is_served = await pipe0.execute()

    if bool(is_served):
        raise HTTPException(status_code=400, detail={"error": "Ticket is already served"})

    if not (in_queue or in_wait_list):
        await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        await redis_helper.get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        pipe_retry = r.pipeline()
        pipe_retry.hexists(res_key, str(customer_ticket_id))
        pipe_retry.hexists(wait_list_key, str(customer_ticket_id))
        in_queue, in_wait_list = await pipe_retry.execute()

    if not (in_queue or in_wait_list):
        raise HTTPException(status_code=404, detail={"error": "Ticket does not exist"})

    ttl = seconds_until_midnight_iran()
    pipe = r.pipeline()
    if in_queue:
        pipe.hset(res_key, str(customer_ticket_id), encoded_reservation)
        pipe.zadd(order_key, {str(customer_ticket_id): int(customer_ticket_id)})
        pipe.expire(res_key, ttl)
        pipe.expire(order_key, ttl)
    else:
        pipe.hset(wait_list_key, str(customer_ticket_id), encoded_reservation)
        pipe.expire(wait_list_key, ttl)
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

    return {
        "status": "OK",
        "customer_ticket_id": customer_ticket_id,
        "location": "queue" if in_queue else "wait_list",
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
        queue_state.tickets.pop(int(num), None)
        if int(num) > queue_state.current_served:
            queue_state.slots_for_singles.add(int(num))
            queue_state.slots_for_multis.add(int(num))

    await redis_helper.save_queue_state(r, bakery_id, queue_state)

    crud.delete_customer_by_ticket_id_today(db, bakery_id, customer_ticket_id)

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
        "freed_numbers": sorted(list(numbers_to_free)),
        "removed_breads": len(to_remove_breads),
    })

    return {
        "status": "OK",
        "customer_ticket_id": customer_ticket_id,
        "freed_numbers": sorted(list(numbers_to_free)),
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
    r = request.app.state.redis

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
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    prep_state_str, time_per_bread, reservations_map, order_ids, all_breads = await pipe.execute()

    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    order_ids = [int(x) for x in order_ids] if order_ids else []
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    } if reservations_map else {}

    should_cook_total = 0
    for tid in order_ids:
        counts = reservation_dict.get(int(tid))
        if counts:
            should_cook_total += sum(counts)

    breads_per_customer = {}
    cooked_total = 0
    for bread_value in all_breads or []:
        if isinstance(bread_value, bytes):
            try:
                bread_value = bread_value.decode()
            except Exception:
                continue
        if isinstance(bread_value, str) and ':' in bread_value:
            try:
                _, cid_str = bread_value.split(':', 1)
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue
            breads_per_customer[cid] = breads_per_customer.get(cid, 0) + 1
            cooked_total += 1

    remaining_total = should_cook_total - cooked_total
    if remaining_total < 0:
        remaining_total = 0

    current_customer_id = None
    current_customer_needed = None
    current_customer_made = None

    if prep_state_str and order_ids:
        if isinstance(prep_state_str, bytes):
            try:
                prep_state_str = prep_state_str.decode()
            except Exception:
                prep_state_str = None
        if prep_state_str:
            try:
                state_customer_id, _ = map(int, str(prep_state_str).split(':'))
            except ValueError:
                state_customer_id = None

            if state_customer_id and state_customer_id in order_ids:
                current_customer_id = state_customer_id
                counts = reservation_dict.get(int(state_customer_id))
                current_customer_needed = sum(counts) if counts else 0
                current_customer_made = breads_per_customer.get(int(state_customer_id), 0)

    return {
        "should_cook": should_cook_total,
        "already_cooked": cooked_total,
        "remaining": remaining_total,
        "current_customer_id": current_customer_id,
        "current_customer_needed": current_customer_needed,
        "current_customer_made": current_customer_made,
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

