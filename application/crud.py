from sqlalchemy.orm import Session
from sqlalchemy import update, case, select
from application import models, schemas
from application.auth import hash_password_md5
import pytz
from datetime import datetime, timezone
from sqlalchemy import asc
from datetime import datetime, time
import json

def get_user_by_phone_number(db: Session, phone_number: str):
    return db.query(models.User).filter(models.User.phone_number == phone_number).first()

def is_user_admin(db: Session, user_id: str):
    return db.query(models.Admin).filter_by(user_id=user_id, active=True).first()

def get_active_bakery_breads(db: Session, bakery_id: int):
    return db.query(models.BakeryBread).join(
        models.Bakery
    ).join(
        models.BreadType
    ).filter(
        models.BakeryBread.bakery_id == bakery_id,
        models.Bakery.active == True,
        models.BreadType.active == True,
    ).order_by(asc(
        models.BakeryBread.bread_type_id)).all()


def get_bakery_breads(db: Session, bakery_id: int):
    return db.query(models.BakeryBread).filter(
        models.BakeryBread.bakery_id == bakery_id,
    ).all()

def get_bakery(db: Session, bakery_id: int):
    return db.query(models.Bakery).filter(models.Bakery.bakery_id == bakery_id).first()


def get_first_admin(db: Session):
    return db.query(models.Admin).first()


def get_bakery_bread(db: Session, bakery_id: int, bread_id: int):
    return db.query(models.BakeryBread).filter(
        models.BakeryBread.bakery_id == bakery_id,
        models.BakeryBread.bread_type_id == bread_id
    ).first()

def get_active_breads(db: Session):
    return db.query(models.BreadType).filter(models.BreadType.active == True).all()

def get_all_active_bakeries(db: Session):
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
    return db_user


def new_customer_no_commit(db: Session, ticket_id, bakery_id, is_in_queue, token: str | None = None):
    customer = models.Customer(
        ticket_id=ticket_id,
        bakery_id=bakery_id,
        is_in_queue=is_in_queue,
        token=token,
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

def update_customer_status_to_false(db: Session, ticket_id: int, bakery_id: int):
    stmt = (
        update(models.Customer)
        .where(models.Customer.ticket_id == ticket_id)
        .where(models.Customer.bakery_id == bakery_id)
        .where(models.Customer.is_in_queue == True)
        .values(is_in_queue=False)
        .returning(models.Customer.id)
    )

    result = db.execute(stmt)
    db.commit()
    customer_id = result.scalar_one_or_none()

    return customer_id

def update_all_customers_status_to_false(db: Session, bakery_id: int):
    stmt = (
        update(models.Customer)
        .where(models.Customer.bakery_id == bakery_id)
        .values(is_in_queue=False)
    )

    result = db.execute(stmt)
    return result

def add_bakery(db: Session, bakery: schemas.AddBakery):
    bakery_db = models.Bakery(name=bakery.name, location=bakery.location, active=bakery.active, baking_time_s=bakery.baking_time_s)
    db.add(bakery_db)
    db.commit()
    db.refresh(bakery_db)
    return bakery_db

def modify_bakery(db: Session, bakery_schema: schemas.ModifyBakery):
    bakery = (
        db.query(models.Bakery)
        .filter(models.Bakery.bakery_id == bakery_schema.bakery_id)
        .first()
    )
    if not bakery:
        return None

    update_data = bakery_schema.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "bakery_id":
            continue
        setattr(bakery, field, value)

    db.commit()
    return bakery

def delete_bakery(db: Session, bakery_id: int):
    bakery = db.query(models.Bakery).filter(models.Bakery.bakery_id == bakery_id).first()
    if not bakery:
        return None
    db.delete(bakery)
    db.commit()
    return True


def add_bread(db: Session, bread: schemas.AddBread):
    bread_db = models.BreadType(name=bread.name, active=bread.active)
    db.add(bread_db)
    db.commit()
    return bread_db

def new_cook_avreage_time(db: Session, bakery_id, new_avreage_time):
    new = models.BreadCookTimeLog(bakery_id=bakery_id, new_avreage_cook_time=new_avreage_time)
    db.add(new)
    return new

def delete_bread(db: Session, bread_id: int):
    bread = db.query(models.BreadType).filter(models.BreadType.bread_id == bread_id).first()
    if not bread:
        return None

    db.delete(bread)
    db.commit()
    return True

def change_bread_status(db: Session, bread_schema: schemas.ModifyBread):
    bread = db.query(models.BreadType).filter(models.BreadType.bread_id == bread_schema.bread_id).first()
    if not bread:
        return None
    bread.active = bread_schema.active
    db.commit()
    return bread


def add_bakery_bread_entries(db: Session, bakery_id:int, bread_type_and_cook_time: dict):
    new_entries = [
        models.BakeryBread(
            bakery_id=bakery_id,
            bread_type_id=int(bread_type_id),
            preparation_time=preparation_time
        )
        for bread_type_id, preparation_time in bread_type_and_cook_time.items()
    ]

    db.add_all(new_entries)

def add_single_bread_to_bakery_no_commit(db: Session, bakery_id:int, bread_type_id: int, preparation_time):
    new_entry = models.BakeryBread(
        bakery_id=bakery_id,
        bread_type_id=int(bread_type_id),
        preparation_time=preparation_time
    )
    db.add(new_entry)

def update_bread_bakery_no_commit(db: Session, bakery_id:int, bread_type_id: int, preparation_time):
    stmt = (
        update(models.BakeryBread)
        .where(models.BakeryBread.bread_type_id == bread_type_id)
        .where(models.BakeryBread.bakery_id == bakery_id)
        .values(preparation_time=preparation_time)
    )
    result = db.execute(stmt)
    return result

def get_bread_by_bread_id(db: Session, bread_id:int):
    return db.query(models.BreadType).filter(
        models.BreadType.bread_id == bread_id
    ).first()

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
            models.Customer.ticket_id == customer_ticket_id
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

def register_new_admin(db: Session, user_id: int, active: bool):
    new_admin = models.Admin(user_id=user_id, active=active)
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin

def remove_admin(db: Session, admin_id: int):
    admin = db.query(models.Admin).filter(models.Admin.admin_id == admin_id).first()
    if not admin:
        return None
    db.delete(admin)
    db.commit()
    return True

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

def add_new_ticket_to_wait_list(db: Session, customer_id, is_in_queue=True):
    new_entry = models.WaitList(customer_id=customer_id, is_in_queue=is_in_queue)
    db.add(new_entry)
    db.commit()
    return new_entry

def get_today_wait_list(db: Session, bakery_id: int):
    tehran = pytz.timezone('Asia/Tehran')
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    stmt = (
        select(models.Customer)
        .join(models.Customer.wait_list_associations)  # join via relationship
        .where(models.WaitList.is_in_queue.is_(True),
               models.Customer.register_date >= midnight_utc,
               models.Customer.bakery_id == bakery_id)
    )

    return db.execute(stmt).scalars().all()

def update_wait_list_customer_status(db: Session, ticket_id: int, bakery_id: int, new_status: bool):
    customer_subq = (
        select(models.Customer.id)
        .where(
            models.Customer.ticket_id == ticket_id,
            models.Customer.bakery_id == bakery_id
        )
    )

    stmt = (
        update(models.WaitList)
        .where(models.WaitList.customer_id.in_(customer_subq))
        .values(is_in_queue=new_status)
    )

    db.execute(stmt)

def get_today_last_customer(db: Session, bakery_id: int):
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    # We want the customer with the *highest ticket_id* for today,
    # not simply the last inserted row. This ensures that
    # get_last_ticket_number() always returns the maximum ticket
    # used today, even if the scheduling algorithm reuses lower
    # numbers for queue positions.
    last_customer = (
        db.query(models.Customer)
        .filter(
            models.Customer.bakery_id == bakery_id,
            models.Customer.register_date >= midnight_utc,
        )
        .order_by(models.Customer.ticket_id.desc())  # max ticket_id first
        .first()
    )

    return last_customer


def upsert_queue_state_snapshot(db: Session, bakery_id: int, state_dict: dict):
    """Insert or update today's QueueStateSnapshot for this bakery.

    We keep exactly one row per (bakery_id, local_tehran_date) and overwrite
    state_json on each call so the latest queue_state is available for
    crash recovery and debugging.
    """
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    today = now_tehran.date()

    payload = json.dumps(state_dict, ensure_ascii=False)

    snapshot = (
        db.query(models.QueueStateSnapshot)
        .filter(
            models.QueueStateSnapshot.bakery_id == bakery_id,
            models.QueueStateSnapshot.snapshot_date == today,
        )
        .first()
    )

    if snapshot:
        snapshot.state_json = payload
    else:
        snapshot = models.QueueStateSnapshot(
            bakery_id=bakery_id,
            snapshot_date=today,
            state_json=payload,
        )
        db.add(snapshot)

    db.commit()
    return snapshot


def get_today_queue_state_snapshot(db: Session, bakery_id: int):
    """Return today's latest QueueStateSnapshot for this bakery, if any."""
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    today = now_tehran.date()

    return (
        db.query(models.QueueStateSnapshot)
        .filter(
            models.QueueStateSnapshot.bakery_id == bakery_id,
            models.QueueStateSnapshot.snapshot_date == today,
        )
        .order_by(models.QueueStateSnapshot.id.desc())
        .first()
    )

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


def get_customer_by_ticket_id(db, ticket_id: int, bakery_id: int):
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    return db.query(models.Customer).filter(
        models.Customer.ticket_id == ticket_id,
        models.Customer.bakery_id == bakery_id,
        models.Customer.is_in_queue == True,
        models.Customer.register_date >= midnight_utc
    ).first()


def customer_ticket_exists_today(db: Session, ticket_id: int, bakery_id: int) -> bool:
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    exists = db.query(models.Customer).filter(
        models.Customer.ticket_id == ticket_id,
        models.Customer.bakery_id == bakery_id,
        models.Customer.register_date >= midnight_utc
    ).first()

    return exists is not None


def get_customer_by_ticket_id_any_status(db: Session, ticket_id: int, bakery_id: int):
    """Return today's Customer row for this bakery and ticket_id, regardless of is_in_queue.

    This is useful for admin and user endpoints that need the internal
    customer_id even if the ticket has moved to the wait list or been served.
    """
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    return db.query(models.Customer).filter(
        models.Customer.ticket_id == ticket_id,
        models.Customer.bakery_id == bakery_id,
        models.Customer.register_date >= midnight_utc,
    ).first()


def set_customer_rating(db: Session, customer_id: int, rate: int):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        return None

    customer.rating = rate
    db.commit()
    db.refresh(customer)
    return customer


def get_customer_by_token_today(db: Session, bakery_id: int, token: str):
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    return db.query(models.Customer).filter(
        models.Customer.bakery_id == bakery_id,
        models.Customer.token == token,
        models.Customer.register_date >= midnight_utc,
    ).first()


def create_bread(db, bakery_id:int, customer_internal_id: int, baked_at: datetime, consumed: bool = False):
    bread = models.Bread(
        belongs_to=customer_internal_id,
        baked_at=baked_at,
        bakery_id=bakery_id,
        consumed=consumed
    )
    db.add(bread)
    db.commit()
    db.refresh(bread)
    return bread


def get_today_breads(db, bakery_id: int):
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)


    return db.query(models.Bread).filter(
        models.Bread.bakery_id == bakery_id,
        models.Bread.enter_date >= midnight_utc,
        models.Bread.consumed == False
    ).all()


def consume_breads_for_customer_today(db: Session, bakery_id: int, ticket_id: int) -> int:
    tehran = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran)
    midnight_tehran = tehran.localize(datetime.combine(now_tehran.date(), time.min))
    midnight_utc = midnight_tehran.astimezone(pytz.utc)

    customer = db.query(models.Customer).filter(
        models.Customer.ticket_id == ticket_id,
        models.Customer.bakery_id == bakery_id,
        models.Customer.register_date >= midnight_utc
    ).first()

    if not customer:
        return 0

    stmt = (
        update(models.Bread)
        .where(models.Bread.bakery_id == bakery_id)
        .where(models.Bread.belongs_to == customer.id)
        .where(models.Bread.enter_date >= midnight_utc)
        .where(models.Bread.consumed.is_(False))
        .values(consumed=True)
        .returning(models.Bread.id)
    )

    result = db.execute(stmt).scalars().all()
    db.commit()
    return len(result)
