from datetime import datetime
import pytz, crud, requests, traceback
from celery import Celery
from database import SessionLocal
from private import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ERR_THREAD_ID

celery_app = Celery("tasks", broker="pyamqp://guest@localhost//")


def report_error_telegram(func_name, error, tb, message):
    err = (
        f"🔴 An error occurred in {func_name}:"
        f"\n\n{message}"
        f"\n\nerror type:{type(error)}"
        f"\nerror reason: {str(error)}"
        f"\n\nTraceback: \n{tb}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": err, 'message_thread_id': ERR_THREAD_ID}
    requests.post(url, data=data, timeout=5)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def register_new_customer(hardware_customer_id, bakery_id, bread_requirements):
    db = SessionLocal()
    try:
        c_id = crud.new_customer_no_commit(
            db, hardware_customer_id, bakery_id, True,
            datetime.now(pytz.timezone('Asia/Tehran'))
        )
        for bread_id, count in bread_requirements.items():
            crud.new_bread_customer(db, c_id, bread_id, count)
        db.commit()
    except Exception as e:
        db.rollback()
        tb = traceback.format_exc()
        report_error_telegram(
            'celery task: register_new_customer', e, tb,
            f'hardware_customer_id: {hardware_customer_id}'
            f'\nbakery_id: {bakery_id}'
            f'\nbread_requirements: {bread_requirements}')
        raise e
    finally:
        db.close()
