from pydantic import BaseModel
from typing import Dict

class SignUpRequirement(BaseModel):
    phone_number: int | None = None
    email: str | None = None
    first_name: str
    last_name: str
    password: str

class SignUpReturn(BaseModel):
    user_id: int

class LogInRequirement(BaseModel):
    phone_number: int
    password: str

class BakeryID(BaseModel):
    bakery_id: int

class NewCustomerRequirement(BakeryID):
    customer_id: int
    bread_requirements: Dict[int, int]

class NextTicketRequirement(BakeryID):
    next_customer_id: int
    current_customer_id: int

class Initialize(BakeryID):
    bread_type_and_cook_time: Dict[int, int]