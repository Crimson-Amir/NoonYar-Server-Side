import functools
import crud, requests
from celery import Celery
from database import SessionLocal
from private import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ERR_THREAD_ID
from private import SMS_KEY
import traceback, redis
from uuid import uuid4
from logger_config import logger
from helpers import token_helpers, redis_helper
from redis import asyncio as aioredis
import asyncio

celery_app = Celery(
    "tasks",
    broker="pyamqp://guest@localhost//"
)

def log_and_report_error(context: str, error: Exception, extra: dict = None):
    tb = traceback.format_exc()
    error_id = uuid4().hex
    extra = extra or {}
    extra["error_id"] = error_id
    logger.error(
        context, extra={"error": str(error), "traceback": tb, **extra}
    )
    err_msg = (
        f"[🔴 ERROR] {context}:"
        f"\n\nError type: {type(error)}"
        f"\nError reason: {str(error)}"
        f"\n\nExtera Info:"
        f"\n{extra}"
    )
    report_to_admin_api.delay(err_msg)


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def report_to_admin_api(msg, message_thread_id=ERR_THREAD_ID):
    json_data = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg[:4096], 'message_thread_id': message_thread_id}
    requests.post(
        url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
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

            log_and_report_error(
                f"Celery task: {func.__name__}",
                e,
                extra={
                    "_args": args,
                    "_kwargs": kwargs,
                    "retries": retries,
                    "max_retries": max_retries,
                }
            )
            raise
    return wrapper


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def register_new_customer(self, customer_ticket_id, bakery_id, bread_requirements):
    db = SessionLocal()
    try:
        c_id = crud.new_customer_no_commit(db, customer_ticket_id, bakery_id, True)
        crud.new_bread_customers(db, c_id, bread_requirements)
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def next_ticket_process(self, hardware_customer_id, bakery_id):
    db = SessionLocal()
    try:
        crud.update_customers_status(db, hardware_customer_id, bakery_id, False)
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def skipped_ticket_proccess(self, hardware_customer_id, bakery_id):
    db = SessionLocal()
    try:
        crud.update_skipped_customers_status(db, hardware_customer_id, bakery_id, False)
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def skip_customer(self, hardware_customer_id, bakery_id):
    db = SessionLocal()
    try:
        customer = crud.update_customers_status(db, hardware_customer_id, bakery_id, False)
        customer_id = max(row[0] for row in customer)
        crud.add_new_skipped_customer(db, customer_id, True)
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def send_otp(self, mobile_number, code, expire_m=10):
    db = SessionLocal()
    try:
        url = f"https://api.sms.ir/v1/send/verify"
        data = {
            "mobile": str(mobile_number),
            "templateId": "123456",
            "parameters": [{"name": "code", "value": str(code)}]
        }
        headers = {
            "ACCEPT": "application/json",
            "X-API-KEY": SMS_KEY
        }
        response = requests.post(url, json=data, headers=headers, timeout=10)
        if response.status_code == 200:
            r = redis.Redis(host="localhost", port=6379, decode_responses=True)
            try:
                otp_store = token_helpers.OTPStore(r)
                otp_store.set_otp(mobile_number, code, expire_m * 60)
            finally:
                r.close()
            response_json = response.json()
            return {"status": response_json['status'], "message": "OTP sent successfully",
                    "message_id": response_json["data"]["messageId"], "code": code}
        
        raise Exception(f"Failed to send OTP: {response.status_code} - {response.text}")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@celery_app.task(bind=True)
def initialize_bakeries_redis_sets(self):
    with SessionLocal() as session:
        all_bakeries = crud.get_all_bakeries(session)
        print(all_bakeries)
        for bakery in all_bakeries:
            initialize_bakery_redis_sets.delay(bakery.bakery_id)


# TODO: make this fucntion standard
@celery_app.task(bind=True)
def initialize_bakery_redis_sets(self, bakery_id):
    async def _task():
        r = aioredis.from_url("redis://localhost:6379", decode_responses=True)
        try:
            await redis_helper.initialize_redis_sets(r, bakery_id)
        finally:
            await r.close()

    asyncio.run(_task())
