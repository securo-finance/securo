"""Module resolution: the catalog, the kind policies, and the chain.

The point of these tests is that the invariants are mechanical rather
than intentional. In particular, `personal` is asserted against a
literal list, so any change that would take a module away from an
existing workspace fails here first.
"""
import pytest

from app.models.workspace import WORKSPACE_KINDS, Workspace
from app.services.module_service import (
    CATALOG,
    KIND_POLICIES,
    MODULE_DEPLOYMENT_FLAGS,
    ModuleId,
    ModuleResolver,
    catalog_defaults,
    resolve_modules,
)

# What a personal workspace shows. This is the app exactly as it was
# before modules existed — spelled out rather than derived, so removing
# something from an existing user has to be a deliberate edit here.
PERSONAL_MODULES = [
    "accounts",
    "assets",
    "budgets",
    "categories",
    "goals",
    "import",
    "payees",
    "recurring",
    "reports",
    "rules",
    "split_groups",
    "transactions",
]


def ws(kind: str) -> Workspace:
    return Workspace(name="W", kind=kind)


# ---------------------------------------------------------------------------
# the two kinds
# ---------------------------------------------------------------------------
def test_personal_resolves_to_todays_navigation():
    assert resolve_modules(ws("personal")) == PERSONAL_MODULES


def test_business_is_personal_plus_invoices():
    assert resolve_modules(ws("business")) == sorted(
        PERSONAL_MODULES + ["invoices"]
    )


def test_invoices_is_the_only_difference():
    """Resist giving the policies more differences than the product has."""
    personal = set(resolve_modules(ws("personal")))
    business = set(resolve_modules(ws("business")))
    assert business - personal == {"invoices"}
    assert personal - business == set()


def test_every_workspace_kind_has_a_policy():
    assert set(KIND_POLICIES) == set(WORKSPACE_KINDS)


def test_unknown_kind_falls_back_to_catalog_defaults():
    """A row stored before a kind was retired still renders."""
    assert resolve_modules(ws("something_retired")) == PERSONAL_MODULES


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------
def test_layers_three_and_four_abstain():
    """Neither may quietly start having opinions."""
    from app.services.module_service import (
        SupplementalProvider,
        WorkspaceOverrideProvider,
    )

    workspace = ws("business")
    current = catalog_defaults()
    assert WorkspaceOverrideProvider().apply(current, workspace) is None
    assert SupplementalProvider().apply(current, workspace) is None


def test_deployment_flag_mapping_ships_empty():
    """No catalog module is deployment-gated today."""
    assert MODULE_DEPLOYMENT_FLAGS == {}


def test_deployment_layer_vetoes_when_its_flag_is_off():
    """Proves the seam before anything uses it."""
    resolver = ModuleResolver(
        deployment_flags={ModuleId.ASSETS: "SOME_CAPABILITY_ENABLED"},
        flag_reader=lambda name: False,
    )
    assert "assets" not in resolver.resolve(ws("personal"))
    # Everything else is untouched.
    assert set(resolver.resolve(ws("personal"))) == set(PERSONAL_MODULES) - {"assets"}


def test_deployment_layer_keeps_the_module_when_its_flag_is_on():
    resolver = ModuleResolver(
        deployment_flags={ModuleId.ASSETS: "SOME_CAPABILITY_ENABLED"},
        flag_reader=lambda name: True,
    )
    assert "assets" in resolver.resolve(ws("personal"))


def test_deployment_layer_cannot_grant():
    """A flag being on must not introduce a module the kind policy
    withheld — otherwise an operator could enable a module the policy
    says doesn't belong, and the invariants stop holding."""
    resolver = ModuleResolver(
        deployment_flags={ModuleId.INVOICES: "SOME_CAPABILITY_ENABLED"},
        flag_reader=lambda name: True,
    )
    assert "invoices" not in resolver.resolve(ws("personal"))


# ---------------------------------------------------------------------------
# the abstraction has to buy something
# ---------------------------------------------------------------------------
def test_a_new_kind_needs_only_a_new_policy():
    """No change to the resolver, the catalog, or any navigation code."""

    class ReadOnlyPolicy:
        def modules(self, base):
            return base - {ModuleId.IMPORT, ModuleId.RULES}

    resolver = ModuleResolver(policies={**KIND_POLICIES, "archive": ReadOnlyPolicy()})
    resolved = resolver.resolve(ws("archive"))
    assert "import" not in resolved and "rules" not in resolved
    assert "transactions" in resolved
    # The kinds that already existed are unaffected.
    assert resolver.resolve(ws("personal")) == PERSONAL_MODULES


# ---------------------------------------------------------------------------
# catalog integrity
# ---------------------------------------------------------------------------
def test_catalog_covers_every_module_id():
    assert set(CATALOG) == set(ModuleId)


def test_invoices_is_the_only_module_off_by_default():
    off = {m.value for m in ModuleId} - {m.value for m in catalog_defaults()}
    assert off == {"invoices"}


@pytest.mark.parametrize("module_id", list(ModuleId))
def test_module_ids_are_lower_snake_case(module_id: ModuleId):
    """The frontend mirrors these verbatim."""
    assert module_id.value == module_id.value.lower().strip()
    assert " " not in module_id.value and "-" not in module_id.value
