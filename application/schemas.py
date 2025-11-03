from pydantic import BaseModel
from typing import Dict

class SignUpRequirement(BaseModel):
    phone_number: str
    first_name: str
    last_name: str
    password: str

class UserID(BaseModel):
    user_id: int

class AdminID(BaseModel):
    admin_id: int

class SignUpReturn(UserID):
    pass

class LogInRequirement(BaseModel):
    phone_number: str
    # password: str

class VerifyOTPRequirement(BaseModel):
    phone_number: str
    code: int

class BakeryID(BaseModel):
    bakery_id: int

class NewCustomerRequirement(BakeryID):
    bread_requirements: Dict[str, int]

class TickeOperationtRequirement(BakeryID):
    customer_ticket_id: int

class Initialize(BakeryID):
    bread_type_id_and_preparation_time: Dict[str, int]

class AddBakery(BaseModel):
    name: str
    location: str
    active: bool = True
    baking_time_s: int = 600

class ModifyBakery(BakeryID):
    name: str | None = None
    location: str | None = None
    active: bool | None = None
    baking_time_s: int | None = None

class AddBakeryResult(BakeryID):
    token: str

class BreadID(BaseModel):
    bread_id: int

class AddBread(BaseModel):
    name: str
    active: bool = True

class ModifyBread(BaseModel):
    bread_id: int
    active: bool

class ModifySingleBreadToBakery(BreadID, BakeryID):
    preparation_time: int

class NewAdminRequirement(UserID):
    status: bool = True

class NewAdminResult(BaseModel):
    admin_id: int

    class Config:
        from_attributes = True

class ChangeBreadName(BaseModel):
    bread_id_and_names: Dict[int, str]

class ModifyBakeryBreadNotify(BakeryID, BreadID):
    pass

class UpcomingNotifyRequest(BakeryID):
    num_tickets: int

class UpdateTimeoutRequest(BakeryID):
    seconds: int
