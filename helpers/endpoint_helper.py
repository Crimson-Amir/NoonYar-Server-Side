from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from tasks import report_to_admin_api
import traceback
from uuid import uuid4
from logger_config import logger

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

from functools import wraps

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

def db_transaction(context: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, db: Session = Depends(get_db), **kwargs):
            try:
                return await func(*args, db=db, **kwargs)
            except HTTPException as e:
                raise e
            except Exception as e:
                db.rollback()
                log_and_report_error(f"{context}:{func.__name__}", e, extra={})
                raise HTTPException(status_code=500, detail={
                    "message": "Internal server error",
                    "type": type(e).__name__,
                    "reason": str(e)
                })
        return wrapper
    return decorator

def handle_endpoint_errors(context: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException as e:
                raise e
            except Exception as e:
                log_and_report_error(f"{context}:{func.__name__}", e, extra={})
                raise HTTPException(status_code=500, detail={
                    "message": "Internal server error",
                    "type": type(e).__name__,
                    "reason": str(e)
                })
        return wrapper
    return decorator

def raise_empty_queue_exception():
    raise HTTPException(status_code=404, detail={'status': 'emptyQueue', "status_code": 3})
