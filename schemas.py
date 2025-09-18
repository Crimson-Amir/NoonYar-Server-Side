from pydantic import BaseModel
from typing import Dict

class SignUpRequirement(BaseModel):
    phone_number: str
    first_name: str
    last_name: str
    password: str

class SignUpReturn(BaseModel):
    user_id: int

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
    bread_type_id_and_cook_time: Dict[str, int]

class AddBakery(BaseModel):
    name: str
    location: str

class AddBakeryResult(BakeryID):
    token: str

class BreadID(BaseModel):
    bread_id: int

class AddBread(BaseModel):
    name: str

class ModifySingleBreadToBakery(BreadID, BakeryID):
    cook_time_s: int

class NewAdminRequirement(BaseModel):
    user_id: int
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