from datetime import datetime
import pytz, crud
from celery import Celery
from database import SessionLocal

celery_app = Celery("tasks", broker="pyamqp://guest@localhost//")


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
def register_new_customer(customer_id, bakery_id, bread_requirements):
    db = SessionLocal()
    try:
        c_id = crud.new_customer_no_commit(db, customer_id, bakery_id, datetime.now(pytz.timezone('Asia/Tehran')))
        for bread in bread_requirements.items():
            pass
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

    finally:
        db.close()
