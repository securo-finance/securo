
import uuid                                                                           
from datetime import date                                                             
from typing import Optional                                                           
                                                                                      
from fastapi import APIRouter, Depends, HTTPException, Query, status                  
from sqlalchemy.ext.asyncio import AsyncSession                                       
                                                                                      
from app.core.auth import current_active_user                                         
from app.core.database import get_async_session                                       
from app.models.savings import  Savings 
from app.models.user import  User
from app.schemas.savings import SavingsRead,SavingsCreate
from app.services import savings_services                                               
                                                                                      
router = APIRouter(prefix="/api/savings", tags=["savings"])


@router.get("/{savings_id}",response_model=SavingsRead)
async def list_goals(
    savings_id : uuid.UUID ,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),

):

    savings = await savings_services.get_goals(session, savings_id, user.id)
    if not savings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="savings not found")
    return savings


@router.post("/", response_model=SavingsRead, status_code=status.HTTP_201_CREATED)
async def create_savings(
    data: SavingsCreate,  
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        return await savings_services.create_savings(
            session,
            user.id,   
            data
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )



@router.delete("/{savings_id}", response_model=SavingsRead)
async def delete_savings(savings_id : uuid.UUID ,
                         session: AsyncSession = Depends(get_async_session),
                         user : User = Depends (current_active_user)
                         ):
    savings = await savings_services.delete_savings(session,savings_id,user.id)
    if not savings:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = "savings not found")
    















