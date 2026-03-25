from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy, get_user_manager, UserManager
from app.core.database import get_async_session
from app.models.account import Account
from app.models.user import User

router = APIRouter(prefix="/api/setup", tags=["setup"])


class SetupStatus(BaseModel):
    has_users: bool


class CreateAdminRequest(BaseModel):
    email: EmailStr
    password: str
    currency: str = "USD"
    name: str = ""
    language: str = "pt-BR"


@router.get("/status", response_model=SetupStatus)
async def get_setup_status(session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(select(func.count(User.id)))
    count = result.scalar() or 0
    return SetupStatus(has_users=count > 0)


@router.post("/create-admin")
async def create_admin(
    body: CreateAdminRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    # Check if users already exist
    result = await session.execute(select(func.count(User.id)))
    count = result.scalar() or 0
    if count > 0:
        raise HTTPException(status_code=403, detail="Setup already completed")

    from fastapi_users import schemas

    user_create = schemas.BaseUserCreate(
        email=body.email,
        password=body.password,
        is_superuser=True,
    )
    user = await user_manager.create(user_create)

    # Build preferences dict
    prefs = {"currency_display": body.currency, "language": body.language, "onboarding_completed": False}
    if body.name:
        prefs["display_name"] = body.name

    # Use direct SQL update to avoid session expiry issues after user_manager.create() commits
    db_session = user_manager.user_db.session
    await db_session.execute(
        sql_update(User).where(User.id == user.id).values(preferences=prefs)
    )

    # Create default wallet with the chosen currency
    wallet_name = "Carteira" if body.language.startswith("pt") else "Wallet"
    wallet = Account(
        user_id=user.id,
        name=wallet_name,
        type="checking",
        balance=Decimal("0.00"),
        currency=body.currency,
    )
    db_session.add(wallet)
    await db_session.commit()

    # Create default categories and rules for the new user
    from app.services.category_service import create_default_categories
    from app.services.rule_service import create_default_rules

    await create_default_categories(db_session, user.id, body.language)
    await create_default_rules(db_session, user.id, body.language)

    # Refresh user to get updated preferences for token generation
    await db_session.refresh(user)

    # Generate access token
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    return {"access_token": token, "token_type": "bearer"}
