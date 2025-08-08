from logger_config import setup_logger
import crud, requests, traceback, algorithm, utilities
from celery import Celery
from database import SessionLocal
from private import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ERR_THREAD_ID
from private import SMS_KEY

celery_app = Celery(
    "tasks",
    broker="pyamqp://guest@localhost//"
    )
logger = setup_logger('tasks_log')

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


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def initialize(bakery_id, bread_type_and_cook_time):
    db = SessionLocal()
    try:
        crud.delete_all_corresponding_bakery_bread(db, bakery_id)
        crud.add_bakery_bread_entries(db, bakery_id, bread_type_and_cook_time)
        db.commit()
    except Exception as e:
        db.rollback()
        tb = traceback.format_exc()
        logger.error('error in initialize!', exc_info=True)
        report_error_telegram(
            'celery task: initialize', e, tb,f'bakery_id: {bakery_id}\ndict: {bread_type_and_cook_time}')
        raise e
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def register_new_customer(hardware_customer_id, bakery_id, bread_requirements):
    db = SessionLocal()
    try:
        data = algorithm.add_customer_to_reservation_dict(bakery_id, hardware_customer_id, bread_requirements)
        c_id = crud.new_customer_no_commit(db, hardware_customer_id, bakery_id, True)
        for bread_id, count in bread_requirements.items():
            crud.new_bread_customer(db, c_id, int(bread_id), count)
        db.commit()
        return data
    except Exception as e:
        db.rollback()
        tb = traceback.format_exc()
        logger.error(f'error in register_new_customer. b_id: {bakery_id}, bread_t: {bread_requirements}!', exc_info=True)
        report_error_telegram(
            'celery task: register_new_customer', e, tb,
            f'hardware_customer_id: {hardware_customer_id}'
            f'\nbakery_id: {bakery_id}'
            f'\nbread_requirements: {bread_requirements}')
        raise e
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def next_ticket_process(hardware_customer_id, bakery_id):
    db = SessionLocal()
    try:
        crud.update_customer_status(db, hardware_customer_id, bakery_id, False)
        data = algorithm.remove_customer_from_reservation_dict(bakery_id, hardware_customer_id)
        db.commit()
        return data
    except Exception as e:
        db.rollback()
        tb = traceback.format_exc()
        logger.error(f'error in next_ticket_process', exc_info=True)
        report_error_telegram(
            'celery task: next_ticket_process', e, tb,
            f'hardware_customer_id: {hardware_customer_id}'
            f'\nbakery_id: {bakery_id}')
        raise e
    finally:
        db.close()


@celery_app.task(autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def send_OTP(mobile_number, code, expire=10):
    db = SessionLocal()
    hashed_otp = utilities.hash_otp(code)
    try:
        url = f"https://api.sms.ir/v1/send/verify"
        data = {"mobile": str(mobile_number), "templateId": "123456", "parameters": [{"name": "code", "value": str(code)}]}
        headers = {
            "ACCEPT": "application/json",
            "X-API-KEY": SMS_KEY
        }
        response = requests.post(url, json=data, headers=headers, timeout=10)
        if response.status_code == 200:
            crud.invalidate_old_otps(db, mobile_number)
            crud.add_otp_to_db(db, mobile_number, hashed_otp, True, utilities.get_expiry(expire))
            db.commit()
            response_json = response.json()
            return {"status": response_json['status'], "message": "OTP sent successfully",
                    "message_id": response_json["data"]["messageId"], "code": code}
        
        raise Exception(f"Failed to send OTP: {response.status_code} - {response.text}")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
