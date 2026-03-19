import uuid                                                                                                                   
from datetime import date, datetime                                                                                           
from decimal import Decimal                                                                                                   
from typing import TYPE_CHECKING                                                                                              
                                                                                                                              
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, UniqueConstraint ,String                                        
from sqlalchemy.dialects.postgresql import UUID                                                                               
from sqlalchemy.orm import Mapped, mapped_column, relationship                                                                
from sqlalchemy.sql import func                                                                                               
                                                                                                                              
from app.core.database import Base                                                                                            
                                                                                                                              
if TYPE_CHECKING:                                                                                                             
    from app.models.category import Category                                                                                  
    from app.models.user import User                                                                                          
                                                                                                                              
                                                                                                                              
class Savings(Base):
    __tablename__ = "savings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    goal: Mapped[str] = mapped_column(String(255))
    target_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))

    #user: Mapped["User"] = relationship("User", back_populates="savings")
    #category: Mapped["Category"] = relationship("Category", back_populates="savings")                                                                  
