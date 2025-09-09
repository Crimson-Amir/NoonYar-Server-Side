from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from tasks import log_and_report_error

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

from functools import wraps

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
                    "detail": "Internal server error",
                    "error_type": type(e).__name__,
                    "error_reason": str(e)
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
                    "detail": "Internal server error",
                    "error_type": type(e).__name__,
                    "error_reason": str(e)
                })
        return wrapper
    return decorator

