# backend/app/services/rule_service.py
import uuid
from typing import Any, Optional, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.rule import Rule
from app.models.category import Category
from app.models.payee import Payee
from app.models.transaction import Transaction

from app.schemas.rule import (
    RuleCreate,
    RuleExportPayload,
    RuleImportResponse,
    RulePreviewItem,
    RulePreviewResponse,
    RuleUpdate,
)

from app.services.category_service import get_hidden_category_ids

from app.services.rule_engine import (
    apply_rule_actions,
    compile_rule_regex,
    evaluate_conditions,
)

from app.services.category_service import DEFAULT_CATEGORIES_I18N


class DuplicateRuleError(Exception):
    """Raised when a rule with the same name already exists for a user."""
    pass


_ALLOWED_CONDITION_FIELDS = {
    "description", "payee", "notes", "amount", "type", "account_id", "payee_id", "date",
}
_ALLOWED_CONDITION_OPS = {
    "contains", "not_contains", "equals", "not_equals", "starts_with",
    "ends_with", "regex", "gt", "gte", "lt", "lte",
}
_ALLOWED_ACTION_OPS = {
    "set_category", "set_payee", "set_description", "append_notes", "ignore",
}


def _rule_item_value(item, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _flatten_conditions(conditions: list) -> list:
    """Return every leaf condition, unwrapping one level of AND/OR groups.

    A rule's condition list mixes leaves with groups that hold their own leaves
    (see `rule_engine.evaluate_conditions`). Field/operator checks apply to the
    leaves either way, and the schema caps nesting at one level.
    """
    leaves = []
    for node in conditions or []:
        nested = _rule_item_value(node, "conditions")
        if isinstance(nested, list):
            leaves.extend(nested)
        else:
            leaves.append(node)
    return leaves


async def _validate_rule_definition(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    conditions: list,
    actions: list,
) -> None:
    for condition in _flatten_conditions(conditions):
        field = _rule_item_value(condition, "field")
        op = _rule_item_value(condition, "op")
        if field not in _ALLOWED_CONDITION_FIELDS or op not in _ALLOWED_CONDITION_OPS:
            raise ValueError("Invalid rule condition")
        if op == "regex":
            compile_rule_regex(str(_rule_item_value(condition, "value") or ""))

    for action in actions or []:
        op = _rule_item_value(action, "op")
        value = _rule_item_value(action, "value")
        if op not in _ALLOWED_ACTION_OPS:
            raise ValueError("Invalid rule action")
        if op == "set_category":
            try:
                category_id = uuid.UUID(str(value))
            except (TypeError, ValueError):
                raise ValueError("Category not found")
            exists_result = await session.execute(
                select(Category.id).where(
                    Category.id == category_id,
                    Category.workspace_id == workspace_id,
                )
            )
            if exists_result.scalar_one_or_none() is None:
                raise ValueError("Category not found")
        elif op == "set_payee":
            try:
                payee_id = uuid.UUID(str(value))
            except (TypeError, ValueError):
                raise ValueError("Payee not found")
            exists_result = await session.execute(
                select(Payee.id).where(
                    Payee.id == payee_id,
                    Payee.workspace_id == workspace_id,
                )
            )
            if exists_result.scalar_one_or_none() is None:
                raise ValueError("Payee not found")
        elif op == "set_description":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Description cannot be blank")
            if len(value.strip()) > 500:
                raise ValueError("Description cannot exceed 500 characters")


# ─── Universal rules (work for any language/country) ───
# Category values here use internal keys (e.g. "transport") that get resolved to the
# user's actual category name at creation time.
UNIVERSAL_RULES = [
    {"name": "Streaming (Netflix, Spotify, Disney+)", "conditions_op": "or", "conditions": [
        {"field": "description", "op": "starts_with", "value": "NETFLIX"},
        {"field": "description", "op": "starts_with", "value": "SPOTIFY"},
        {"field": "description", "op": "starts_with", "value": "DISNEY"},
    ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

    {"name": "Uber", "conditions_op": "or", "conditions": [
        {"field": "description", "op": "starts_with", "value": "UBER"},
    ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

    {"name": "Amazon", "conditions_op": "or", "conditions": [
        {"field": "description", "op": "starts_with", "value": "AMAZON"},
    ], "actions": [{"op": "set_category", "value": "shopping"}], "priority": 10},

    {"name": "Apple / Google Subscriptions", "conditions_op": "or", "conditions": [
        {"field": "description", "op": "contains", "value": "APPLE.COM/BILL"},
        {"field": "description", "op": "starts_with", "value": "GOOGLE *"},
    ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

    {"name": "Salary / Payroll", "conditions_op": "and", "conditions": [
        {"field": "description", "op": "regex", "value": "SALARY|PAYROLL|DIRECT DEPOSIT"},
    ], "actions": [{"op": "set_category", "value": "salary"}], "priority": 10},

    # Investment movements (aplicação/resgate, CDBs, Tesouro, funds). The target
    # category is flagged `treat_as_transfer=true` so reports exclude these
    # from income/expense — the "other side" of the movement is the Asset
    # (holding) that grew or shrank, not a real gain or cost.
    # Patterns are PT-first since Pluggy (our primary connector) is Brazilian;
    # they're harmless no-ops against English descriptions.
    {"name": "Investimentos (Aplicação / Resgate)", "conditions_op": "or", "conditions": [
        {"field": "description", "op": "regex",
         "value": r"APLICACAO|APLICAÇÃO|RESGATE|DEB FUNDO|CREDITO FUNDO|CRÉDITO FUNDO|COMPRA CDB|VENDA CDB|TESOURO DIRETO|RENDA FIXA|\bCDB\b|\bLCA\b|\bLCI\b|DEBENTURE|FUNDO DE INVESTIMENTO"},
    ], "actions": [{"op": "set_category", "value": "investments"}], "priority": 20},
]

# ─── Country-specific rule packs (optional, not auto-applied) ───
RULE_PACKS: dict[str, dict[str, Any]] = {
    "BR": {
        "name": "Brazil",
        "flag": "\U0001F1E7\U0001F1F7",
        "rules": [
            {"name": "99 (Ride-hailing)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "99"},
                {"field": "description", "op": "starts_with", "value": "99POP"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "iFood / Rappi", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "IFOOD"},
                {"field": "description", "op": "starts_with", "value": "RAPPI"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Mercado Livre", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "MERCADOLIVRE"},
                {"field": "description", "op": "starts_with", "value": "MERCADO LIVRE"},
            ], "actions": [{"op": "set_category", "value": "shopping"}], "priority": 10},

            {"name": "Pix Recebido", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "PIX.*RECEBIDO"},
            ], "actions": [{"op": "set_category", "value": "transfers"}], "priority": 50},

            {"name": "Transferência", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "contains", "value": "TRANSFERENCIA"},
            ], "actions": [{"op": "set_category", "value": "transfers"}], "priority": 90},

            {"name": "Shopee / Magazine Luiza", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "SHOPEE"},
                {"field": "description", "op": "starts_with", "value": "MAGALU"},
                {"field": "description", "op": "starts_with", "value": "MAGAZINE LUIZA"},
            ], "actions": [{"op": "set_category", "value": "shopping"}], "priority": 10},

            {"name": "Drogaria / Farmácia", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "DROGARIA"},
                {"field": "description", "op": "contains", "value": "FARMACIA"},
                {"field": "description", "op": "contains", "value": "DROGA RAIA"},
                {"field": "description", "op": "contains", "value": "DROGASIL"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "Uber Eats", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "UBER EATS"},
                {"field": "description", "op": "starts_with", "value": "UBEREATS"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 5},

            {"name": "Claro / Vivo / Tim", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "CLARO"},
                {"field": "description", "op": "starts_with", "value": "VIVO"},
                {"field": "description", "op": "starts_with", "value": "TIM"},
            ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

            {"name": "Posto / Shell (Combustível)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "POSTO"},
                {"field": "description", "op": "starts_with", "value": "SHELL"},
                {"field": "description", "op": "contains", "value": "COMBUSTIVEL"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Supermercado / Carrefour / Assaí", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "SUPERMERCADO"},
                {"field": "description", "op": "starts_with", "value": "CARREFOUR"},
                {"field": "description", "op": "starts_with", "value": "ASSAI"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "Smart Fit / Academia", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "SMART FIT"},
                {"field": "description", "op": "contains", "value": "ACADEMIA"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "Aluguel", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "ALUGUEL"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},

            {"name": "Salário / Folha", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "SALARIO|FOLHA|PGTO.*SALARIO"},
            ], "actions": [{"op": "set_category", "value": "salary"}], "priority": 10},

            {"name": "Dízimo / Doação", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "DIZIMO"},
                {"field": "description", "op": "contains", "value": "DOACAO"},
                {"field": "description", "op": "contains", "value": "CARIDADE"},
            ], "actions": [{"op": "set_category", "value": "donations"}], "priority": 10},

            {"name": "Pix Enviado", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "PIX.*ENVIADO|PIX.*TRANSF"},
            ], "actions": [{"op": "set_category", "value": "transfers"}], "priority": 50},

            {"name": "Estacionamento / Pedágio", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "ESTACIONAMENTO"},
                {"field": "description", "op": "contains", "value": "PEDAGIO"},
                {"field": "description", "op": "contains", "value": "SEM PARAR"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Barbearia / Salão", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "BARBEARIA"},
                {"field": "description", "op": "contains", "value": "SALAO"},
                {"field": "description", "op": "contains", "value": "CABELEIREIRO"},
            ], "actions": [{"op": "set_category", "value": "personal_care"}], "priority": 10},

            {"name": "IPTU / IPVA / Imposto", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "IPTU"},
                {"field": "description", "op": "contains", "value": "IPVA"},
                {"field": "description", "op": "contains", "value": "IMPOSTO"},
                {"field": "description", "op": "contains", "value": "DARF"},
            ], "actions": [{"op": "set_category", "value": "taxes"}], "priority": 10},

            {"name": "Condomínio", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "CONDOMINIO"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},

            {"name": "Curso / Escola / Faculdade", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "ESCOLA"},
                {"field": "description", "op": "contains", "value": "FACULDADE"},
                {"field": "description", "op": "contains", "value": "UNIVERSIDADE"},
                {"field": "description", "op": "contains", "value": "UDEMY"},
                {"field": "description", "op": "contains", "value": "ALURA"},
            ], "actions": [{"op": "set_category", "value": "education"}], "priority": 10},
        ],
    },
    "US": {
        "name": "United States",
        "flag": "\U0001F1FA\U0001F1F8",
        "rules": [
            {"name": "Lyft", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "LYFT"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "DoorDash / Grubhub", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "DOORDASH"},
                {"field": "description", "op": "starts_with", "value": "GRUBHUB"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Walmart / Target / Costco", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "WALMART"},
                {"field": "description", "op": "starts_with", "value": "TARGET"},
                {"field": "description", "op": "starts_with", "value": "COSTCO"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "Venmo / Zelle / CashApp", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "VENMO"},
                {"field": "description", "op": "contains", "value": "ZELLE"},
                {"field": "description", "op": "contains", "value": "CASH APP"},
            ], "actions": [{"op": "set_category", "value": "transfers"}], "priority": 50},

            {"name": "Starbucks / Dunkin", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "STARBUCKS"},
                {"field": "description", "op": "starts_with", "value": "DUNKIN"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Chevron / Shell / Exxon (Fuel)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "CHEVRON"},
                {"field": "description", "op": "starts_with", "value": "SHELL"},
                {"field": "description", "op": "starts_with", "value": "EXXON"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Whole Foods / Trader Joe's / Kroger", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "WHOLE FOODS"},
                {"field": "description", "op": "starts_with", "value": "TRADER JOE"},
                {"field": "description", "op": "starts_with", "value": "KROGER"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "CVS / Walgreens", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "CVS"},
                {"field": "description", "op": "starts_with", "value": "WALGREENS"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "T-Mobile / AT&T / Verizon", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "T-MOBILE"},
                {"field": "description", "op": "starts_with", "value": "ATT"},
                {"field": "description", "op": "starts_with", "value": "AT&T"},
                {"field": "description", "op": "starts_with", "value": "VERIZON"},
            ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

            {"name": "Comcast / Xfinity", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "COMCAST"},
                {"field": "description", "op": "starts_with", "value": "XFINITY"},
            ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

            {"name": "Home Depot / Lowe's", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "HOME DEPOT"},
                {"field": "description", "op": "starts_with", "value": "LOWES"},
                {"field": "description", "op": "starts_with", "value": "LOWE'S"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},

            {"name": "Planet Fitness / YMCA", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "PLANET FITNESS"},
                {"field": "description", "op": "starts_with", "value": "YMCA"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "Chipotle / McDonald's / Subway", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "CHIPOTLE"},
                {"field": "description", "op": "starts_with", "value": "MCDONALD"},
                {"field": "description", "op": "starts_with", "value": "SUBWAY"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Paycheck / Direct Deposit", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "PAYROLL|DIRECT DEP|ADP|GUSTO"},
            ], "actions": [{"op": "set_category", "value": "salary"}], "priority": 10},

            {"name": "Donations / Charity", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "DONATION"},
                {"field": "description", "op": "contains", "value": "CHARITY"},
                {"field": "description", "op": "contains", "value": "TITHE"},
                {"field": "description", "op": "contains", "value": "RED CROSS"},
            ], "actions": [{"op": "set_category", "value": "donations"}], "priority": 10},

            {"name": "Taxes / IRS", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "IRS"},
                {"field": "description", "op": "contains", "value": "TAX PAYMENT"},
                {"field": "description", "op": "contains", "value": "PROPERTY TAX"},
            ], "actions": [{"op": "set_category", "value": "taxes"}], "priority": 10},

            {"name": "Rent / Mortgage", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "RENT PAYMENT"},
                {"field": "description", "op": "contains", "value": "MORTGAGE"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},
        ],
    },
    "EU": {
        "name": "Europe",
        "flag": "\U0001F1EA\U0001F1FA",
        "rules": [
            {"name": "Bolt / FreeNow", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "BOLT"},
                {"field": "description", "op": "starts_with", "value": "FREENOW"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Deliveroo / Just Eat / Glovo", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "DELIVEROO"},
                {"field": "description", "op": "starts_with", "value": "JUST EAT"},
                {"field": "description", "op": "starts_with", "value": "GLOVO"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Lidl / Aldi / Carrefour", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "LIDL"},
                {"field": "description", "op": "starts_with", "value": "ALDI"},
                {"field": "description", "op": "starts_with", "value": "CARREFOUR"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "SEPA Transfer", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "SEPA"},
                {"field": "description", "op": "contains", "value": "WIRE TRANSFER"},
            ], "actions": [{"op": "set_category", "value": "transfers"}], "priority": 50},

            {"name": "Wolt", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "WOLT"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Flixbus / BlaBlaCar", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "FLIXBUS"},
                {"field": "description", "op": "starts_with", "value": "BLABLACAR"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Rossmann / DM", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "ROSSMANN"},
                {"field": "description", "op": "starts_with", "value": "DM "},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "IKEA", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "IKEA"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},

            {"name": "Deutsche Bahn / SNCF / Renfe", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "DEUTSCHE BAHN"},
                {"field": "description", "op": "starts_with", "value": "DB "},
                {"field": "description", "op": "starts_with", "value": "SNCF"},
                {"field": "description", "op": "starts_with", "value": "RENFE"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Albert Heijn / Rewe / Mercadona / Edeka", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "ALBERT HEIJN"},
                {"field": "description", "op": "starts_with", "value": "REWE"},
                {"field": "description", "op": "starts_with", "value": "MERCADONA"},
                {"field": "description", "op": "starts_with", "value": "EDEKA"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "Miete / Loyer (Rent)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "MIETE"},
                {"field": "description", "op": "contains", "value": "LOYER"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},

            {"name": "Gehalt / Salaire (Salary)", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "GEHALT|SALAIRE|LOHN|SALARY|STIPENDIO"},
            ], "actions": [{"op": "set_category", "value": "salary"}], "priority": 10},

            {"name": "Spende / Don (Donation)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "SPENDE"},
                {"field": "description", "op": "contains", "value": "DONATION"},
                {"field": "description", "op": "contains", "value": "DON "},
            ], "actions": [{"op": "set_category", "value": "donations"}], "priority": 10},

            {"name": "Steuer / Impôt (Tax)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "STEUER"},
                {"field": "description", "op": "contains", "value": "IMPOT"},
                {"field": "description", "op": "contains", "value": "FINANZAMT"},
            ], "actions": [{"op": "set_category", "value": "taxes"}], "priority": 10},
        ],
    },
    "GB": {
        "name": "United Kingdom",
        "flag": "\U0001F1EC\U0001F1E7",
        "rules": [
            {"name": "Tesco / Sainsbury's / Asda", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "TESCO"},
                {"field": "description", "op": "starts_with", "value": "SAINSBURY"},
                {"field": "description", "op": "starts_with", "value": "ASDA"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "Deliveroo / Just Eat", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "DELIVEROO"},
                {"field": "description", "op": "starts_with", "value": "JUST EAT"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "TfL / Trainline", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "TFL"},
                {"field": "description", "op": "starts_with", "value": "TRAINLINE"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Greggs / Costa / Pret", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "GREGGS"},
                {"field": "description", "op": "starts_with", "value": "COSTA"},
                {"field": "description", "op": "starts_with", "value": "PRET"},
            ], "actions": [{"op": "set_category", "value": "food"}], "priority": 10},

            {"name": "Shell / BP / Esso (Fuel)", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "SHELL"},
                {"field": "description", "op": "starts_with", "value": "BP "},
                {"field": "description", "op": "starts_with", "value": "ESSO"},
            ], "actions": [{"op": "set_category", "value": "transport"}], "priority": 10},

            {"name": "Boots / Superdrug", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "BOOTS"},
                {"field": "description", "op": "starts_with", "value": "SUPERDRUG"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "Sky / BT / Virgin Media", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "SKY"},
                {"field": "description", "op": "starts_with", "value": "BT "},
                {"field": "description", "op": "starts_with", "value": "VIRGIN MEDIA"},
            ], "actions": [{"op": "set_category", "value": "subscriptions"}], "priority": 10},

            {"name": "Argos", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "ARGOS"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "M&S", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "M&S"},
                {"field": "description", "op": "starts_with", "value": "MARKS"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "Aldi / Lidl / Morrisons / Waitrose", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "ALDI"},
                {"field": "description", "op": "starts_with", "value": "LIDL"},
                {"field": "description", "op": "starts_with", "value": "MORRISONS"},
                {"field": "description", "op": "starts_with", "value": "WAITROSE"},
            ], "actions": [{"op": "set_category", "value": "groceries"}], "priority": 10},

            {"name": "HMRC / Council Tax", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "COUNCIL TAX"},
                {"field": "description", "op": "contains", "value": "HMRC"},
            ], "actions": [{"op": "set_category", "value": "taxes"}], "priority": 10},

            {"name": "NHS / Bupa", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "starts_with", "value": "NHS"},
                {"field": "description", "op": "starts_with", "value": "BUPA"},
            ], "actions": [{"op": "set_category", "value": "health"}], "priority": 10},

            {"name": "Salary / Wages", "conditions_op": "and", "conditions": [
                {"field": "description", "op": "regex", "value": "SALARY|WAGES|PAYROLL"},
            ], "actions": [{"op": "set_category", "value": "salary"}], "priority": 10},

            {"name": "Charity / Donation", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "CHARITY"},
                {"field": "description", "op": "contains", "value": "DONATION"},
                {"field": "description", "op": "contains", "value": "JUST GIVING"},
            ], "actions": [{"op": "set_category", "value": "donations"}], "priority": 10},

            {"name": "Rent / Mortgage", "conditions_op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "RENT"},
                {"field": "description", "op": "contains", "value": "MORTGAGE"},
                {"field": "description", "op": "contains", "value": "OPENRENT"},
            ], "actions": [{"op": "set_category", "value": "housing"}], "priority": 10},
        ],
    },
}

# Map currency code -> default rule pack country
CURRENCY_TO_PACK = {
    "BRL": "BR",
    "USD": "US",
    "EUR": "EU",
    "GBP": "GB",
}


def _resolve_categories_by_internal_key(
    categories_by_name: dict[str, str],
) -> dict[str, str]:
    """Map default-category internal keys (e.g. 'transport') to the user's actual
    category UUID by matching against any language variant of the default name.

    Rule templates reference categories by their internal key, but the user's
    categories are stored under a localized name. If we resolve the key using
    only the language passed in, an "en"-named category ("Transport") won't
    match a "pt-BR" lookup ("Transporte"), so every rule gets silently
    dropped. Walking all language variants makes pack install work no matter
    which language the user's categories were created in.
    """
    key_to_id: dict[str, str] = {}
    non_name_fields = {"icon", "color", "treat_as_transfer"}
    for internal_key, data in DEFAULT_CATEGORIES_I18N.items():
        for field, value in data.items():
            if field in non_name_fields:
                continue
            cat_id = categories_by_name.get(value)
            if cat_id:
                key_to_id[internal_key] = cat_id
                break
    return key_to_id


def _build_rules_from_templates(
    templates: list[dict], key_to_category_id: dict[str, str]
) -> tuple[list[dict], int]:
    """Convert rule templates (with internal keys) to rules with resolved category UUIDs.

    Returns (resolved_rules, unresolved_count) — unresolved is how many
    templates were dropped because their `set_category` target category
    doesn't exist for this user. Callers use it to distinguish "pack
    already installed" (every rule skipped because the name already
    exists) from "pack can't be installed" (rules skipped because the
    user has no matching category).
    """
    resolved: list[dict] = []
    unresolved = 0
    for rule_data in templates:
        actions = []
        dropped_for_missing_category = False
        for action in rule_data["actions"]:
            if action["op"] == "set_category":
                cat_id = key_to_category_id.get(action["value"])
                if not cat_id:
                    dropped_for_missing_category = True
                    continue
                actions.append({"op": "set_category", "value": cat_id})
            else:
                actions.append(action)
        if not actions:
            if dropped_for_missing_category:
                unresolved += 1
            continue
        resolved.append({**rule_data, "actions": actions})
    return resolved, unresolved


async def _get_existing_rule_names(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """Get the set of existing rule names for a user."""
    result = await session.execute(
        select(Rule.name).where(Rule.user_id == user_id)
    )
    return {row[0] for row in result.all()}


async def create_default_rules(
    session: AsyncSession,
    user_id: uuid.UUID,
    lang: str = "pt-BR",
    workspace_id: Optional[uuid.UUID] = None,
) -> list[Rule]:
    """Create universal default categorization rules for a new user.

    `lang` is accepted for backwards compatibility but no longer affects
    category resolution — categories are matched by internal key across all
    language variants.
    """
    # Scope category resolution to the target workspace so rules in a
    # newly-created workspace point at THAT workspace's categories
    # rather than the user's first workspace.
    cat_query = select(Category)
    if workspace_id is not None:
        cat_query = cat_query.where(Category.workspace_id == workspace_id)
    else:
        cat_query = cat_query.where(Category.user_id == user_id)
    result = await session.execute(cat_query)
    categories = {cat.name: str(cat.id) for cat in result.scalars().all()}
    key_to_id = _resolve_categories_by_internal_key(categories)

    resolved, _unresolved = _build_rules_from_templates(UNIVERSAL_RULES, key_to_id)

    rules = []
    for rule_data in resolved:
        rule = Rule(
            user_id=user_id,
            workspace_id=workspace_id,
            name=rule_data["name"],
            conditions_op=rule_data["conditions_op"],
            conditions=rule_data["conditions"],
            actions=rule_data["actions"],
            priority=rule_data["priority"],
            is_active=True,
        )
        session.add(rule)
        rules.append(rule)

    await session.commit()
    return rules


class RulePackInstallResult:
    """Outcome of installing a country-specific rule pack."""

    __slots__ = ("rules", "unresolved", "categories_created")

    def __init__(
        self,
        rules: list[Rule],
        unresolved: int,
        categories_created: int = 0,
    ) -> None:
        self.rules = rules
        self.unresolved = unresolved
        self.categories_created = categories_created


def _required_internal_keys(pack: dict) -> set[str]:
    """Return the set of category internal keys referenced by a pack's rules."""
    keys: set[str] = set()
    for rule in pack["rules"]:
        for action in rule["actions"]:
            if action["op"] == "set_category":
                keys.add(action["value"])
    return keys


async def _ensure_categories_for_keys(
    session: AsyncSession,
    workspace_id: Optional[uuid.UUID],
    user_id: uuid.UUID,
    internal_keys: set[str],
    lang: str,
) -> int:
    """Create any default categories from `internal_keys` that the user is missing.

    Idempotent — categories are matched across language variants, so a
    user with English defaults won't get Portuguese duplicates if `lang`
    happens to be pt-BR. Returns the count of newly-created categories.
    """
    from app.models.category_group import CategoryGroup
    from app.services.category_group_service import (
        CATEGORY_TO_GROUP,
        DEFAULT_GROUPS_I18N,
    )
    from app.services.category_service import DEFAULT_CATEGORIES_I18N

    non_name_fields = {"icon", "color", "treat_as_transfer", "position"}

    def variants(data: dict) -> set[str]:
        return {v for k, v in data.items() if k not in non_name_fields}

    existing_cats = list(
        (
            await session.execute(
                select(Category).where(
                    Category.workspace_id == workspace_id
                    if workspace_id is not None
                    else Category.user_id == user_id
                )
            )
        ).scalars().all()
    )
    existing_cat_names = {c.name for c in existing_cats}

    missing_keys = [
        key
        for key in internal_keys
        if (data := DEFAULT_CATEGORIES_I18N.get(key))
        and not (variants(data) & existing_cat_names)
    ]
    if not missing_keys:
        return 0

    existing_groups = list(
        (
            await session.execute(
                select(CategoryGroup).where(
                    CategoryGroup.workspace_id == workspace_id
                    if workspace_id is not None
                    else CategoryGroup.user_id == user_id
                )
            )
        ).scalars().all()
    )

    needed_group_keys = {
        gk for gk in (CATEGORY_TO_GROUP.get(k) for k in missing_keys) if gk
    }
    groups_by_key: dict[str, CategoryGroup] = {}
    for gkey in needed_group_keys:
        gdata = DEFAULT_GROUPS_I18N.get(gkey)
        if not gdata:
            continue
        match = next(
            (g for g in existing_groups if g.name in variants(gdata)), None
        )
        if match:
            groups_by_key[gkey] = match
            continue
        group = CategoryGroup(
            user_id=user_id,
            workspace_id=workspace_id,
            name=gdata.get(lang, gdata["en"]),
            icon=gdata["icon"],
            color=gdata["color"],
            position=gdata["position"],
            is_system=True,
        )
        session.add(group)
        groups_by_key[gkey] = group
    await session.flush()

    for key in missing_keys:
        data = DEFAULT_CATEGORIES_I18N[key]
        target_group = groups_by_key.get(CATEGORY_TO_GROUP.get(key))
        cat = Category(
            user_id=user_id,
            workspace_id=workspace_id,
            name=data.get(lang, data["en"]),
            icon=data["icon"],
            color=data["color"],
            is_system=True,
            group_id=target_group.id if target_group else None,
            treat_as_transfer=data.get("treat_as_transfer", False),
        )
        session.add(cat)
    await session.commit()
    return len(missing_keys)


async def install_rule_pack(
    session: AsyncSession,
    workspace_id_or_user_id: uuid.UUID,
    user_id_or_pack_code: uuid.UUID | str,
    pack_code: Optional[str] = None,
    lang: str = "pt-BR",
    create_missing_categories: bool = False,
) -> RulePackInstallResult:
    """Install a country-specific rule pack for a user. Skips rules whose name already exists.

    When `create_missing_categories=True`, creates any default categories
    referenced by the pack that the user is missing — so users in a
    degenerate state (or who never had defaults seeded) can opt into
    "install pack, fill in what's needed". `lang` controls the names of
    any newly-created categories.
    """
    if pack_code is None:
        workspace_id = None
        user_id = workspace_id_or_user_id
        pack_code = str(user_id_or_pack_code)
    else:
        workspace_id = workspace_id_or_user_id
        # Narrowed by the branch: `pack_code` being set means the caller used
        # the workspace-scoped signature, so this argument is the user id.
        user_id = cast(uuid.UUID, user_id_or_pack_code)

    pack = RULE_PACKS.get(pack_code)
    if not pack:
        return RulePackInstallResult([], 0)

    categories_created = 0
    if create_missing_categories:
        categories_created = await _ensure_categories_for_keys(
            session, workspace_id, user_id, _required_internal_keys(pack), lang
        )

    result = await session.execute(
        select(Category).where(
            Category.workspace_id == workspace_id
            if workspace_id is not None
            else Category.user_id == user_id
        )
    )
    categories = {cat.name: str(cat.id) for cat in result.scalars().all()}
    key_to_id = _resolve_categories_by_internal_key(categories)

    resolved, unresolved = _build_rules_from_templates(pack["rules"], key_to_id)

    existing_names = (
        await _get_existing_rule_names_for_workspace(session, workspace_id)
        if workspace_id is not None
        else await _get_existing_rule_names(session, user_id)
    )

    rules: list[Rule] = []
    for rule_data in resolved:
        if rule_data["name"] in existing_names:
            continue
        rule = Rule(
            user_id=user_id,
            workspace_id=workspace_id,
            name=rule_data["name"],
            conditions_op=rule_data["conditions_op"],
            conditions=rule_data["conditions"],
            actions=rule_data["actions"],
            priority=rule_data["priority"],
            is_active=True,
        )
        session.add(rule)
        rules.append(rule)

    await session.commit()
    return RulePackInstallResult(rules, unresolved, categories_created)


async def get_installed_packs(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: Optional[uuid.UUID] = None
) -> dict[str, bool]:
    """Check which rule packs are fully installed for a user."""
    existing_names = (
        await _get_existing_rule_names_for_workspace(session, workspace_id)
        if user_id is not None
        else await _get_existing_rule_names(session, workspace_id)
    )
    result = {}
    for code, pack in RULE_PACKS.items():
        pack_names = {r["name"] for r in pack["rules"]}
        result[code] = pack_names.issubset(existing_names)
    return result


async def get_rules(session: AsyncSession, workspace_id: uuid.UUID) -> list[Rule]:
    result = await session.execute(
        select(Rule)
        .where(Rule.workspace_id == workspace_id)
        .order_by(Rule.priority, Rule.id)
    )
    return list(result.scalars().all())




async def export_rules(session: AsyncSession, workspace_id: uuid.UUID) -> RuleExportPayload:
    """Return a portable JSON export of rules for a workspace.

    Category actions are serialized by category name rather than UUID so the
    file can be imported into another instance/workspace that has equivalent
    category names but different database IDs.
    """
    rules = await get_rules(session, workspace_id)
    category_result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    category_names = {str(cat.id): cat.name for cat in category_result.scalars().all()}

    exported_rules = []
    for rule in rules:
        actions = []
        for action in rule.actions or []:
            if action.get("op") == "set_category":
                category_name = category_names.get(str(action.get("value")))
                if not category_name:
                    continue
                actions.append({**action, "value": category_name})
            else:
                actions.append(action)
        exported_rules.append({
            "name": rule.name,
            "conditions_op": rule.conditions_op,
            "conditions": rule.conditions or [],
            "actions": actions,
            "priority": rule.priority,
            "is_active": rule.is_active,
        })
    return RuleExportPayload(rules=exported_rules)


async def import_rules(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: RuleExportPayload,
    overwrite: bool = False,
) -> RuleImportResponse:
    """Import a portable rules payload into a workspace.

    Existing rules are only replaced when the caller explicitly passes
    `overwrite=True`. Rules whose `set_category` target cannot be matched by
    category name are skipped.
    """
    existing = await get_rules(session, workspace_id)
    if existing and not overwrite:
        raise DuplicateRuleError("Import would overwrite existing rules")

    category_result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    categories_by_name = {cat.name: str(cat.id) for cat in category_result.scalars().all()}

    imported = 0
    skipped = 0
    rules_to_create: list[Rule] = []
    seen_names: set[str] = set()

    for incoming in payload.rules:
        if incoming.name in seen_names:
            skipped += 1
            continue
        seen_names.add(incoming.name)
        resolved_actions = []
        missing_required_reference = False
        for action in incoming.actions:
            action_data = action.model_dump()
            if action_data["op"] == "set_category":
                category_id = categories_by_name.get(str(action_data["value"]))
                if not category_id:
                    missing_required_reference = True
                    break
                action_data["value"] = category_id
            resolved_actions.append(action_data)
        if missing_required_reference:
            skipped += 1
            continue
        try:
            await _validate_rule_definition(
                session,
                workspace_id,
                [condition.model_dump() for condition in incoming.conditions],
                resolved_actions,
            )
        except ValueError:
            skipped += 1
            continue
        rules_to_create.append(Rule(
            user_id=user_id,
            workspace_id=workspace_id,
            name=incoming.name,
            conditions_op=incoming.conditions_op,
            conditions=[condition.model_dump() for condition in incoming.conditions],
            actions=resolved_actions,
            priority=incoming.priority,
            is_active=incoming.is_active,
        ))

    overwritten = 0
    if overwrite and existing and rules_to_create:
        overwritten = len(existing)
        await session.execute(delete(Rule).where(Rule.workspace_id == workspace_id))

    for rule in rules_to_create:
        session.add(rule)
        imported += 1

    await session.commit()
    return RuleImportResponse(imported=imported, skipped=skipped, overwritten=overwritten)


async def get_rule(session: AsyncSession, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> Optional[Rule]:
    result = await session.execute(
        select(Rule).where(Rule.id == rule_id, Rule.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def create_rule(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: RuleCreate,
) -> Rule:
    existing_names = await _get_existing_rule_names_for_workspace(session, workspace_id)
    if data.name in existing_names:
        raise DuplicateRuleError(f"A rule named '{data.name}' already exists")

    await _validate_rule_definition(session, workspace_id, data.conditions, data.actions)

    rule = Rule(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        conditions_op=data.conditions_op,
        conditions=[c.model_dump() for c in data.conditions],
        actions=[a.model_dump() for a in data.actions],
        priority=data.priority,
        is_active=data.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def update_rule(
    session: AsyncSession, rule_id: uuid.UUID, workspace_id: uuid.UUID, data: RuleUpdate
) -> Optional[Rule]:
    rule = await get_rule(session, rule_id, workspace_id)
    if not rule:
        return None

    update_data = data.model_dump(
        exclude_unset=True,
        exclude={"apply_to_existing", "overwrite_existing_categories"},
    )

    if "name" in update_data and update_data["name"] != rule.name:
        existing_names = await _get_existing_rule_names_for_workspace(session, workspace_id)
        if update_data["name"] in existing_names:
            raise DuplicateRuleError(f"A rule named '{update_data['name']}' already exists")

    if data.conditions is not None:
        update_data["conditions"] = [c.model_dump() for c in data.conditions]
    if data.actions is not None:
        update_data["actions"] = [a.model_dump() for a in data.actions]

    await _validate_rule_definition(
        session,
        workspace_id,
        update_data.get("conditions", rule.conditions or []),
        update_data.get("actions", rule.actions or []),
    )

    for key, value in update_data.items():
        setattr(rule, key, value)

    await session.commit()
    await session.refresh(rule)
    return rule


async def delete_rule(session: AsyncSession, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    rule = await get_rule(session, rule_id, workspace_id)
    if not rule:
        return False
    await session.delete(rule)
    await session.commit()
    return True


async def _get_existing_rule_names_for_workspace(
    session: AsyncSession, workspace_id: uuid.UUID
) -> set[str]:
    """Get the set of existing rule names in a workspace."""
    result = await session.execute(
        select(Rule.name).where(Rule.workspace_id == workspace_id)
    )
    return {row[0] for row in result.all()}


# Exactly the columns `_rule_preview` copies. Anything reading a column that is
# not here off a transaction loaded with `load_only(*_PREVIEW_COLUMNS)` would
# trigger a lazy refresh — which raises under async — so the two lists move
# together.
_PREVIEW_COLUMNS = (
    Transaction.user_id,
    Transaction.workspace_id,
    Transaction.account_id,
    Transaction.category_id,
    Transaction.description,
    Transaction.original_description,
    Transaction.description_is_rule_managed,
    Transaction.amount,
    Transaction.currency,
    Transaction.date,
    Transaction.type,
    Transaction.source,
    Transaction.status,
    Transaction.payee,
    Transaction.payee_id,
    Transaction.notes,
    Transaction.is_ignored,
)


def _rule_preview(transaction: Transaction) -> Transaction:
    """Build a detached transaction-shaped object for side-effect-free rule evaluation."""
    return Transaction(
        user_id=transaction.user_id,
        workspace_id=transaction.workspace_id,
        account_id=transaction.account_id,
        category_id=transaction.category_id,
        description=transaction.description,
        original_description=transaction.original_description,
        description_is_rule_managed=bool(transaction.description_is_rule_managed),
        amount=transaction.amount,
        currency=transaction.currency,
        date=transaction.date,
        type=transaction.type,
        source=transaction.source,
        status=transaction.status,
        payee=transaction.payee,
        payee_id=transaction.payee_id,
        notes=transaction.notes,
        is_ignored=transaction.is_ignored,
    )


def _has_manual_description(transaction: Transaction) -> bool:
    """True when the displayed description is the user's own text.

    An imported row whose description drifted from its raw provenance without a
    rule having done it was edited by hand. Rules may still run every other
    matching action on it, but they must not replace that text.
    """
    return (
        not transaction.description_is_rule_managed
        and transaction.original_description is not None
        and transaction.description != transaction.original_description
    )


async def preview_rules_for_transaction(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction: Transaction,
    skip_category_rules: bool = False,
) -> Transaction:
    """Apply active rules to a detached preview and return it without persistence."""
    preview = _rule_preview(transaction)
    await apply_rules_to_transaction(
        session,
        user_id,
        preview,
        skip_category_rules=skip_category_rules,
    )
    return preview


async def apply_rules_to_transaction(
    session: AsyncSession, user_id: uuid.UUID, transaction: Transaction,
    skip_category_rules: bool = False,
) -> None:
    """Apply all active rules to a transaction, modifying it in-place. Commits nothing.

    `user_id` is kept for backwards-compatibility with sync/import callers that
    haven't been migrated to pass workspace_id directly; rules are scoped by
    workspace via the transaction's own workspace_id when available, falling
    back to the legacy user filter so historical rows still match.
    """
    rule_filter = Rule.user_id == user_id
    if getattr(transaction, "workspace_id", None) is not None:
        rule_filter = Rule.workspace_id == transaction.workspace_id
    result = await session.execute(
        select(Rule)
        .where(rule_filter, Rule.is_active == True)
        .order_by(Rule.priority, Rule.id)
    )
    rules = result.scalars().all()

    category_set = transaction.category_id is not None or skip_category_rules
    hidden_categories = (
        await get_hidden_category_ids(session, transaction.workspace_id)
        if getattr(transaction, "workspace_id", None) is not None
        else set()
    )

    for rule in rules:
        conditions = rule.conditions or []
        actions = rule.actions or []
        if evaluate_conditions(rule.conditions_op, conditions, transaction):
            category_set = apply_rule_actions(
                actions,
                transaction,
                category_set,
                hidden_category_ids=hidden_categories,
            )


def _rule_effect_state(tx: Transaction) -> tuple:
    """Everything rule actions can write, for a before/after comparison.

    Mirrors the tuple `apply_single_rule` compares, so a preview counts a
    transaction as changed exactly when saving the rule would change it.
    """
    return (
        tx.category_id,
        tx.payee_id,
        tx.description,
        tx.original_description,
        tx.description_is_rule_managed,
        tx.notes,
        tx.is_ignored,
    )


async def preview_rule(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    conditions_op: str,
    conditions: list,
    actions: list,
    is_active: bool = True,
    apply_to_existing: bool = True,
    overwrite_existing_categories: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> RulePreviewResponse:
    """Report which existing transactions an unsaved rule would match.

    Read-only counterpart of `apply_single_rule`: same transaction scope, same
    engine, same overwrite semantics — so the table the editor shows is exactly
    what saving the rule would produce. Returns the full match/change counts
    plus one window of the matches — `limit` of them from `offset`, newest
    first — so a rule matching thousands of rows can be paged through rather
    than judged by its first screenful. Every match is evaluated whatever the
    window is, which is what keeps the counts exact.

    That means honouring the two gates the save path applies before any rule
    touches history: `apply_single_rule` does nothing for an inactive rule, and
    the create/update endpoints only call it when `apply_to_existing` is set.
    With either off, the matches are still listed — they are what the
    conditions select, which is worth seeing — but nothing is reported as
    changing, because saving would change nothing.
    """
    will_apply = is_active and apply_to_existing
    await _validate_rule_definition(session, workspace_id, conditions, actions or [])

    # Every transaction is evaluated in Python, so only the columns the engine
    # reads and `_rule_preview` copies are worth fetching — notably not
    # `raw_data`, whose provider payloads dwarf the rest of the row and which
    # no condition or action can see.
    result = await session.execute(
        select(Transaction)
        .options(load_only(*_PREVIEW_COLUMNS))
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
        )
        # `id` breaks date ties, so the match list is in the same order for
        # every window: without a total order two rows sharing a date could
        # swap between requests and be shown twice, or skipped, while paging.
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    transactions = result.scalars().all()

    category_rows = await session.execute(
        select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
    )
    category_names = {row.id: row.name for row in category_rows}

    action_dicts = [
        action if isinstance(action, dict) else action.model_dump() for action in actions or []
    ]

    matched = 0
    changed = 0
    sample: list[RulePreviewItem] = []

    for tx in transactions:
        matches = evaluate_conditions(conditions_op, conditions or [], tx)
        # A rule that renamed this row before should still recognise it, so a
        # miss is retried against the original text — same as apply_single_rule.
        if not matches and tx.original_description is not None:
            original_target = _rule_preview(tx)
            original_target.description = tx.original_description
            matches = evaluate_conditions(conditions_op, conditions or [], original_target)
        if not matches:
            continue
        matched += 1
        # An untouched copy when nothing is applied, so `new_category_*` below
        # simply mirrors what the transaction already has.
        draft = _rule_preview(tx)
        will_change = False
        if will_apply:
            before = _rule_effect_state(draft)
            apply_rule_actions(
                action_dicts,
                draft,
                category_already_set=tx.category_id is not None
                and not overwrite_existing_categories,
                skip_description=_has_manual_description(tx),
            )
            will_change = _rule_effect_state(draft) != before
            if will_change:
                changed += 1
        # `matched` has just counted this row, so `matched - 1` is its index
        # in the match list the window is cut from.
        if offset <= matched - 1 < offset + limit:
            sample.append(
                RulePreviewItem(
                    id=tx.id,
                    date=tx.date,
                    description=tx.description,
                    amount=tx.amount,
                    currency=tx.currency,
                    type=tx.type,
                    current_category_id=tx.category_id,
                    current_category_name=category_names.get(tx.category_id),
                    new_category_id=draft.category_id,
                    new_category_name=category_names.get(draft.category_id),
                    will_change=will_change,
                )
            )

    return RulePreviewResponse(
        matched=matched,
        will_change=changed,
        will_apply=will_apply,
        sample=sample,
        offset=offset,
    )


async def apply_single_rule(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rule: Rule,
    overwrite_existing_categories: bool = False,
) -> int:
    """Apply one rule to existing workspace transactions and count modifications.

    Used after rule creation or editing so it can affect history without the
    destructive reset performed by `apply_all_rules`. Existing categories are
    protected unless the caller opts into replacement; other actions still
    apply. Conditions first inspect the current transaction state so rules can
    depend on earlier normalization. If that does not match, the preserved
    imported description is tried as a fallback so edited normalization rules
    can still find transactions they previously changed. A description the user
    typed themselves is left alone, exactly as `apply_all_rules` leaves it.
    """
    if not rule.is_active:
        return 0

    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
        )
    )
    transactions = result.scalars().all()
    conditions = rule.conditions or []
    actions = rule.actions or []

    hidden_categories = await get_hidden_category_ids(session, workspace_id)
    count = 0
    for tx in transactions:
        matches = evaluate_conditions(rule.conditions_op, conditions, tx)
        if not matches and tx.original_description is not None:
            original_target = _rule_preview(tx)
            original_target.description = tx.original_description
            matches = evaluate_conditions(
                rule.conditions_op, conditions, original_target
            )
        if not matches:
            continue

        before = (
            tx.category_id,
            tx.payee_id,
            tx.description,
            tx.original_description,
            tx.description_is_rule_managed,
            tx.notes,
            tx.is_ignored,
        )
        apply_rule_actions(
            actions,
            tx,
            category_already_set=tx.category_id is not None
            and not overwrite_existing_categories,
            skip_description=_has_manual_description(tx),
            hidden_category_ids=hidden_categories,
        )
        after = (
            tx.category_id,
            tx.payee_id,
            tx.description,
            tx.original_description,
            tx.description_is_rule_managed,
            tx.notes,
            tx.is_ignored,
        )
        if before != after:
            count += 1

    await session.commit()
    return count


async def apply_all_rules(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Reset rule-managed fields and reapply active rules in priority order."""
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
        )
    )
    transactions = result.scalars().all()

    rules_result = await session.execute(
        select(Rule)
        .where(Rule.workspace_id == workspace_id, Rule.is_active == True)
        .order_by(Rule.priority, Rule.id)
    )
    rules = rules_result.scalars().all()

    hidden_categories = await get_hidden_category_ids(session, workspace_id)
    count = 0
    for tx in transactions:
        preserve_manual_description = _has_manual_description(tx)
        before = (
            tx.category_id,
            tx.payee_id,
            tx.description,
            tx.original_description,
            tx.description_is_rule_managed,
            tx.notes,
            tx.is_ignored,
        )
        if tx.description_is_rule_managed:
            if tx.original_description is not None:
                tx.description = tx.original_description
            tx.description_is_rule_managed = False
        matched = False
        category_set = False
        for rule in rules:
            conditions = rule.conditions or []
            actions = rule.actions or []
            if evaluate_conditions(rule.conditions_op, conditions, tx):
                if not matched:
                    tx.category_id = None
                    tx.notes = None
                    matched = True
                category_set = apply_rule_actions(
                    actions,
                    tx,
                    category_set,
                    skip_description=preserve_manual_description,
                    hidden_category_ids=hidden_categories,
                )

        after = (
            tx.category_id,
            tx.payee_id,
            tx.description,
            tx.original_description,
            tx.description_is_rule_managed,
            tx.notes,
            tx.is_ignored,
        )
        if matched or before != after:
            count += 1

    await session.commit()
    return count
