import crud, requests, algorithm, utilities
from celery import Celery
from database import SessionLocal
from private import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ERR_THREAD_ID
from private import SMS_KEY
import redis, traceback
from uuid import uuid4
from logger_config import logger

celery_app = Celery(
    "tasks",
    broker="pyamqp://guest@localhost//"
)

def log_and_report_error(context: str, error: Exception, extra: dict = None):
    tb = traceback.format_exc()
    error_id = uuid4().hex
    extra["error_id"] = error_id
    logger.error(
        context, extra={"error": str(error), "traceback": tb, **extra}
    )
    report_error_telegram.delay(context, error.__class__.__name__, str(error), extra)


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def report_error_telegram(context, err_type, err_str, extra: dict = None):
    """
    Object of type ZeroDivisionError is not JSON serializable--cant send error object directly
    """
    err = (
        f"🔴 {context}:"
        f"\n\nError type: {err_type}"
        f"\nError reason: {err_str}"
        f"\n\nExtera Info:"
        f"\n{extra}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": err, 'message_thread_id': ERR_THREAD_ID}
    requests.post(url, data=data, timeout=5)


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def register_new_customer(customer_ticket_id, bakery_id, bread_requirements):
    db = SessionLocal()
    try:
        c_id = crud.new_customer_no_commit(db, customer_ticket_id, bakery_id, True)
        for bread_id, count in bread_requirements.items():
            crud.new_bread_customer(db, c_id, int(bread_id), count)
        db.commit()
    except Exception as e:
        db.rollback()
        log_and_report_error(
            "Celery task: register_new_customer", e,
            {"hardware_customer_id": customer_ticket_id,
             "bakery_id": bakery_id, "bread_requirements": bread_requirements}
        )
        raise e
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def next_ticket_process(hardware_customer_id, bakery_id):
    db = SessionLocal()
    try:
        crud.update_customers_status(db, hardware_customer_id, bakery_id, False)
        db.commit()
    except Exception as e:
        db.rollback()
        log_and_report_error(
            "Celery task: next_ticket_process", e,
            {"hardware_customer_id": hardware_customer_id, "bakery_id": bakery_id}
        )
        raise e
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def send_otp(mobile_number, code, expire_m=10):
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
            otp_store = utilities.OTPStore(r)
            otp_store.set_otp(mobile_number, code, expire_m * 60)
            response_json = response.json()
            return {"status": response_json['status'], "message": "OTP sent successfully",
                    "message_id": response_json["data"]["messageId"], "code": code}
        
        raise Exception(f"Failed to send OTP: {response.status_code} - {response.text}")
    except Exception as e:
        db.rollback()
        log_and_report_error(
            "Celery task: send_otp", e, {"mobile_number": mobile_number}
        )
        raise e
    finally:
        db.close()
