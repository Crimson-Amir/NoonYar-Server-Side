from fastapi import HTTPException
from application import crud
from application.database import SessionLocal
from application.helpers.general_helpers import seconds_until_midnight_iran
import time
from collections import defaultdict


REDIS_KEY_PREFIX = "bakery:{0}"
REDIS_KEY_RESERVATIONS = f"{REDIS_KEY_PREFIX}:reservations"
REDIS_KEY_RESERVATION_ORDER = f"{REDIS_KEY_PREFIX}:reservation_order"
REDIS_KEY_TIME_PER_BREAD = f"{REDIS_KEY_PREFIX}:time_per_bread"
REDIS_KEY_WAIT_LIST = f"{REDIS_KEY_PREFIX}:wait_list"
REDIS_KEY_LAST_KEY = f"{REDIS_KEY_PREFIX}:last_ticket"
REDIS_KEY_UPCOMING_BREADS = f"{REDIS_KEY_PREFIX}:upcoming_breads"
REDIS_KEY_UPCOMING_CUSTOMERS = f"{REDIS_KEY_PREFIX}:upcoming_customers"
REDIS_KEY_CURRENT_UPCOMING_CUSTOMER = f"{REDIS_KEY_PREFIX}:current_upcoming_customer"
REDIS_KEY_BAKING_TIME_S = f"{REDIS_KEY_PREFIX}:baking_time_s"
REDIS_KEY_TIMEOUT_SEC = f"{REDIS_KEY_PREFIX}:timeout_sec"
REDIS_KEY_BREADS = f"{REDIS_KEY_PREFIX}:breads"
REDIS_KEY_LAST_BREAD_TIME = f"{REDIS_KEY_PREFIX}:last_bread_time"
REDIS_KEY_BREAD_TIME_DIFFS = f"{REDIS_KEY_PREFIX}:bread_time_diff"
REDIS_KEY_PREP_STATE = f"{REDIS_KEY_PREFIX}:prep_state"
REDIS_KEY_DISPLAY_CUSTOMER = f"{REDIS_KEY_PREFIX}:display_customer"
REDIS_KEY_BREAD_NAMES = "bread_names"
REDIS_KEY_SLOTS_FOR_MULTIS = f"{REDIS_KEY_PREFIX}:slots_for_multis"
REDIS_KEY_SLOTS_FOR_SINGLES = f"{REDIS_KEY_PREFIX}:slots_for_singles"
REDIS_KEY_NEXT_TICKET = f"{REDIS_KEY_PREFIX}:next_ticket"
REDIS_KEY_LAST_SINGLE = f"{REDIS_KEY_PREFIX}:last_single"
REDIS_KEY_LAST_MULTI = f"{REDIS_KEY_PREFIX}:last_multi"
REDIS_KEY_CURRENT_SERVED = f"{REDIS_KEY_PREFIX}:current_served"
REDIS_KEY_QUEUE_STATE = f"{REDIS_KEY_PREFIX}:queue_state"
REDIS_KEY_SERVED_TICKETS = f"{REDIS_KEY_PREFIX}:served_tickets"
REDIS_KEY_USER_CURRENT_TICKET = f"{REDIS_KEY_PREFIX}:user_current_ticket"

async def get_bakery_runtime_state(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    upcoming_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    pipe1.smembers(upcoming_key)
    breads_type, reservation_dict, upcoming_members = await pipe1.execute()
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservation_dict.items()} if reservation_dict else {}

    if not breads_type:
        raise HTTPException(status_code=404, detail={"error": "this bakery does not have any bread"})

    upcoming_set = set(upcoming_members) if upcoming_members else None

    return breads_type, reservation_dict, upcoming_set

async def get_order_set_from_reservations(r, bakery_id: int):
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    hlen = await r.hlen(reservations_key)

    if hlen > 0:
        members = await r.hkeys(reservations_key)
        if members:
            mapping = {mid: int(mid) for mid in members}
            await r.zadd(order_key, mapping)
            first_id = min(mapping, key=mapping.get)
            return list(first_id) or []

async def get_customer_reservation(r, bakery_id, customer_id):
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    return await r.hget(res_key, customer_id)

async def get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    upcoming_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.smembers(upcoming_key)
    return await pipe1.execute()

async def get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    skipped_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
    upcomming_bread_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hget(res_key, str(customer_id))
    pipe1.hget(skipped_key, str(customer_id))
    pipe1.hdel(skipped_key, str(customer_id))
    pipe1.smembers(upcomming_bread_key)
    return await pipe1.execute()

async def check_current_ticket_id(r, bakery_id, current_ticket_id: list, return_error=True):
    if not current_ticket_id:
        current_ticket_id = await get_order_set_from_reservations(r, bakery_id)
        if not current_ticket_id:
            if return_error:
                raise HTTPException(status_code=404, detail={"error": "empty queue"})
            else:
                return
    return int(current_ticket_id[0])

async def check_for_correct_current_id(customer_id, current_ticket_id):

    if current_ticket_id != customer_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid ticket number",
                "current_ticket_id": current_ticket_id,
            }
        )

async def get_current_cusomter_detail(r, bakery_id, customer_id, time_per_bread, customer_reservations):

    if not customer_reservations:
        get_from_db = await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        customer_reservations = get_from_db.get(customer_id)
        if not customer_reservations: raise HTTPException(status_code=404, detail={"error": "reservation not found in list"})
    else:
        customer_reservations = list(map(int, customer_reservations.split(",")))

    return customer_reservations

async def remove_customer_id_from_reservation(r, bakery_id, customer_id):
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe = r.pipeline()
    pipe.hget(res_key, str(customer_id))
    pipe.hdel(res_key, customer_id)
    pipe.zrem(order_key, customer_id)
    reservations, r1, r2 = await pipe.execute()
    return bool(r1 and r2), reservations

async def get_customer_reservation_detail(time_per_bread, counts) -> dict[str, int] | None:
    bread_ids = list(time_per_bread.keys())

    if len(counts) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    return {bid: count for bid, count in zip(bread_ids, counts)}

LUA_ADD_RESERVATION = """

    local reservations = KEYS[1]
    local order = KEYS[2]
    local last_ticket = KEYS[3]
    local ticket = ARGV[1]
    local value = ARGV[2]
    local score = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[3])

    local ok = redis.call('HSETNX', reservations, ticket, value)
    if ok == 1 then
        redis.call('ZADD', order, score, ticket)
        redis.call('SET', last_ticket, ticket)
        -- set TTLs on all related keys
        redis.call('EXPIRE', reservations, ttl)
        redis.call('EXPIRE', order, ttl)
        redis.call('EXPIRE', last_ticket, ttl)
    end

    return ok
"""



async def add_customer_to_reservation_dict(
        r, bakery_id: int, customer_id: int, bread_count_data: dict[str, int], time_per_bread=None
) -> bool:
    time_per_bread = time_per_bread or await get_bakery_time_per_bread(r, bakery_id)
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    last_ticket_key = REDIS_KEY_LAST_KEY.format(bakery_id)

    reservation = [bread_count_data.get(bid, 0) for bid in time_per_bread.keys()]
    encoded = ",".join(map(str, reservation))
    ttl = seconds_until_midnight_iran()
    script = r.register_script(LUA_ADD_RESERVATION)
    result = await script(
        keys=[reservations_key, order_key, last_ticket_key],
        args=[str(customer_id), encoded, str(ttl)],
    )

    return result == 1

async def add_customer_to_wait_list(r, bakery_id: int, customer_id: int, reservations: list[int]=None, reservations_str=None):
    skipped_customer_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
    pipe = r.pipeline()
    pipe.hset(skipped_customer_key, str(customer_id), reservations_str or ",".join(map(str, reservations)))
    ttl = seconds_until_midnight_iran()
    pipe.expire(skipped_customer_key, ttl)
    await pipe.execute()

async def reset_bakery_metadata(r, bakery_id: int):
    """
    Refresh bread metadata into Redis as HASHES (better than JSON).
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    with SessionLocal() as db:
        bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.preparation_time for bread in bakery_breads}

        pipe = r.pipeline()
        pipe.delete(time_key)
        if time_per_bread:
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
        
        await pipe.execute()
        return time_per_bread


async def reset_bread_names(r):
    bread_name_key = REDIS_KEY_BREAD_NAMES

    with SessionLocal() as db:
        breads = crud.get_active_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        pipe = r.pipeline()
        pipe.delete(bread_name_key)
        
        if bread_names:
            pipe.hset(bread_name_key, mapping=bread_names)
            ttl = seconds_until_midnight_iran()
            pipe.expire(bread_name_key, ttl)
        
        await pipe.execute()
        return bread_names


async def get_bakery_bread_names(r):
    bread_name_key = REDIS_KEY_BREAD_NAMES
    raw = await r.hgetall(bread_name_key)
    if raw:
        return raw

    with SessionLocal() as db:
        breads = crud.get_active_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        pipe = r.pipeline()
        pipe.delete(bread_name_key)
        
        if bread_names:
            pipe.hset(bread_name_key, mapping=bread_names)
            ttl = seconds_until_midnight_iran()
            pipe.expire(bread_name_key, ttl)
        
        await pipe.execute()
        return bread_names


async def get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=True, bakery_time_per_bread=None):
    skipped_customer_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
    if fetch_from_redis_first:
        reservations = await r.hgetall(skipped_customer_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    with SessionLocal() as db:
        today_customers = crud.get_today_wait_list(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(skipped_customer_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.ticket_id] = reservation

            pipe.hset(skipped_customer_key, str(customer.ticket_id), ",".join(map(str, reservation)))

        if reservation_dict:
            ttl = seconds_until_midnight_iran()
            pipe.expire(skipped_customer_key, ttl)
        await pipe.execute()
        print("fetch skipped customer from db")

        return reservation_dict


async def get_bakery_reservations(r, bakery_id: int, fetch_from_redis_first=True, bakery_time_per_bread=None):
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)

    if fetch_from_redis_first:
        reservations = await r.hgetall(reservations_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    with SessionLocal() as db:

        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)
        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(order_key)
        pipe.delete(reservations_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.ticket_id] = reservation

            pipe.hset(reservations_key, str(customer.ticket_id), ",".join(map(str, reservation)))
            pipe.zadd(order_key, {str(customer.ticket_id): customer.ticket_id})

        
        if reservation_dict:
            ttl = seconds_until_midnight_iran()
            pipe.expire(reservations_key, ttl)
            pipe.expire(order_key, ttl)
        
        await pipe.execute()
        print("fetch reservation from db")

        return reservation_dict


async def get_bakery_time_per_bread(r, bakery_id: int, fetch_from_redis_first=True):
    """
    Fetch bread_type_id -> preparation_time mapping for a bakery.
    Stored in Redis as a HASH: HSET bakery:{id}:time_per_bread {bread_id} {time}
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    if fetch_from_redis_first:
        raw = await r.hgetall(time_key)
        if raw:
            return {k: int(v) for k, v in raw.items()}

    with SessionLocal() as db:

        bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): int(bread.preparation_time) for bread in bakery_breads}
        pipe = r.pipeline()
        pipe.delete(time_key)

        if time_per_bread:
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
        
        await pipe.execute()
        print("fetch time per bread from db")
        return time_per_bread


def reset_time_per_bread_sync(r, db, bakery_id: int):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
    time_per_bread = {str(bread.bread_type_id): bread.preparation_time for bread in bakery_breads}
    pipe = r.pipeline()
    pipe.delete(time_key)

    if time_per_bread:
        pipe.hset(time_key, mapping=time_per_bread)
        ttl = seconds_until_midnight_iran()
        pipe.expire(time_key, ttl)

    pipe.execute()
    print("fetch time per bread from db")
    return time_per_bread


async def get_last_ticket_number(r, bakery_id, fetch_from_redis_first=True):
    last_one_key = REDIS_KEY_LAST_KEY.format(bakery_id)
    if fetch_from_redis_first:
        last_ticket_number = await r.get(last_one_key)
        if last_ticket_number:
            return int(last_ticket_number)

    with SessionLocal() as db:
        last_customer = crud.get_today_last_customer(db, bakery_id)

        last = last_customer.ticket_id if last_customer else 0

        pipe = r.pipeline()
        pipe.set(last_one_key, last)
        ttl = seconds_until_midnight_iran()
        pipe.expire(last_one_key, ttl)
        await pipe.execute()

        return last


async def is_ticket_used_today(r, bakery_id: int, ticket_id: int) -> bool:
    """Check if a hardware ticket_id has been used today for a bakery.

    This queries the database for any Customer row for today with this
    ticket_id and bakery_id, independent of is_in_queue/wait_list status.
    """
    with SessionLocal() as db:
        return crud.customer_ticket_exists_today(db, ticket_id, bakery_id)


async def get_current_served(r, bakery_id: int) -> int:
    key = REDIS_KEY_CURRENT_SERVED.format(bakery_id)
    raw = await r.get(key)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def set_current_served(r, bakery_id: int, ticket_id: int) -> None:
    key = REDIS_KEY_CURRENT_SERVED.format(bakery_id)
    if ticket_id is None or int(ticket_id) <= 0:
        await r.delete(key)
        return

    ttl = seconds_until_midnight_iran()
    await r.set(key, int(ticket_id), ex=ttl)


async def set_user_current_ticket(r, bakery_id: int, ticket_id: int | None) -> None:
    """Set the current ticket ID for user-facing endpoints (/res).

    If ticket_id is None or <= 0, the key is cleared.
    Otherwise it is stored with TTL until midnight.
    """
    key = REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id)
    if ticket_id is None or int(ticket_id) <= 0:
        await r.delete(key)
        return

    ttl = seconds_until_midnight_iran()
    await r.set(key, int(ticket_id), ex=ttl)


async def get_user_current_ticket(r, bakery_id: int) -> int | None:
    key = REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id)
    raw = await r.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def load_queue_state(r, bakery_id: int):
    from application.bakery_queue_model import BakeryQueueState

    key = REDIS_KEY_QUEUE_STATE.format(bakery_id)
    raw = await r.get(key)
    import json

    if not raw:
        # No Redis state: try to restore from today's DB snapshot first.
        from application.database import SessionLocal
        from application import crud

        with SessionLocal() as db:
            snapshot = crud.get_today_queue_state_snapshot(db, bakery_id)

        if snapshot and snapshot.state_json:
            try:
                data = json.loads(snapshot.state_json)
                return BakeryQueueState.from_dict(data)
            except Exception:
                # Fallback to fresh state if snapshot is corrupted.
                pass

        # Fresh state: seed next_number from today's last ticket so
        # hardware ticket IDs remain monotonic across restarts.
        state = BakeryQueueState()
        last_ticket = await get_last_ticket_number(r, bakery_id)
        state.next_number = last_ticket + 1
        return state

    try:
        data = json.loads(raw)
    except Exception:
        # Redis payload is corrupted: attempt DB snapshot, else fall back
        # to a fresh state seeded from today's last ticket.
        from application.database import SessionLocal
        from application import crud

        with SessionLocal() as db:
            snapshot = crud.get_today_queue_state_snapshot(db, bakery_id)

        if snapshot and snapshot.state_json:
            try:
                data = json.loads(snapshot.state_json)
                return BakeryQueueState.from_dict(data)
            except Exception:
                pass

        state = BakeryQueueState()
        last_ticket = await get_last_ticket_number(r, bakery_id)
        state.next_number = last_ticket + 1
        return state

    return BakeryQueueState.from_dict(data)


async def save_queue_state(r, bakery_id: int, state) -> None:
    key = REDIS_KEY_QUEUE_STATE.format(bakery_id)

    import json

    payload = json.dumps(state.to_dict(), ensure_ascii=False)
    ttl = seconds_until_midnight_iran()
    await r.set(key, payload, ex=ttl)

    # Persist a daily snapshot to the database for crash recovery and
    # debugging. This keeps one row per bakery per local_tehran_date.
    from application.database import SessionLocal
    from application import crud

    with SessionLocal() as db:
        crud.upsert_queue_state_snapshot(db, bakery_id, state.to_dict())


async def get_effective_current_served(r, bakery_id: int) -> int:
    """Return the effective current_served cutoff for ticket issuance.

    This combines the explicit current_served key (set from new_bread)
    with the maximum ticket_id that has any bread recorded in the
    breads sorted set. The behavior matches the current_served logic
    inside get_slots_state, but without loading or touching any of the
    legacy slot/next/last keys.
    """
    current_key = REDIS_KEY_CURRENT_SERVED.format(bakery_id)
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(current_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    raw_current, all_breads = await pipe.execute()

    current_served = int(raw_current) if raw_current is not None else 0

    max_ticket_from_breads = 0
    if all_breads:
        for bread_value in all_breads:
            if ':' in bread_value:
                try:
                    _, cid_str = bread_value.split(':', 1)
                    cid = int(cid_str)
                    if cid > max_ticket_from_breads:
                        max_ticket_from_breads = cid
                except (ValueError, TypeError):
                    continue

    if max_ticket_from_breads > current_served:
        current_served = max_ticket_from_breads

    return current_served


async def get_slots_state(r, bakery_id: int):
    multi_key = REDIS_KEY_SLOTS_FOR_MULTIS.format(bakery_id)
    single_key = REDIS_KEY_SLOTS_FOR_SINGLES.format(bakery_id)
    next_key = REDIS_KEY_NEXT_TICKET.format(bakery_id)
    last_single_key = REDIS_KEY_LAST_SINGLE.format(bakery_id)
    last_multi_key = REDIS_KEY_LAST_MULTI.format(bakery_id)
    current_key = REDIS_KEY_CURRENT_SERVED.format(bakery_id)
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.smembers(multi_key)
    pipe.smembers(single_key)
    pipe.get(next_key)
    pipe.get(last_single_key)
    pipe.get(last_multi_key)
    pipe.get(current_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    raw_multi, raw_single, raw_next, raw_last_single, raw_last_multi, raw_current, all_breads = await pipe.execute()

    slots_for_multis = {int(x) for x in raw_multi} if raw_multi else set()
    slots_for_singles = {int(x) for x in raw_single} if raw_single else set()

    if raw_next is None:
        last_ticket = await get_last_ticket_number(r, bakery_id)
        next_number = last_ticket + 1
    else:
        next_number = int(raw_next)

    last_single = int(raw_last_single) if raw_last_single is not None else 0
    last_multi = int(raw_last_multi) if raw_last_multi is not None else 0
    current_served = int(raw_current) if raw_current is not None else 0

    max_ticket_from_breads = 0
    if all_breads:
        for bread_value in all_breads:
            if ':' in bread_value:
                try:
                    _, cid_str = bread_value.split(':', 1)
                    cid = int(cid_str)
                    if cid > max_ticket_from_breads:
                        max_ticket_from_breads = cid
                except (ValueError, TypeError):
                    continue

    if max_ticket_from_breads > current_served:
        current_served = max_ticket_from_breads

    if current_served > 0:
        slots_for_multis = {n for n in slots_for_multis if n > current_served}
        slots_for_singles = {n for n in slots_for_singles if n > current_served}
        if next_number <= current_served:
            next_number = current_served + 1

    # Ensure next_number is strictly greater than any existing structural
    # number (last_single, last_multi, current_served, and all slot values).
    # This mirrors the monotonic next_number behavior of the in-memory
    # BakeryQueue, and prevents new tickets (especially large multis) from
    # being assigned into the middle of existing singles when there is not
    # enough slots_for_multis capacity.
    max_slot = 0
    if slots_for_multis:
        max_slot = max(max_slot, max(slots_for_multis))
    if slots_for_singles:
        max_slot = max(max_slot, max(slots_for_singles))

    max_structural = max(last_single, last_multi, current_served, max_slot)
    if next_number <= max_structural:
        next_number = max_structural + 1

    return slots_for_multis, slots_for_singles, next_number, last_single, last_multi, current_served


async def save_slots_state(
    r,
    bakery_id: int,
    slots_for_multis,
    slots_for_singles,
    next_number: int,
    last_single: int,
    last_multi: int,
):
    multi_key = REDIS_KEY_SLOTS_FOR_MULTIS.format(bakery_id)
    single_key = REDIS_KEY_SLOTS_FOR_SINGLES.format(bakery_id)
    next_key = REDIS_KEY_NEXT_TICKET.format(bakery_id)
    last_single_key = REDIS_KEY_LAST_SINGLE.format(bakery_id)
    last_multi_key = REDIS_KEY_LAST_MULTI.format(bakery_id)

    ttl = seconds_until_midnight_iran()
    pipe = r.pipeline()

    pipe.delete(multi_key)
    pipe.delete(single_key)

    if slots_for_multis:
        pipe.sadd(multi_key, *[str(x) for x in slots_for_multis])
        pipe.expire(multi_key, ttl)

    if slots_for_singles:
        pipe.sadd(single_key, *[str(x) for x in slots_for_singles])
        pipe.expire(single_key, ttl)

    pipe.set(next_key, int(next_number))
    pipe.expire(next_key, ttl)
    pipe.set(last_single_key, int(last_single))
    pipe.expire(last_single_key, ttl)
    pipe.set(last_multi_key, int(last_multi))
    pipe.expire(last_multi_key, ttl)

    await pipe.execute()

async def is_ticket_in_wait_list(r, bakery_id, customer_id):
    skipped_list = REDIS_KEY_WAIT_LIST.format(bakery_id)
    is_exists = await r.hget(skipped_list, customer_id)
    return is_exists is not None


async def add_served_ticket(r, bakery_id: int, ticket_id: int):
    key = REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    pipe = r.pipeline()
    pipe.sadd(key, int(ticket_id))
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def is_ticket_served(r, bakery_id: int, ticket_id: int) -> bool:
    key = REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    res = await r.sismember(key, int(ticket_id))
    return bool(res)


async def get_bakery_upcoming_breads(r, bakery_id: int, fetch_from_redis_first: bool = True) -> list[str]:
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    if fetch_from_redis_first:
        members = await r.smembers(key)
        if members:
            return list(members)

    with SessionLocal() as db:
        entries = crud.get_bakery_upcoming_breads(db, bakery_id)
        bread_ids = [str(e.bread_type_id) for e in entries]

    pipe = r.pipeline()
    pipe.delete(key)

    if bread_ids:
        pipe.sadd(key, *bread_ids)
        ttl = seconds_until_midnight_iran()
        pipe.expire(key, ttl)

    await pipe.execute()
    return bread_ids


async def add_upcoming_bread_to_bakery(r, bakery_id: int, bread_id: int):
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe = r.pipeline()
    pipe.sadd(key, str(bread_id))
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def remove_upcoming_bread_from_bakery(r, bakery_id: int, bread_id: int):
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe = r.pipeline()
    pipe.srem(key, str(bread_id))
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def ensure_upcoming_customers_zset(
        r,
        bakery_id: int,
        fetch_from_redis_first: bool = True
) -> list[int] | None:
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)

    if fetch_from_redis_first:
        zcard = await r.zcard(zkey)
        if zcard and zcard > 0:
            members = await r.zrange(zkey, 0, -1)
            return [int(x) for x in members]

    pipe = r.pipeline()
    pipe.delete(zkey)

    with SessionLocal() as db:
        entries = crud.get_bakery_upcoming_customers(db, bakery_id)
        res = None
        if entries:
            customer_ids = {str(customer.customer.ticket_id): int(customer.customer.ticket_id) for customer in entries}
            pipe.zadd(zkey, customer_ids)
            ttl = seconds_until_midnight_iran()
            pipe.expire(zkey, ttl)
            res = customer_ids.values()

    await pipe.execute()
    return res

async def maybe_add_customer_to_upcoming_zset(
        r,
        bakery_id: int,
        customer_id: int,
        bread_requirements: dict[str, int],
        upcoming_members: set[str] | None = None
) -> bool:

    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)

    if upcoming_members is None:
        upcoming_members = await get_bakery_upcoming_breads(r, bakery_id)

    members = set(upcoming_members)

    if not members:
        return False
    will_add = any((bid in members and int(count) > 0) for bid, count in bread_requirements.items())
    if not will_add:
        return False

    pipe2 = r.pipeline()
    pipe2.zadd(zkey, {str(customer_id): int(customer_id)})
    ttl = seconds_until_midnight_iran()
    pipe2.expire(zkey, ttl)
    await pipe2.execute()
    return True
    
 

async def get_baking_time_s(r, bakery_id: int, fetch_from_redis_first: bool = True) -> int:
    key = REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    if fetch_from_redis_first:
        val = await r.get(key)
        if val is not None:
            return int(val)

    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        bakery = crud.get_bakery(db, bakery_id)
        value = bakery.baking_time_s
        pipe.set(key, value)
        
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()
    
    return value


async def get_timeout_second(r, bakery_id: int, fetch_from_redis_first: bool = True) -> int:
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    if fetch_from_redis_first:
        val = await r.get(key)
        if val is not None:
            return int(val)

    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        bakery = crud.get_bakery(db, bakery_id)
        value = None
        if bakery:
            value = bakery.timeout_sec
            pipe.set(key, value)
            ttl = seconds_until_midnight_iran()
            pipe.expire(key, ttl)

    await pipe.execute()
    return value

async def reset_timeout(r, bakery_id: int) -> int:
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        res = crud.update_timeout_second(db, bakery_id, 0)
        value = None
        if res is not None:
            value = 0
            pipe.set(key, value)
            ttl = seconds_until_midnight_iran()
            pipe.expire(key, ttl)

    await pipe.execute()
    return value


async def update_timeout(r, bakery_id: int, new_timeout_second: int):
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    pipe = r.pipeline()
    pipe.set(key, new_timeout_second)
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def remove_customer_from_upcoming_customers(r, bakery_id, customer_id):
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    await r.zrem(zkey, customer_id)

async def remove_customer_from_upcoming_customers_and_add_to_current_upcoming_customer(r, bakery_id, customer_id, preparation_time):
    cur_key = REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id)
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    pipe = r.pipeline()
    pipe.setex(cur_key, preparation_time, customer_id)
    pipe.zrem(zkey, customer_id)
    await pipe.execute()

async def initialize_redis_sets(r, bakery_id: int):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_last_ticket_number(r, bakery_id, fetch_from_redis_first=False)
    await get_bakery_upcoming_breads(r, bakery_id, fetch_from_redis_first=False)
    await ensure_upcoming_customers_zset(r, bakery_id, fetch_from_redis_first=False)
    await get_baking_time_s(r, bakery_id, fetch_from_redis_first=False)
    await get_timeout_second(r, bakery_id, fetch_from_redis_first=False)
    await load_breads_from_db(r, bakery_id)
    await rebuild_prep_state(r, bakery_id)
    await rebuild_display_state(r, bakery_id)

async def initialize_redis_sets_only_12_oclock(r, bakery_id: int):
    await reset_timeout(r, bakery_id)

async def purge_bakery_data(r, bakery_id: int):
    keys = [
        REDIS_KEY_RESERVATIONS.format(bakery_id),
        REDIS_KEY_RESERVATION_ORDER.format(bakery_id),
        REDIS_KEY_TIME_PER_BREAD.format(bakery_id),
        REDIS_KEY_WAIT_LIST.format(bakery_id),
        REDIS_KEY_LAST_KEY.format(bakery_id),
        REDIS_KEY_UPCOMING_BREADS.format(bakery_id),
        REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id),
        REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id),
        REDIS_KEY_BAKING_TIME_S.format(bakery_id),
        REDIS_KEY_TIMEOUT_SEC.format(bakery_id),
        REDIS_KEY_PREP_STATE.format(bakery_id),
        REDIS_KEY_BREADS.format(bakery_id),
        REDIS_KEY_DISPLAY_CUSTOMER.format(bakery_id),
        REDIS_KEY_LAST_BREAD_TIME.format(bakery_id),
        REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id),
        REDIS_KEY_SLOTS_FOR_MULTIS.format(bakery_id),
        REDIS_KEY_SLOTS_FOR_SINGLES.format(bakery_id),
        REDIS_KEY_NEXT_TICKET.format(bakery_id),
        REDIS_KEY_LAST_SINGLE.format(bakery_id),
        REDIS_KEY_LAST_MULTI.format(bakery_id),
        REDIS_KEY_CURRENT_SERVED.format(bakery_id),
        REDIS_KEY_QUEUE_STATE.format(bakery_id),
        REDIS_KEY_SERVED_TICKETS.format(bakery_id),
        REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id),
    ]

    pipe = r.pipeline(transaction=True)
    for key in keys:
        pipe.delete(key)
    await pipe.execute()


async def calculate_ready_status(
        r, bakery_id: int, current_user_detail: dict, time_per_bread: dict,
        reservation_keys: list, reservation_number: int, reservation_dict: dict
):
    """
    Calculate if customer's breads are ready.
    Now uses actual customer_id from bread values: "timestamp:customer_id"

    Logic:
    1. No breads at all → Calculate full preparation time for all customers before + this customer
    2. Some breads exist but none for this customer → Calculate remaining time for previous customers + this customer
    3. Some breads for this customer but not all → Calculate remaining breads × average prep time
    4. All breads for this customer exist → Check if last bread is done baking
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)
    baking_time_key = REDIS_KEY_BAKING_TIME_S.format(bakery_id)

    now = time.time()
    bread_count = sum(current_user_detail.values())
    average_cook_time = sum(time_per_bread.values()) // len(time_per_bread)
    people_before = [key for key in reservation_keys if key < reservation_number]

    # Fetch all breads and baking time
    pipe = r.pipeline()
    pipe.get(baking_time_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')
    baking_time_s_raw, all_breads = await pipe.execute()

    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0

    # Parse breads: "timestamp:customer_id"
    breads_by_customer = defaultdict(list)
    for bread_value in all_breads:
        if ':' in bread_value:
            timestamp_str, cid_str = bread_value.split(':')
            customer_id = int(cid_str)
            breads_by_customer[customer_id].append(float(timestamp_str))

    # Sort timestamps for each customer (oldest first)
    for customer_id in breads_by_customer:
        breads_by_customer[customer_id].sort()

    # Get this customer's breads
    this_customer_breads = breads_by_customer.get(reservation_number, [])

    # ============================================================
    # CASE 1: No breads at all in the system
    # ============================================================
    if not all_breads:
        total_wait_s = baking_time_s

        # Add time for all customers up to and including this one
        for key in reservation_keys:
            if key > reservation_number:
                break

            breads_for_person = reservation_dict[key]
            bread_ids = list(time_per_bread.keys())
            total_wait_s += sum(
                count * time_per_bread[bread_id]
                for bread_id, count in zip(bread_ids, breads_for_person)
            )

        return False, False, int(total_wait_s)

    # ============================================================
    # CASE 2: Some breads exist, but none for this customer
    # ============================================================
    if not this_customer_breads:
        # Calculate preparation time for this customer
        preparation_time_second = sum(
            count * time_per_bread[bread_id]
            for bread_id, count in current_user_detail.items()
        )

        # Calculate remaining time for customers before this one
        total_remaining_before = 0

        for customer_id in people_before:
            customer_breads_made = len(breads_by_customer.get(customer_id, []))
            customer_total_needed = sum(reservation_dict[customer_id])

            if customer_breads_made >= customer_total_needed:
                # This customer is complete, no extra time
                continue
            elif customer_breads_made > 0:
                # Partially complete - calculate remaining breads
                breads_remaining = customer_total_needed - customer_breads_made
                total_remaining_before += breads_remaining * average_cook_time
            else:
                # No breads for this customer - full preparation time
                breads_for_person = reservation_dict[customer_id]
                bread_ids = list(time_per_bread.keys())
                total_remaining_before += sum(
                    count * time_per_bread[bread_id]
                    for bread_id, count in zip(bread_ids, breads_for_person)
                )

        total_wait_s = total_remaining_before + preparation_time_second + baking_time_s
        return False, False, int(total_wait_s)

    # ============================================================
    # CASE 3: Some breads for this customer, but not all
    # ============================================================
    if len(this_customer_breads) < bread_count:
        # Calculate remaining breads needed
        breads_remaining = bread_count - len(this_customer_breads)

        # Calculate average prep time for this customer's bread types
        breads_cook_time = [
            time_per_bread[bread_type]
            for bread_type, count in current_user_detail.items()
            if count > 0
        ]
        this_customer_average_cook_time = sum(breads_cook_time) // len(breads_cook_time)

        remaining_time = breads_remaining * this_customer_average_cook_time + baking_time_s
        return False, False, int(remaining_time)

    # ============================================================
    # CASE 4: All breads for this customer exist
    # ============================================================
    # Get the last bread's timestamp (the one that will be ready last)
    last_bread_timestamp = this_customer_breads[bread_count - 1]

    if now >= last_bread_timestamp:
        # All breads are ready!
        return True, True, None
    else:
        # Breads are still baking, return exact remaining time
        remaining_time = int(last_bread_timestamp - now)
        return False, True, remaining_time


async def consume_ready_breads(r, bakery_id: int, customer_id: int):
    """
    Remove breads for a specific customer from Redis.
    Now we know exactly which breads belong to which customer!

    Args:
        r: Redis connection
        bakery_id: Bakery ID
        customer_id: Customer ticket ID

    Returns:
        Number of breads removed
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    # Get all breads
    all_breads = await r.zrangebyscore(breads_key, '-inf', '+inf')

    if not all_breads:
        return 0

    # Find breads belonging to this customer
    customer_breads = []
    for bread_value in all_breads:
        if ':' in bread_value:
            timestamp_str, cid_str = bread_value.split(':')
            if int(cid_str) == customer_id:
                customer_breads.append(bread_value)

    if not customer_breads:
        return 0

    # Remove all breads for this customer
    removed_count = await r.zrem(breads_key, *customer_breads)
    return removed_count


async def load_breads_from_db(r, bakery_id: int):
    """
    Load today's breads from database into Redis on initialization
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    with SessionLocal() as db:
        today_breads = crud.get_today_breads(db, bakery_id)

        pipe = r.pipeline()
        pipe.delete(breads_key)

        bread_mapping = {}  # ✅ initialize here

        if today_breads:
            for bread in today_breads:
                # Get hardware customer ID from internal customer ID
                if bread.customer:
                    baked_at_timestamp = int(bread.baked_at.timestamp())
                    bread_value = f"{baked_at_timestamp}:{bread.customer.ticket_id}"
                    # Use bread.id as score (unique identifier)
                    bread_mapping[bread_value] = bread.id

            if bread_mapping:
                pipe.zadd(breads_key, bread_mapping)
                ttl = seconds_until_midnight_iran()
                pipe.expire(breads_key, ttl)

        await pipe.execute()
        print(f"Loaded {len(bread_mapping)} breads from database for bakery {bakery_id}")


async def rebuild_prep_state(r, bakery_id: int):
    """
    Rebuild preparation state from loaded breads and reservations
    Should be called after load_breads_from_db

    Logic matches new_bread endpoint:
    - If customer incomplete: set prep_state to "customer_id:bread_count"
    - If all customers complete: keep last customer with full count to prevent restart
    - If no customers: delete prep_state
    """
    prep_state_key = REDIS_KEY_PREP_STATE.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.zrange(order_key, 0, -1)
    pipe.hgetall(res_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')

    order_ids, reservations_map, all_breads = await pipe.execute()

    if not order_ids or not reservations_map:
        # No customers at all
        await r.delete(prep_state_key)
        print(f"No customers in queue for bakery {bakery_id}, cleared prep_state")
        return

    order_ids = [int(x) for x in order_ids]
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservations_map.items()}

    # Count breads per customer
    breads_per_customer = defaultdict(int)
    for bread_value in all_breads:
        if ':' in bread_value:
            cid = int(bread_value.split(':')[1])
            breads_per_customer[cid] += 1

    ttl = seconds_until_midnight_iran()

    # Find first incomplete customer
    for customer_id in order_ids:
        total_needed = sum(reservation_dict[customer_id])
        already_made = breads_per_customer.get(customer_id, 0)

        if already_made < total_needed:
            # This customer is still in preparation
            await r.set(prep_state_key, f"{customer_id}:{already_made}", ex=ttl)
            print(
                f"Rebuilt prep_state for bakery {bakery_id}: customer {customer_id} has {already_made}/{total_needed} breads")
            return

    # All customers complete - keep last customer to prevent restart
    last_customer_id = order_ids[-1]
    last_customer_total = sum(reservation_dict[last_customer_id])
    await r.set(prep_state_key, f"{last_customer_id}:{last_customer_total}", ex=ttl)
    print(
        f"All customers complete for bakery {bakery_id}, set prep_state to last customer {last_customer_id}:{last_customer_total}")


async def set_display_flag(r, bakery_id: int):
    """
    Set flag to show customer info on display.
    This means baker should see bread requirements for next customer.
    """
    display_key = REDIS_KEY_DISPLAY_CUSTOMER.format(bakery_id)
    ttl = seconds_until_midnight_iran()
    await r.set(display_key, "1", ex=ttl)


async def clear_display_flag(r, bakery_id: int):
    """Clear the display flag (baker started baking)."""
    display_key = REDIS_KEY_DISPLAY_CUSTOMER.format(bakery_id)
    await r.delete(display_key)


async def should_show_on_display(r, bakery_id: int) -> bool:
    """
    Check if we should show customer info on display.
    True if: display flag is set (no breads being prepared)
    """
    display_key = REDIS_KEY_DISPLAY_CUSTOMER.format(bakery_id)
    has_display_flag = await r.exists(display_key)
    return bool(has_display_flag)


async def consume_display_flag(r, bakery_id: int) -> bool:
    """Atomically read and clear the display flag.

    Used by new_ticket so that only the *first* ticket after idle
    gets show_on_display = True. Subsequent tickets before baking
    will see False.
    """
    display_key = REDIS_KEY_DISPLAY_CUSTOMER.format(bakery_id)
    pipe = r.pipeline()
    pipe.exists(display_key)
    pipe.delete(display_key)
    has_flag, _ = await pipe.execute()
    return bool(has_flag)


async def rebuild_display_state(r, bakery_id: int):
    """
    Rebuild display state after server restart.
    
    Logic:
    - If breads are being prepared: clear flag (baker is working)
    - If no breads being prepared: set flag (show display)
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)
    bread_count = await r.zcard(breads_key)
    
    if bread_count == 0:
        # No breads being prepared - set flag to show display
        await set_display_flag(r, bakery_id)
        print(f"Set display flag for bakery {bakery_id} (no breads in preparation)")
    else:
        # Breads are being prepared - clear flag
        await clear_display_flag(r, bakery_id)
        print(f"Cleared display flag for bakery {bakery_id} ({bread_count} breads in preparation)")
    pipe.delete(display_key)
    has_flag, _ = await pipe.execute()
    return bool(has_flag)


async def rebuild_display_state(r, bakery_id: int):
    """
    Rebuild display state after server restart.
    
    Logic:
    - If breads are being prepared: clear flag (baker is working)
    - If no breads being prepared: set flag (show display)
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)
    bread_count = await r.zcard(breads_key)
    
    if bread_count == 0:
        # No breads being prepared - set flag to show display
        await set_display_flag(r, bakery_id)
        print(f"Set display flag for bakery {bakery_id} (no breads in preparation)")
    else:
        # Breads are being prepared - clear flag
        await clear_display_flag(r, bakery_id)
        print(f"Cleared display flag for bakery {bakery_id} ({bread_count} breads in preparation)")
