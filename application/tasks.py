import functools, requests
from application import crud
from celery import Celery
from application.logger_config import celery_logger
from application.database import SessionLocal
from application.setting import settings
import traceback, redis
from uuid import uuid4
from application.helpers import token_helpers, redis_helper
from redis import asyncio as aioredis
import asyncio
from contextlib import contextmanager

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=None
)

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
def report_to_admin_api(msg, message_thread_id=settings.ERR_THREAD_ID):
    json_data = {'chat_id': settings.TELEGRAM_CHAT_ID, 'text': msg[:4096], 'message_thread_id': message_thread_id}
    requests.post(
        url=f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
        json=json_data,
        timeout=10
    )

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
def register_new_customer(self, customer_ticket_id, bakery_id, bread_requirements, customer_in_upcoming_customer=False):
    with session_scope() as db:
        c_id = crud.new_customer_no_commit(db, customer_ticket_id, bakery_id, True)
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
def next_ticket_process(self, hardware_customer_id, bakery_id):
    with session_scope() as db:
        crud.update_customers_status(db, hardware_customer_id, bakery_id, False)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def serve_wait_list_ticket(self, hardware_customer_id, bakery_id):
    with session_scope() as db:
        crud.update_wait_list_customer_status(db, hardware_customer_id, bakery_id, False)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def send_ticket_to_wait_list(self, hardware_customer_id, bakery_id):
    with session_scope() as db:
        customer = crud.update_customers_status(db, hardware_customer_id, bakery_id, False)
        customer_id = max(row[0] for row in customer)
        crud.add_new_ticket_to_wait_list(db, customer_id, True)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def send_otp(self, mobile_number, code, expire_m=10):
    celery_logger.info("hello")
    url = f"https://api.sms.ir/v1/send/verify"
    data = {
        "mobile": str(mobile_number),
        "templateId": "123456",
        "parameters": [{"name": "code", "value": str(code)}]
    }
    headers = {
        "ACCEPT": "application/json",
        "X-API-KEY": settings.SMS_KEY
    }
    response = requests.post(url, json=data, headers=headers, timeout=10)
    if response.status_code == 200:
        r = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True
        )
        try:
            otp_store = token_helpers.OTPStore(r)
            otp_store.set_otp(mobile_number, code, expire_m * 60)
        finally:
            r.close()
        response_json = response.json()
        return {"status": response_json['status'], "message": "OTP sent successfully",
                "message_id": response_json["data"]["messageId"], "code": code}

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
                new_cook_time = max(20, min(80, bread.cook_time_s + differnet_second))
                crud.update_bread_bakery_no_commit(db, bakery_id, bread.bread_type_id, new_cook_time)
            redis_helper.reset_time_per_bread_sync(r, db, bakery_id)

    r.zrem(bread_diff_key, *[bread_index for bread_index, _ in zitems])
