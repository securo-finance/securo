import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class SavingsCreate(BaseModel):
    goal : Optional[str] = None 
    amount: Decimal
    target_date :  _Date
    target_amount: Decimal


    
class SavingsRead(BaseModel):
    id: uuid.UUID
    goal : Optional[str] = None    
    amount: Decimal                
    target_date :  _Date           
    target_amount: Decimal         
