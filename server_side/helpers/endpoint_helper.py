from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

from functools import wraps

def db_transaction(func):
    @wraps(func)
    async def wrapper(*args, db: Session = Depends(get_db), **kwargs):
        try:
            return await func(*args, db=db, **kwargs)
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Error: {type(e).__name__}: {str(e)}"
            )
    return wrapper

