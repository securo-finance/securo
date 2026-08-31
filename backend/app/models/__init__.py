from app.models.user import User
from app.models.passkey import UserPasskey
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceTaxId
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.bank_connection import BankConnection
from app.models.institution import Institution
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
from app.models.payee import Payee, PayeeMapping, PayeeTaxId
from app.models.app_settings import AppSetting
from app.models.goal import Goal
from app.models.credit_card_bill import CreditCardBill
from app.models.group import Group, GroupMember
from app.models.transaction_split import TransactionSplit
from app.models.group_settlement import GroupSettlement
from app.models.collection import Collection, collection_accounts, collection_asset_groups
from app.models.invoice import Invoice, InvoiceAllocation, InvoiceLine, InvoiceSettings
from app.models.invoice_attachment import InvoiceAttachment

# Side-effect import: register the before_insert listener that auto-stamps
# workspace_id from user_id on financial entities. Imported last so all
# referenced models are loaded.
from app.core import workspace_autostamp  # noqa: F401, E402

__all__ = [
    "User",
    "UserPasskey",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceTaxId",
    "Category",
    "CategoryGroup",
    "BankConnection",
    "Institution",
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
    "PayeeTaxId",
    "AppSetting",
    "Goal",
    "CreditCardBill",
    "Group",
    "GroupMember",
    "TransactionSplit",
    "GroupSettlement",
    "Collection",
    "Invoice",
    "InvoiceAllocation",
    "InvoiceLine",
    "InvoiceSettings",
    "InvoiceAttachment",
    "collection_accounts",
    "collection_asset_groups",
]
