from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.bank_connection import BankConnection
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.rule import Rule
from app.models.recurring_transaction import RecurringTransaction
from app.models.budget import Budget
from app.models.import_log import ImportLog
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.models.transaction_attachment import TransactionAttachment
from app.models.payee import Payee, PayeeMapping
from app.models.app_settings import AppSetting
from app.models.goal import Goal
from app.models.credit_card_bill import CreditCardBill
from app.models.group import Group, GroupMember
from app.models.transaction_split import TransactionSplit
from app.models.group_settlement import GroupSettlement
from app.models.collection import Collection, collection_accounts, collection_asset_groups

# Side-effect import: register the before_insert listener that auto-stamps
# workspace_id from user_id on financial entities. Imported last so all
# referenced models are loaded.
from app.core import workspace_autostamp  # noqa: F401, E402

__all__ = [
    "User",
    "Workspace",
    "WorkspaceMember",
    "Category",
    "CategoryGroup",
    "BankConnection",
    "Account",
    "Transaction",
    "Rule",
    "RecurringTransaction",
    "Budget",
    "ImportLog",
    "Asset",
    "AssetGroup",
    "AssetTransaction",
    "AssetValue",
    "FxRate",
    "TransactionAttachment",
    "Payee",
    "PayeeMapping",
    "AppSetting",
    "Goal",
    "CreditCardBill",
    "Group",
    "GroupMember",
    "TransactionSplit",
    "GroupSettlement",
    "Collection",
    "collection_accounts",
    "collection_asset_groups",
]
