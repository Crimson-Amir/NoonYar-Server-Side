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

class NewCustomerRequirement(BaseModel):
    customer_id: int
    bakery_id: int
    bread_requirements: Dict[int, int]