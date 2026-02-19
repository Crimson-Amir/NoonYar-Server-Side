import functools, requests
import json
from application import crud
from celery import Celery
from datetime import datetime
from pytz import UTC
from application.logger_config import celery_logger
from application.database import SessionLocal
from application.setting import settings
import traceback, redis
from uuid import uuid4
from application.auth import OTPStore
from application.helpers import redis_helper
from redis import asyncio as aioredis
import asyncio
from contextlib import contextmanager

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=None
)


@celery_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Run every 5 seconds to dispatch any ready tickets to wait list.
    if settings.ENABLE_AUTO_DISPATCH_READY_TICKETS:
        sender.add_periodic_task(5.0, auto_dispatch_ready_tickets.s(), name="auto_dispatch_ready_tickets_every_5s")
        celery_logger.info("Periodic auto-dispatch is enabled")
    else:
        celery_logger.info("Periodic auto-dispatch is disabled by configuration")


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def report_to_admin_api(msg, message_thread_id=settings.ERR_THREAD_ID, parse_mode: str | None = None):
    json_data = {'chat_id': settings.TELEGRAM_CHAT_ID, 'text': msg[:4096], 'message_thread_id': message_thread_id}
    if parse_mode:
        json_data['parse_mode'] = parse_mode
    proxies = None
    if settings.TELEGRAM_PROXY_URL:
        proxies = {
            "http": settings.TELEGRAM_PROXY_URL,
            "https": settings.TELEGRAM_PROXY_URL,
        }

    response = requests.post(
        url=f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
        json=json_data,
        timeout=10,
        # proxies=proxies,
    )
    response.raise_for_status()

def handle_task_errors(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            retries = getattr(self.request, "retries", None)
            max_retries = getattr(self, "max_retries", None)
            error_id = uuid4().hex
            tb = traceback.format_exc()

            celery_logger.error(
                f"Celery task {func.__name__} failed",
                extra={"error": str(e), "traceback": tb},
            )

            err_msg = (
                f"[🔴 ERROR] Celery task: {func.__name__}"
                f"\n\nType: {type(e)}"
                f"\nReason: {str(e)}"
                f"\nRetries: {retries}/{max_retries}"
                f"\nError ID: {error_id}"
            )

            report_to_admin_api.delay(err_msg)
            raise
    return wrapper


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def register_new_customer(self, customer_ticket_id, bakery_id, bread_requirements, customer_in_upcoming_customer=False, token: str | None = None, note: str | None = None):
    with session_scope() as db:
        c_id = crud.new_customer_no_commit(db, customer_ticket_id, bakery_id, True, token, note)
        crud.new_bread_customers(db, c_id, bread_requirements)
        if customer_in_upcoming_customer:
            crud.new_customer_to_upcoming_customers(db, c_id)

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def remove_customer_from_upcoming_customers(self, customer_ticket_id, bakery_id):
    with session_scope() as db:
        crud.remove_upcoming_customer(db, customer_ticket_id, bakery_id)

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def next_ticket_process(self, ticket_id, bakery_id):
    with session_scope() as db:
        crud.update_customer_status_to_false(db, ticket_id, bakery_id)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def serve_wait_list_ticket(self, ticket_id, bakery_id):
    with session_scope() as db:
        crud.update_wait_list_customer_status(db, ticket_id, bakery_id, False)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def send_ticket_to_wait_list(self, ticket_id, bakery_id, source: str = "system"):
    celery_logger.info(
        "send_ticket_to_wait_list started",
        extra={"bakery_id": int(bakery_id), "ticket_id": int(ticket_id), "source": str(source)},
    )
    with session_scope() as db:
        customer_id = crud.update_customer_status_to_false(db, ticket_id, bakery_id)
        if customer_id is None:
            customer = crud.get_customer_by_ticket_id_any_status(db, ticket_id, bakery_id)
            customer_id = customer.id if customer else None

        if customer_id is None:
            raise ValueError(f"Customer not found for ticket_id={ticket_id}, bakery_id={bakery_id}")

        crud.add_new_ticket_to_wait_list(db, customer_id, True)

    celery_logger.info(
        "send_ticket_to_wait_list persisted to DB",
        extra={"bakery_id": int(bakery_id), "ticket_id": int(ticket_id), "customer_id": int(customer_id)},
    )

    msg = (
        f"📌 Ticket Sent To Wait List"
        f"\n• Bakery Id: {int(bakery_id)}"
        f"\n• Ticket Number: {int(ticket_id)}"
        f"\n• Source: {str(source)}"
    )
    report_to_admin_api(msg, settings.BAKERY_TICKET_THREAD_ID)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def send_otp(self, mobile_number, code, expire_m=10):
    # url = f"https://api.sms.ir/v1/send/verify"
    # data = {
    #     "mobile": str(mobile_number),
    #     "templateId": "123456",
    #     "parameters": [{"name": "code", "value": str(code)}]
    # }
    # headers = {
    #     "ACCEPT": "application/json",
    #     "X-API-KEY": settings.SMS_KEY
    # }
    # response = requests.post(url, json=data, headers=headers, timeout=10)
    # if response.status_code == 200:
    r = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True
    )
    try:
        otp_store = OTPStore(r)
        otp_store.set_otp(mobile_number, code, expire_m * 60)
    finally:
        r.close()
        # response_json = response.json()
        # return {"status": response_json['status'], "message": "OTP sent successfully",
        #         "message_id": response_json["data"]["messageId"], "code": code}










@celery_app.task(bind=True)
@handle_task_errors
def schedule_auto_dispatch(self, bakery_id: int, countdown_s: int = 0):
    """Schedule a one-shot auto-dispatch check using Celery countdown."""
    delay = max(0, int(countdown_s or 0))
    auto_dispatch_ready_tickets.apply_async(kwargs={"bakery_id": int(bakery_id)}, countdown=delay)


@celery_app.task(bind=True)
@handle_task_errors
def auto_dispatch_ready_tickets(self, bakery_id: int | None = None):

    async def _task(target_bakery_id: int | None):
        r = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True
        )
        try:
            with SessionLocal() as session:
                bakeries = crud.get_all_active_bakeries(session)

            target_bakery_ids = []
            if target_bakery_id is not None:
                target_bakery_ids = [int(target_bakery_id)]
            else:
                for bakery in bakeries or []:
                    target_bakery_ids.append(int(getattr(bakery, "bakery_id", bakery)))

            for current_bakery_id in target_bakery_ids:
                await redis_helper.rebuild_prep_state(r, current_bakery_id)
                lock_key = f"bakery:{current_bakery_id}:auto_dispatch_lock"
                lock_token = uuid4().hex
                acquired = await r.set(lock_key, lock_token, nx=True, ex=10)
                if not acquired:
                    celery_logger.info(
                        "auto_dispatch_ready_tickets skipped because lock is held",
                        extra={"bakery_id": current_bakery_id, "lock_key": lock_key},
                    )
                    continue

                try:
                    best = await redis_helper.select_best_ticket_by_ready_time(r, current_bakery_id)
                    if not best or not bool(best.get("ready")):
                        celery_logger.info(
                            "auto_dispatch_ready_tickets no ready ticket",
                            extra={"bakery_id": current_bakery_id, "best": best},
                        )
                        continue

                    ticket_id = int(best["ticket_id"])

                    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(current_bakery_id)
                    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(current_bakery_id)

                    pipe = r.pipeline()
                    pipe.hget(res_key, str(ticket_id))
                    pipe.hdel(res_key, ticket_id)
                    pipe.zrem(order_key, ticket_id)
                    current_customer_reservation, r1, r2 = await pipe.execute()

                    if not bool(r1 and r2):
                        reservation_list = await redis_helper.get_bakery_reservations(
                            r, current_bakery_id, fetch_from_redis_first=False
                        )
                        if not reservation_list:
                            continue
                        status, current_customer_reservation = await redis_helper.remove_customer_id_from_reservation(
                            r, current_bakery_id, ticket_id
                        )
                        if not status:
                            continue

                    queue_state = await redis_helper.load_queue_state(r, current_bakery_id)
                    queue_state.mark_ticket_served(ticket_id)
                    await redis_helper.save_queue_state(r, current_bakery_id, queue_state)

                    await redis_helper.add_customer_to_wait_list(
                        r, current_bakery_id, ticket_id, reservations_str=current_customer_reservation
                    )
                    await redis_helper.set_user_current_ticket(r, current_bakery_id, ticket_id)
                    await redis_helper.consume_ready_breads(r, current_bakery_id, ticket_id)
                    await redis_helper.rebuild_prep_state(r, current_bakery_id)

                    _, time_per_bread, upcoming_breads = await redis_helper.get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(
                        r, current_bakery_id
                    )

                    from application import mqtt_client
                    await mqtt_client.publish_ticket_job_background(
                        bakery_id=current_bakery_id,
                        ticket_id=ticket_id,
                        token="does not matter",
                        print_ticket=False,
                        show_on_display=True,
                    )

                    db_waitlist_task = send_ticket_to_wait_list.delay(ticket_id, current_bakery_id, "auto_dispatch")
                    celery_logger.info(
                        "auto_dispatch_ready_tickets moved ticket to wait list",
                        extra={
                            "bakery_id": current_bakery_id,
                            "ticket_id": ticket_id,
                            "db_waitlist_task_id": db_waitlist_task.id,
                        },
                    )

                    if time_per_bread and any(bread in time_per_bread.keys() for bread in (upcoming_breads or [])):
                        await redis_helper.remove_customer_from_upcoming_customers(r, current_bakery_id, ticket_id)
                        remove_customer_from_upcoming_customers.delay(ticket_id, current_bakery_id)

                    with SessionLocal() as db:
                        crud.consume_breads_for_customer_today(db, current_bakery_id, ticket_id)

                    msg = (
                        f"Bakery ID: {current_bakery_id}"
                        f"\nTicket Number: {ticket_id}"
                        f"\nAction: auto-dispatch to wait list"
                    )
                    report_to_admin_api(msg, settings.BAKERY_TICKET_THREAD_ID)
                finally:
                    current_token = await r.get(lock_key)
                    if current_token == lock_token:
                        await r.delete(lock_key)

        finally:
            await r.close()

    asyncio.run(_task(bakery_id))


@celery_app.task(bind=True)
@handle_task_errors
def initialize_bakeries_redis_sets(self, mid_night):
    with SessionLocal() as session:
        all_bakeries = crud.get_all_active_bakeries(session)
        for bakery in all_bakeries:
            initialize_bakery_redis_sets.delay(bakery.bakery_id, mid_night=mid_night)

# TODO: make this fucntion standard
@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 1, "countdown": 5}, max_retries=1)
@handle_task_errors
def initialize_bakery_redis_sets(self, bakery_id, mid_night=False):
    async def _task():
        r = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True
        )
        try:
            await redis_helper.initialize_redis_sets(r, bakery_id)
            if mid_night:
                await redis_helper.initialize_redis_sets_only_12_oclock(r, bakery_id)
                with session_scope() as db:
                    crud.update_all_customers_status_to_false(db, bakery_id)
        finally:
            await r.close()

    asyncio.run(_task())


@celery_app.task(bind=True)
@handle_task_errors
def change_bakeries_time_per_bread(self):
    with SessionLocal() as session:
        all_bakeries = crud.get_all_active_bakeries(session)
        for bakery in all_bakeries:
            calculate_new_time_per_bread.delay(bakery.bakery_id)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def calculate_new_time_per_bread(self, bakery_id):
    r = redis.from_url(settings.REDIS_URL, decode_responses=True)

    bread_diff_key = redis_helper.REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    pipe = r.pipeline()
    pipe.zrange(bread_diff_key, 0, -1, withscores=True)
    pipe.hgetall(time_key)
    zitems, time_per_bread_raw = pipe.execute()

    if not zitems:
        return None

    if not time_per_bread_raw:
        raise ValueError("time_per_bread is empty")

    time_per_bread = {k: int(v) for k, v in time_per_bread_raw.items()}
    time_per_bread_values = list(time_per_bread.values())

    time_diffs_clean = [int(td) for _, td in zitems if 20 <= int(td) <= 80]

    if len(time_diffs_clean) >= 15:
        average_time_diff = sum(time_diffs_clean) // len(time_diffs_clean)
        current_average_time = sum(time_per_bread_values) // len(time_per_bread_values)
        differnet_second =  average_time_diff - current_average_time

        with session_scope() as db:
            crud.new_cook_avreage_time(db, bakery_id, average_time_diff)
            all_bakery_breads = crud.get_bakery_breads(db, bakery_id)
            for bread in all_bakery_breads:
                new_cook_time = max(20, min(80, bread.preparation_time + differnet_second))
                crud.update_bread_bakery_no_commit(db, bakery_id, bread.bread_type_id, new_cook_time)
            redis_helper.reset_time_per_bread_sync(r, db, bakery_id)

    r.zrem(bread_diff_key, *[bread_index for bread_index, _ in zitems])


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def save_bread_to_db(self, ticket_id, bakery_id, baked_at_timestamp):
    with session_scope() as db:
        customer_id = None
        consumed = True
        if ticket_id is not None and ticket_id != 0:
            customer = crud.get_customer_by_ticket_id(db, int(ticket_id), int(bakery_id))
            if customer:
                consumed = False
                customer_id = customer.id

        baked_at = datetime.fromtimestamp(baked_at_timestamp, tz=UTC)
        crud.create_bread(db, bakery_id, customer_id, baked_at, consumed)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def log_urgent_inject(self, bakery_id: int, urgent_id: str, ticket_id: int | None, bread_requirements: dict, reason: str | None = None):
    with session_scope() as db:
        bread_map = {str(k): int(v) for k, v in (bread_requirements or {}).items()}
        crud.create_urgent_bread_log(
            db,
            bakery_id=int(bakery_id),
            urgent_id=str(urgent_id),
            ticket_id=int(ticket_id) if ticket_id is not None else None,
            status="PENDING",
            original_breads=bread_map,
            remaining_breads=bread_map,
            reason=str(reason or ""),
        )


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def log_urgent_edit(self, bakery_id: int, urgent_id: str, bread_requirements: dict, reason: str | None = None):
    with session_scope() as db:
        bread_map = {str(k): int(v) for k, v in (bread_requirements or {}).items()}
        ok = crud.update_urgent_bread_log(
            db,
            bakery_id=int(bakery_id),
            urgent_id=str(urgent_id),
            status="PENDING",
            original_breads=bread_map,
            remaining_breads=bread_map,
            reason=(None if reason is None else str(reason or "")),
        )
        if not ok:
            crud.create_urgent_bread_log(
                db,
                bakery_id=int(bakery_id),
                urgent_id=str(urgent_id),
                ticket_id=None,
                status="PENDING",
                original_breads=bread_map,
                remaining_breads=bread_map,
                reason=str(reason or ""),
            )


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def log_urgent_cancel(self, bakery_id: int, urgent_id: str):
    with session_scope() as db:
        ok = crud.update_urgent_bread_log(
            db,
            bakery_id=int(bakery_id),
            urgent_id=str(urgent_id),
            status="CANCELLED",
            cancelled=True,
        )
        if not ok:
            crud.create_urgent_bread_log(
                db,
                bakery_id=int(bakery_id),
                urgent_id=str(urgent_id),
                ticket_id=None,
                status="CANCELLED",
                original_breads={},
                remaining_breads={},
            )


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def log_urgent_processing(self, bakery_id: int, urgent_id: str):
    with session_scope() as db:
        ok = crud.update_urgent_bread_log(
            db,
            bakery_id=int(bakery_id),
            urgent_id=str(urgent_id),
            status="PROCESSING",
        )
        if not ok:
            crud.create_urgent_bread_log(
                db,
                bakery_id=int(bakery_id),
                urgent_id=str(urgent_id),
                ticket_id=None,
                status="PROCESSING",
                original_breads={},
                remaining_breads={},
            )


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def log_urgent_remaining(self, bakery_id: int, urgent_id: str, remaining_breads: dict | None, done: bool = False):
    with session_scope() as db:
        remaining_map = {str(k): int(v) for k, v in (remaining_breads or {}).items()}
        crud.update_urgent_bread_log(
            db,
            bakery_id=int(bakery_id),
            urgent_id=str(urgent_id),
            status="DONE" if done else "PROCESSING",
            remaining_breads=remaining_map,
            done=bool(done),
        )
