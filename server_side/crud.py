from sqlalchemy.orm import Session
from sqlalchemy import update, insert, delete
import models, schemas
from auth import hash_password_md5
import pytz
from sqlalchemy import asc
from datetime import datetime, time, UTC


def get_user_by_phone_number(db: Session, phone_number: str):
    return db.query(models.User).filter(models.User.phone_number == phone_number).first()


def is_user_admin(db: Session, user_id: str):
    return db.query(models.Admin).filter_by(user_id=user_id, active=True).first()

def get_bakery_breads(db: Session, bakery_id: int):
    return db.query(models.BakeryBread).filter(models.BakeryBread.bakery_id == bakery_id).order_by(asc(models.BakeryBread.bread_type_id)).all()

def get_bakery(db: Session, bakery_id: int):
    return db.query(models.Bakery).filter(models.Bakery.bakery_id == bakery_id).first()

def get_today_customers(db: Session, bakery_id: int):
    tehran = pytz.timezone('Asia/Tehran')
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    return db.query(models.Customer).filter(
        models.Customer.bakery_id == bakery_id,
        models.Customer.register_date >= midnight_utc,
        models.Customer.is_in_queue == True).all()

def delete_all_corresponding_bakery_bread(db: Session, bakery_id: int):
    db.query(models.BakeryBread).filter(models.BakeryBread.bakery_id == bakery_id).delete()

def get_otp(db: Session, phone_number: str):
    otp_entry = db.query(models.OTP).filter(
        models.OTP.phone_number == phone_number,
        models.OTP.valid == True,
        models.OTP.exception_at > datetime.now(UTC)
    ).order_by(models.OTP.register_date.desc()).first()
    return otp_entry

def invalidate_old_otps(db, phone_number: int):
    db.query(models.OTP).filter(
        models.OTP.phone_number == phone_number,
        models.OTP.valid == True
    ).update({models.OTP.valid: False})

def add_otp_to_db(db: Session, phone_number: int, hashed_code: str, valid: bool, expiration: datetime):
    otp = models.OTP(
        phone_number=phone_number,
        hashed_code=hashed_code,
        valid=valid,
        exception_at=expiration
    )

    db.add(otp)

def create_user(db: Session, user: schemas.SignUpRequirement):
    hash_password = hash_password_md5(user.password)
    db_user = models.User(first_name=user.first_name, phone_number=user.phone_number, hashed_password=hash_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def new_customer_no_commit(db: Session, hardware_customer_id, bakery_id, is_in_queue):
    customer = models.Customer(
        hardware_customer_id=hardware_customer_id,
        bakery_id=bakery_id,
        is_in_queue=is_in_queue,
    )
    db.add(customer)
    db.flush()
    return customer.id


def new_bread_customer(db: Session, customer_id, bread_type_id, count):
    customer_bread = models.CustomerBread(customer_id=customer_id, bread_type_id=bread_type_id, count=count)
    db.add(customer_bread)


def update_customers_status(db: Session, hardware_customer_id: int, bakery_id: int, new_status: bool):
    customer = (
        update(models.Customer)
        .where(models.Customer.hardware_customer_id <= hardware_customer_id)
        .where(models.Customer.bakery_id == bakery_id)
        .values(is_in_queue=new_status)
    )

    result = db.execute(customer)
    return result

def add_bakery(db: Session, bakery: schemas.AddBakery):
    bakery_db = models.Bakery(name=bakery.name, location=bakery.location)
    db.add(bakery_db)
    db.commit()
    db.refresh(bakery_db)
    return bakery_db

def add_bread(db: Session, bread: schemas.AddBread):
    bread_db = models.BreadType(name=bread.name)
    db.add(bread_db)
    db.commit()
    db.refresh(bread_db)
    return bread_db


def add_bakery_bread_entries(db: Session, bakery_id:int, bread_type_and_cook_time: dict):
    new_entries = [
        models.BakeryBread(
            bakery_id=bakery_id,
            bread_type_id=int(bread_type_id),
            cook_time_s=cook_time_s
        )
        for bread_type_id, cook_time_s in bread_type_and_cook_time.items()
    ]

    db.add_all(new_entries)

def add_single_bread_to_bakery(db: Session, bakery_id:int, bread_type_id: int, cook_time_s):
    new_entry = models.BakeryBread(
        bakery_id=bakery_id,
        bread_type_id=int(bread_type_id),
        cook_time_s=cook_time_s
    )
    db.add(new_entry)
    db.commit()

def remove_single_bread_from_bakery(db: Session, bakery_id: int, bread_type_id: int):
    bread_entry = (
        db.query(models.BakeryBread)
        .filter(
            models.BakeryBread.bakery_id == bakery_id,
            models.BakeryBread.bread_type_id == bread_type_id
        ).first())

    if bread_entry:
        db.delete(bread_entry)
        db.commit()
        return True
    else:
        return False


def register_new_admin(db: Session, admin: schemas.NewAdminRequirement):
    new_admin = models.Admin(user_id=admin.user_id, active=admin.status)
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin