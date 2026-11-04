"""Which modules does a workspace show?

One answer, one place. A module is a top-level destination in the app;
this decides which ones a given workspace gets. Adding a workspace kind,
a per-workspace opt-out, or a deployment switch is a new entry in one of
the registries below, never an edit to navigation code.

The chain, in order:

    0. Deployment   — capability flags from the environment. Veto only.
    1. Catalog      — each module declares whether it is on by default.
    2. Kind policy  — the only layer with opinions today.
    3. Workspace    — per-workspace override. Abstains for now.
    4. Supplemental — extension point. Abstains in this codebase.

Layer 0 is listed first because it is the outermost constraint, and
applied last because that is the only ordering in which it is genuinely
a veto: anything applied before layers 1-4 could be handed back by them.

What this is NOT:

  - Not a permission system. It answers "does this workspace show this
    module", never "may this user write". Nothing here belongs next to
    `require_write`; there is no API-level enforcement, by design.
  - Not a feature-flag framework. No per-user toggles, no remote config,
    no rollouts. `ModuleId` is closed on purpose — a stringly-typed
    module id is exactly how this would drift into one.
  - Not navigation metadata. Icons, paths and labels stay in the
    frontend. This answers *which* modules, never *how* to render them.

Deployment flags and this resolver answer different questions and
coexist deliberately: a flag turns a capability off for the whole
install (the operator's call), a kind policy turns it off for one
workspace. `AGENTS_ENABLED` is still read directly by the frontend and
is not part of the catalog — see MODULE_DEPLOYMENT_FLAGS.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

from app.core.feature_flags import feature_flag
from app.models.workspace import Workspace


class ModuleId(str, Enum):
    """Top-level destinations. Closed set — see the module docstring."""

    TRANSACTIONS = "transactions"
    ACCOUNTS = "accounts"
    IMPORT = "import"
    REPORTS = "reports"
    ASSETS = "assets"
    BUDGETS = "budgets"
    GOALS = "goals"
    RECURRING = "recurring"
    CATEGORIES = "categories"
    PAYEES = "payees"
    SPLIT_GROUPS = "split_groups"
    RULES = "rules"
    INVOICES = "invoices"


@dataclass(frozen=True)
class ModuleSpec:
    """Layer 1 — a module's base availability, before any policy runs."""

    module_id: ModuleId
    # True for everything a workspace gets unless a later layer removes
    # it. False for modules a policy has to opt into.
    default_enabled: bool


CATALOG: Mapping[ModuleId, ModuleSpec] = {
    spec.module_id: spec
    for spec in (
        ModuleSpec(ModuleId.TRANSACTIONS, default_enabled=True),
        ModuleSpec(ModuleId.ACCOUNTS, default_enabled=True),
        ModuleSpec(ModuleId.IMPORT, default_enabled=True),
        ModuleSpec(ModuleId.REPORTS, default_enabled=True),
        ModuleSpec(ModuleId.ASSETS, default_enabled=True),
        ModuleSpec(ModuleId.BUDGETS, default_enabled=True),
        ModuleSpec(ModuleId.GOALS, default_enabled=True),
        ModuleSpec(ModuleId.RECURRING, default_enabled=True),
        ModuleSpec(ModuleId.CATEGORIES, default_enabled=True),
        ModuleSpec(ModuleId.PAYEES, default_enabled=True),
        ModuleSpec(ModuleId.SPLIT_GROUPS, default_enabled=True),
        ModuleSpec(ModuleId.RULES, default_enabled=True),
        ModuleSpec(ModuleId.INVOICES, default_enabled=False),
    )
}


def catalog_defaults(catalog: Mapping[ModuleId, ModuleSpec] = CATALOG) -> frozenset[ModuleId]:
    return frozenset(s.module_id for s in catalog.values() if s.default_enabled)


# ---------------------------------------------------------------------------
# Layer 2 — kind policy (the Strategy)
# ---------------------------------------------------------------------------
class ModulePolicy(Protocol):
    """One implementation per workspace kind."""

    def modules(self, base: frozenset[ModuleId]) -> frozenset[ModuleId]:
        """Return the modules this kind shows, given the catalog defaults."""
        ...


class PersonalPolicy:
    """The catalog defaults, unchanged.

    Deliberately empty of opinions: `personal` renders exactly what the
    app rendered before modules existed, and the test asserting that is
    what keeps this true.
    """

    def modules(self, base: frozenset[ModuleId]) -> frozenset[ModuleId]:
        return base


class BusinessPolicy:
    """The catalog defaults plus what a workspace tracking work needs."""

    def modules(self, base: frozenset[ModuleId]) -> frozenset[ModuleId]:
        return base | {ModuleId.INVOICES}


KIND_POLICIES: Mapping[str, ModulePolicy] = {
    "personal": PersonalPolicy(),
    "business": BusinessPolicy(),
}


# ---------------------------------------------------------------------------
# Layers 3 and 4 — providers
# ---------------------------------------------------------------------------
class ModuleProvider(Protocol):
    """A layer that may add, remove, or abstain.

    Return the new set, or None to abstain. Abstaining and returning the
    set unchanged mean the same thing to the resolver; None says so
    louder, and the tests assert on it.
    """

    def apply(
        self, current: frozenset[ModuleId], workspace: Workspace
    ) -> Optional[frozenset[ModuleId]]:
        ...


class WorkspaceOverrideProvider:
    """Layer 3 — per-workspace opt-in/opt-out.

    Abstains: nothing is stored per workspace yet. When it becomes a DB
    read, memoize it on the request's WorkspaceContext — never in a
    global cache that outlives the request, or one workspace's answer
    leaks into another's.
    """

    def apply(
        self, current: frozenset[ModuleId], workspace: Workspace
    ) -> Optional[frozenset[ModuleId]]:
        return None


class SupplementalProvider:
    """Layer 4 — extension point.

    A deployment can supply an implementation that further restricts the
    resolved set. This codebase ships one that always abstains, so the
    seam is exercised by the resolver and the tests without this
    repository holding any opinion about what would go there.
    """

    def apply(
        self, current: frozenset[ModuleId], workspace: Workspace
    ) -> Optional[frozenset[ModuleId]]:
        return None


DEFAULT_PROVIDERS: Sequence[ModuleProvider] = (
    WorkspaceOverrideProvider(),
    SupplementalProvider(),
)


# ---------------------------------------------------------------------------
# Layer 0 — deployment veto
# ---------------------------------------------------------------------------
# ModuleId -> environment flag name. Ships empty: no module in the
# catalog is deployment-gated today. The layer is wired and tested so
# that gating one later is adding an entry here, not building a
# mechanism.
#
# `agents` is deliberately absent. It is not a catalog module: the
# frontend reads AGENTS_ENABLED straight from /api/info at its own call
# sites, and folding it in here would turn this into an agents refactor.
MODULE_DEPLOYMENT_FLAGS: Mapping[ModuleId, str] = {}


class ModuleResolver:
    """Runs the chain. Cheap enough to build per request."""

    def __init__(
        self,
        *,
        catalog: Mapping[ModuleId, ModuleSpec] = CATALOG,
        policies: Mapping[str, ModulePolicy] = KIND_POLICIES,
        providers: Sequence[ModuleProvider] = DEFAULT_PROVIDERS,
        deployment_flags: Mapping[ModuleId, str] = MODULE_DEPLOYMENT_FLAGS,
        flag_reader: Callable[[str], bool] = feature_flag,
    ) -> None:
        self._catalog = catalog
        self._policies = policies
        self._providers = providers
        self._deployment_flags = deployment_flags
        self._flag_reader = flag_reader

    def resolve(self, workspace: Workspace) -> list[str]:
        """The modules this workspace shows, as sorted strings.

        Sorted so the API response is stable and diffable; the frontend
        treats it as a set and never relies on the order.
        """
        resolved = self._resolve_ids(workspace)
        return sorted(m.value for m in resolved)

    def _resolve_ids(self, workspace: Workspace) -> frozenset[ModuleId]:
        current = catalog_defaults(self._catalog)

        # Layer 2. An unknown kind falls back to the catalog defaults
        # rather than raising: a workspace stored before a kind was
        # retired still has to render something sane.
        policy = self._policies.get(workspace.kind)
        if policy is not None:
            current = policy.modules(current)

        # Layers 3 and 4.
        for provider in self._providers:
            proposed = provider.apply(current, workspace)
            if proposed is not None:
                current = proposed

        # Layer 0, applied last so it cannot be undone. Veto only: a
        # flag can remove a module, never introduce one the layers above
        # did not grant.
        vetoed = {
            module_id
            for module_id, flag_name in self._deployment_flags.items()
            if not self._flag_reader(flag_name)
        }
        return frozenset(current - vetoed)


_default_resolver = ModuleResolver()


def resolve_modules(workspace: Workspace) -> list[str]:
    """Module list for a workspace, using the default chain."""
    return _default_resolver.resolve(workspace)
