from sqlalchemy.orm import Session
from sqlalchemy import update, case, select
import models, schemas
from auth import hash_password_md5
import pytz
from sqlalchemy import asc
from datetime import datetime, time
from helpers import database_helper

def get_user_by_phone_number(db: Session, phone_number: str):
    return db.query(models.User).filter(models.User.phone_number == phone_number).first()

def is_user_admin(db: Session, user_id: str):
    return db.query(models.Admin).filter_by(user_id=user_id, active=True).first()

def get_bakery_breads(db: Session, bakery_id: int):
    return db.query(models.BakeryBread).filter(models.BakeryBread.bakery_id == bakery_id).order_by(asc(
        models.BakeryBread.bread_type_id)).all()

def get_bakery(db: Session, bakery_id: int):
    return db.query(models.Bakery).filter(models.Bakery.bakery_id == bakery_id).first()

def get_breads(db: Session):
    return db.query(models.BreadType).all()

def get_all_bakeries(db: Session):
    return db.query(models.Bakery).filter(models.Bakery.active == True).all()

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

def create_user(db: Session, user: schemas.SignUpRequirement):
    hash_password = hash_password_md5(user.password)
    db_user = models.User(
        first_name=user.first_name,
        last_name=user.last_name,
        phone_number=user.phone_number,
        hashed_password=hash_password
    )
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


def new_bread_customers(db: Session, customer_id: int, bread_requirements: dict[str, int]):
    """
    Insert all bread requirements for a customer in one batch.
    """
    objs = [
        models.CustomerBread(
            customer_id=customer_id,
            bread_type_id=int(bread_id),
            count=count,
        )
        for bread_id, count in bread_requirements.items()
    ]
    db.add_all(objs)

def new_customer_to_upcoming_customers(db: Session, customer_id):
    upcoming_customer = models.UpcomingCustomer(
        customer_id=customer_id
    )
    db.add(upcoming_customer)
    return upcoming_customer

def update_customers_status(db: Session, hardware_customer_id: int, bakery_id: int, new_status: bool):
    stmt = (
        update(models.Customer)
        .where(models.Customer.hardware_customer_id <= hardware_customer_id)
        .where(models.Customer.bakery_id == bakery_id)
        .values(is_in_queue=new_status)
        .returning(models.Customer.id)
    )

    result = db.execute(stmt)
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

def update_bread_bakery(db: Session, bakery_id:int, bread_type_id: int, cook_time_s):
    stmt = (
        update(models.BakeryBread)
        .where(models.BakeryBread.bread_type_id == bread_type_id)
        .where(models.BakeryBread.bakery_id == bakery_id)
        .values(cook_time_s=cook_time_s)
    )
    result = db.execute(stmt)
    db.commit()
    return result

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


def remove_upcoming_customer(db: Session, customer_ticket_id: int, bakery_id: int):
    customer_entry = (
        db.query(models.UpcomingCustomer)
        .join(models.Customer)
        .filter(
            models.Customer.bakery_id == bakery_id,
            models.Customer.hardware_customer_id == customer_ticket_id
        )
        .first()
    )

    if customer_entry:
        db.delete(customer_entry)

def add_upcoming_bread_to_bakery(db: Session, bakery_id: int, bread_type_id: int):
    # Ensure the bakery-bread pair exists to avoid integrity errors
    exists = (
        db.query(models.BakeryBread)
        .filter(
            models.BakeryBread.bakery_id == bakery_id,
            models.BakeryBread.bread_type_id == bread_type_id
        ).first()
    )

    if not exists: return

    entry = models.BakeryUpcomingBread(bakery_id=bakery_id, bread_type_id=bread_type_id)
    db.add(entry)
    db.commit()
    return entry

def remove_upcoming_bread_from_bakery(db: Session, bakery_id: int, bread_type_id: int):
    entry = (
        db.query(models.BakeryUpcomingBread)
        .filter(
            models.BakeryUpcomingBread.bakery_id == bakery_id,
            models.BakeryUpcomingBread.bread_type_id == bread_type_id
        ).first()
    )
    if entry:
        db.delete(entry)
        db.commit()
        return True
    return False

def get_bakery_upcoming_breads(db: Session, bakery_id: int):
    return (
        db.query(models.BakeryUpcomingBread)
        .filter(models.BakeryUpcomingBread.bakery_id == bakery_id)
        .all()
    )
def get_bakery_upcoming_customers(db: Session, bakery_id: int):
    return (
        db.query(models.UpcomingCustomer)
        .join(models.Customer)
        .filter(
            models.Customer.bakery_id == bakery_id,
            models.Customer.is_in_queue == True
        ).all()
    )

def register_new_admin(db: Session, admin: schemas.NewAdminRequirement):
    new_admin = models.Admin(user_id=admin.user_id, active=admin.status)
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin

def edit_bread_names(db: Session, bread_type_and_new_name: dict):
    stmt = (
        update(models.BreadType)
        .where(models.BreadType.bread_id.in_(bread_type_and_new_name.keys()))
        .values(
            name=case(
                *[(models.BreadType.bread_id == k, v)
                  for k, v in bread_type_and_new_name.items()]
            )
        )
    )
    db.execute(stmt)
    db.commit()

def add_new_skipped_customer(db: Session, customer_id, is_in_queue=True):
    new_entry = models.SkippedCustomer(customer_id=customer_id, is_in_queue=is_in_queue)
    db.add(new_entry)
    db.commit()
    return new_entry

def get_today_skipped_customers(db: Session, bakery_id: int):
    tehran = pytz.timezone('Asia/Tehran')
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    stmt = (
        select(models.Customer)
        .join(models.Customer.skipped_associations)  # join via relationship
        .where(models.SkippedCustomer.is_in_queue.is_(True),
               models.Customer.register_date >= midnight_utc,
               models.Customer.bakery_id == bakery_id)
    )

    return db.execute(stmt).scalars().all()

def update_skipped_customers_status(db: Session, hardware_customer_id: int, bakery_id: int, new_status: bool):
    customer_subq = (
        select(models.Customer.id)
        .where(
            models.Customer.hardware_customer_id == hardware_customer_id,
            models.Customer.bakery_id == bakery_id
        )
    )

    stmt = (
        update(models.SkippedCustomer)
        .where(models.SkippedCustomer.customer_id.in_(customer_subq))
        .values(is_in_queue=new_status)
    )

    db.execute(stmt)

def get_today_last_customer(db: Session, bakery_id: int):
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    last_customer = (
        db.query(models.Customer)
        .filter(
            models.Customer.bakery_id == bakery_id,
            models.Customer.register_date >= midnight_utc,
        )
        .order_by(models.Customer.id.desc())  # max ID first
        .first()
    )

    return last_customer

def update_timeout_second(db: Session, bakery_id: int, second: int) -> int | None:
    if second:
        second = models.Bakery.timeout_sec + second

    stmt = (
        update(models.Bakery)
        .where(models.Bakery.bakery_id == bakery_id)
        .values(timeout_sec=second)
        .returning(models.Bakery.timeout_sec)
    )
    result = db.execute(stmt).scalar()
    db.commit()
    return result

# def get_otp(db: Session, phone_number: str):
#     otp_entry = db.query(models.OTP).filter(
#         models.OTP.phone_number == phone_number,
#         models.OTP.valid == True,
#         models.OTP.exception_at > datetime.now(UTC)
#     ).order_by(models.OTP.register_date.desc()).first()
#     return otp_entry
#
# def invalidate_old_otps(db, phone_number: int):
#     db.query(models.OTP).filter(
#         models.OTP.phone_number == phone_number,
#         models.OTP.valid == True
#     ).update({models.OTP.valid: False})

# def add_otp_to_db(db: Session, phone_number: int, hashed_code: str, valid: bool, expiration: datetime):
#     otp = models.OTP(
#         phone_number=phone_number,
#         hashed_code=hashed_code,
#         valid=valid,
#         exception_at=expiration
#     )
#
#     db.add(otp)
