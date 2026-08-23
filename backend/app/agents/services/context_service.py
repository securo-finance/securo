"""Builds a small "context primer" injected at the start of each
conversation when the agent has `auto_context=True`.

Goals:
  - Tiny budget: ~300 tokens. Cheap on every turn.
  - Stable orientation, not authoritative data — the LLM should still
    use tools for precise queries (balances, transactions, etc.).
  - Locale-aware: respects the user's primary currency and language.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import app_today
from app.models.user import User
from app.services import admin_service


def _fmt_amount(value: Optional[float | Decimal], currency: str) -> str:
    if value is None:
        return ""
    try:
        return f"{currency} {float(value):,.2f}"
    except Exception:  # noqa: BLE001
        return f"{currency} {value}"


def _user_label(user: User) -> str:
    prefs = getattr(user, "preferences", None) or {}
    name = (prefs.get("name") or prefs.get("display_name") or "").strip()
    email = (user.email or "").strip()
    if name and email:
        return f"{name} ({email})"
    if name:
        return name
    if "@" in email:
        return f"{email.split('@', 0)[0]} ({email})" if False else email
    return email or "the user"


async def build_context_primer(
    session: AsyncSession,
    user: User,
    *,
    workspace_id: uuid.UUID | None = None,
    max_accounts: int = 10,
    now: datetime | None = None,
) -> str:
    """Returns a short Markdown block describing who the user is and
    what their accounts look like in the active workspace. Empty string
    when there's nothing useful to say."""
    from app.services import account_service

    prefs = getattr(user, "preferences", None) or {}
    primary_currency = prefs.get("currency_display") or "USD"
    language = prefs.get("language") or "en"
    timezone_label = await admin_service.get_app_timezone(session)

    today_app = app_today(timezone_label, now=now).isoformat()

    lines: list[str] = []
    lines.append("# Context for this conversation")
    lines.append("")
    lines.append("The user you are helping (Securo, a personal finance app):")
    lines.append(f"- Identity: {_user_label(user)}")
    lines.append(f"- Primary currency: {primary_currency}")
    lines.append(f"- Preferred language: {language}")
    lines.append(f"- Timezone: {timezone_label}")
    lines.append(f"- Today is {today_app} ({timezone_label})")
    lines.append("")

    rows: list[dict] = []
    if workspace_id is not None:
        try:
            rows = await account_service.get_accounts(session, workspace_id, include_closed=False)
        except Exception:  # noqa: BLE001
            rows = []

    if rows:
        lines.append(f"Their open accounts (top {min(len(rows), max_accounts)}):")
        for row in rows[:max_accounts]:
            name = row.get("name") or "?"
            kind = row.get("type") or "account"
            currency = row.get("currency") or primary_currency
            balance = row.get("balance")
            balance_str = _fmt_amount(balance, currency) if balance is not None else ""
            account_id = row.get("id")
            piece = f"- {name} ({kind}"
            if balance_str:
                piece += f", {balance_str}"
            piece += ")"
            if account_id:
                piece += f" — id: {account_id}"
            lines.append(piece)
        lines.append("")

    lines.append(
        "Use the available tools (list_transactions, get_dashboard_snapshot, "
        "aggregate, etc.) for precise current data. The summary above is "
        "orientation only — never quote balances from this primer; query the "
        "tools instead."
    )
    lines.append("")
    lines.append("Tool-use rules:")
    lines.append(
        "- `propose_*` tools NEVER execute the action; they return a preview "
        "that renders as a card with an Apply button. After calling one, say "
        "'I prepared a proposal — review and click Apply to confirm', NOT "
        "'I created' or 'Done'."
    )
    lines.append(
        "- When the user names an entity (category, account, payee) you "
        "haven't seen, list it first. Do NOT silently substitute a "
        "different one — ask the user to pick from the existing list, or "
        "use `propose_create_category` to add the missing one."
    )
    return "\n".join(lines).strip()
