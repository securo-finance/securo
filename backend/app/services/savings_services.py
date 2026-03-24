
import uuid                                                             
from datetime import date                                               
from decimal import Decimal                                             
from typing import Optional                                             
                                                                        
from sqlalchemy import select, func, and_                               
from sqlalchemy.ext.asyncio import AsyncSession                         
from app.schemas.savings import SavingsCreate,SavingsRead 
from app.models.savings import Savings  
                                 
                                                                        
async def get_goals(session: AsyncSession, 
                    savings_id: uuid.UUID,
                    user_id: uuid.UUID):
    result = await session.execute(
            select(Savings).where(Savings.id == savings_id)
    )
    
    goals = result.scalar_one_or_none()
    
    return goals                       

async def create_savings(
    session: AsyncSession,
        user_id: uuid.UUID,
    data: SavingsCreate
) -> Optional[Savings]:

    savings = Savings(
        user_id=user_id,
        goal=data.goal,
        amount=data.amount,
        target_date=data.target_date,
        target_amount=data.target_amount
    )

    session.add(savings)
    await session.commit()
    await session.refresh(savings)

    return savings


async def delete_savings ( session: AsyncSession,
                        savings_id : uuid.UUID,
                        user_id : uuid.UUID
                          ) -> bool:

    savings = await get_goals(session,savings_id,user_id)
    if not savings:
        return False

    await session.delete(savings)
    await session.commit()
    return True


    
