import secrets
from sqlalchemy.types import Unicode
from application.database import Base
from sqlalchemy import Integer, String, Column, Boolean, ForeignKey, DateTime, BigInteger
from sqlalchemy import ForeignKeyConstraint
from datetime import datetime
from pytz import UTC
from sqlalchemy.orm import relationship

def generate_token():
    return secrets.token_urlsafe(32)

class User(Base):
    __tablename__ = 'user_detail'

    user_id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    phone_number = Column(String, unique=True, default=None)
    first_name = Column(String)
    last_name = Column(String)
    hashed_password = Column(String)
    active = Column(Boolean, default=True)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))
    customer_associations = relationship("UserCustomer", back_populates="user", cascade="all, delete-orphan")


class Admin(Base):
    __tablename__ = 'admin'
    admin_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user_detail.user_id', ondelete='CASCADE'), unique=True)
    active = Column(Boolean, default=True)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))


class Bakery(Base):
    __tablename__ = 'bakery'

    bakery_id = Column(Integer, primary_key=True)
    name = Column(Unicode(255))
    location = Column(Unicode(255))
    token = Column(String, nullable=False, default=generate_token)
    active = Column(Boolean, default=True)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))
    baking_time_s = Column(Integer, nullable=False, default=600)
    timeout_sec = Column(Integer, nullable=False, default=0)
    bread_associations = relationship("BakeryBread", back_populates="bakery", cascade="all, delete-orphan")
    bread_cook_time_log_associations = relationship("BreadCookTimeLog", back_populates="bakery", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="bakery")


class BreadType(Base):
    __tablename__ = 'bread_type'

    bread_id = Column(Integer, primary_key=True)
    name = Column(Unicode(255), unique=True, nullable=False)
    active = Column(Boolean, default=True)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))

    bakery_associations = relationship("BakeryBread", back_populates="bread", cascade="all, delete-orphan")
    customer_associations = relationship("CustomerBread", back_populates="bread", cascade="all, delete-orphan")


class BakeryBread(Base):
    __tablename__ = 'bakery_bread'

    bakery_id = Column(Integer, ForeignKey('bakery.bakery_id', ondelete='CASCADE'), primary_key=True)
    bread_type_id = Column(Integer, ForeignKey('bread_type.bread_id', ondelete='CASCADE'), primary_key=True)
    preparation_time = Column(Integer, nullable=False)

    bakery = relationship("Bakery", back_populates="bread_associations")
    bread = relationship("BreadType", back_populates="bakery_associations")


class BakeryUpcomingBread(Base):
    __tablename__ = 'bakery_upcoming_bread'

    bakery_id = Column(Integer, ForeignKey('bakery.bakery_id', ondelete='CASCADE'), primary_key=True)
    bread_type_id = Column(Integer, ForeignKey('bread_type.bread_id', ondelete='CASCADE'), primary_key=True)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))
    __table_args__ = (
        ForeignKeyConstraint(
            ['bakery_id', 'bread_type_id'],
            ['bakery_bread.bakery_id', 'bakery_bread.bread_type_id'],
            ondelete='CASCADE'
        ),
    )


class Customer(Base):
    __tablename__ = 'customer'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, nullable=False)
    bakery_id = Column(Integer, ForeignKey('bakery.bakery_id', ondelete='CASCADE'))
    is_in_queue = Column(Boolean, nullable=False)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))

    bread_associations = relationship("CustomerBread", back_populates="customer", cascade="all, delete-orphan")
    user_associations = relationship("UserCustomer", back_populates="customer", cascade="all, delete-orphan")
    wait_list_associations = relationship("Wait_list", back_populates="customer", cascade="all, delete-orphan")
    upcoming_associations = relationship("UpcomingCustomer", back_populates="customer", cascade="all, delete-orphan")
    bakery = relationship("Bakery", back_populates="customers")


class CustomerBread(Base):
    __tablename__ = 'customer_bread'

    customer_id = Column(Integer, ForeignKey('customer.id', ondelete='CASCADE'), primary_key=True)
    bread_type_id = Column(Integer, ForeignKey('bread_type.bread_id', ondelete='CASCADE'), primary_key=True)
    count = Column(Integer, nullable=False, default=1)

    customer = relationship("Customer", back_populates="bread_associations")
    bread = relationship("BreadType", back_populates="customer_associations")


class UserCustomer(Base):
    __tablename__ = 'user_customer'

    user_id = Column(Integer, ForeignKey('user_detail.user_id', ondelete='CASCADE'), primary_key=True)
    customer_id = Column(Integer, ForeignKey('customer.id', ondelete='CASCADE'), primary_key=True)

    user = relationship("User", back_populates="customer_associations")
    customer = relationship("Customer", back_populates="user_associations")

class WaitList(Base):
    __tablename__ = 'wait_list'

    customer_id = Column(Integer, ForeignKey('customer.id', ondelete='CASCADE'), primary_key=True)
    is_in_queue = Column(Boolean, nullable=False)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))

    customer = relationship("Customer", back_populates="wait_list_associations")

class UpcomingCustomer(Base):
    __tablename__ = 'upcoming_customer'

    customer_id = Column(Integer, ForeignKey('customer.id', ondelete='CASCADE'), primary_key=True)
    customer = relationship("Customer", back_populates="upcoming_associations")


class BreadCookTimeLog(Base):
    __tablename__ = 'bread_cook_time_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    bakery_id = Column(Integer, ForeignKey('bakery.bakery_id', ondelete='CASCADE'))
    new_avreage_cook_time = Column(Integer, nullable=False)
    register_date = Column(DateTime, default=lambda: datetime.now(UTC))

    bakery = relationship("Bakery", back_populates="bread_cook_time_log_associations")


# class OTP(Base):
#     __tablename__ = 'otp_table'
#
#     otp_id = Column(Integer, primary_key=True, autoincrement=True)
#     phone_number = Column(BigInteger, nullable=False)
#     hashed_code = Column(String, nullable=False)
#     valid = Column(Boolean, default=False)
#     register_date = Column(DateTime, default=lambda: datetime.now(UTC))
#     exception_at = Column(DateTime, nullable=False)
