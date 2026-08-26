"""Every mutating route declares an explicit permission decision.

This is the test the codebase was missing, and the reason it was missing is
the reason it is worth having: the write gate is a **dependency wrapper**,
so `require_write` appears in one file and grepping the routes for it
returns nothing. An audit did exactly that and reported that none of the
111 write routes were gated, when 85 of them were. The claim propagated
into three documents before someone re-read the code by hand.

So this walks the dependency graph the way the framework does, and answers
the question mechanically instead of leaving it to memory.

**It asserts the property, not the mechanism.** The rule is "a mutating
route reaches some recognised permission decision", with the recognised set
in `WRITE_GATES` below. Permissions may later become per-action, per-module
or per-view; when a narrower guard exists, it is added to that set and this
test keeps holding. Phrasing it as "must depend on
`current_writable_workspace`" would make the test an obstacle to the very
change it should survive.
"""
import pytest
from fastapi.routing import APIRoute

from app.main import app

MUTATING = {"POST", "PATCH", "PUT", "DELETE"}

#: Dependencies that constitute a workspace write decision. Add to this set
#: when a narrower gate is introduced — never loosen the assertion instead.
WRITE_GATES = {"current_writable_workspace"}

#: Mutating routes that are legitimately not workspace-write-gated, each with
#: the reason. This is not a list of exceptions to tolerate; it is the
#: inventory of everything that will need re-deciding when permissions get
#: finer, which is why the reason is stored next to the path.
ALLOWLIST: dict[tuple[str, str], str] = {
    # Not workspace-scoped: the actor is the user, on their own account.
    ("PATCH", "/me"): "the requester's own user record",
    ("POST", "/2fa/setup"): "the requester's own second factor",
    ("POST", "/2fa/enable"): "the requester's own second factor",
    ("POST", "/2fa/disable"): "the requester's own second factor",
    ("POST", "/2fa/verify"): "unauthenticated step of the requester's own login",
    ("POST", "/passkeys/register/options"): "the requester's own credentials",
    ("POST", "/passkeys/register/verify"): "the requester's own credentials",
    ("DELETE", "/passkeys/{passkey_id}"): "the requester's own credentials",
    ("POST", "/passkeys/authenticate/options"): "unauthenticated: this is how you log in",
    ("POST", "/passkeys/authenticate/verify"): "unauthenticated: this is how you log in",
    ("POST", "/passkeys/2fa/options"): "unauthenticated step of login",
    ("POST", "/passkeys/2fa/verify"): "unauthenticated step of login",
    ("POST", "/login"): "unauthenticated by definition",
    ("POST", "/logout"): "unauthenticated by definition",
    ("POST", "/register"): "unauthenticated by definition",
    ("POST", "/forgot-password"): "unauthenticated by definition",
    ("POST", "/reset-password"): "unauthenticated by definition",
    ("PATCH", "/{id}"): "fastapi-users' own user router, superuser-gated",
    ("DELETE", "/{id}"): "fastapi-users' own user router, superuser-gated",
    ("POST", "/api/setup/create-admin"): "first-run bootstrap, refuses once a user exists",
    # Instance administration: gated by `current_superuser`, not by workspace.
    ("POST", "/api/admin/users"): "superuser-gated instance administration",
    ("PATCH", "/api/admin/users/{user_id}"): "superuser-gated instance administration",
    ("DELETE", "/api/admin/users/{user_id}"): "superuser-gated instance administration",
    ("PATCH", "/api/admin/settings/{key}"): "superuser-gated instance administration",
    # Workspace administration: uses its own, stricter owner floor via
    # `require_membership(min_role="owner")` inside the handler. Converting
    # these to the write gate would *widen* access, since editors can write.
    ("POST", "/api/workspaces"): "creates the workspace there is no membership in yet",
    ("PATCH", "/api/workspaces/{workspace_id}"): "owner floor inside the handler",
    ("POST", "/api/workspaces/{workspace_id}/archive"): "owner floor inside the handler",
    ("POST", "/api/workspaces/{workspace_id}/members"): "owner floor inside the handler",
    ("PATCH", "/api/workspaces/{workspace_id}/members/{member_user_id}"): "owner floor inside the handler",
    ("DELETE", "/api/workspaces/{workspace_id}/members/{member_user_id}"): "owner floor inside the handler",
    # Deliberate: a POST that persists nothing. See the comment on the route.
    ("POST", "/api/transactions/import/preview"): "parses an upload and returns a preview; writes nothing",
    # Same shape for investment orders: the upload has to be a body, and the
    # dry run only reports what an import would do.
    ("POST", "/api/assets/import/preview"): "parses an upload and returns a preview; writes nothing",
    # A read that has to be a POST: the backup password belongs in a body,
    # not in a query string. Same read permission as GET /api/export/backup.
    ("POST", "/api/export/backup"): "exports the workspace it can already read; writes nothing",
    # The agents surface, mounted only when AGENTS_ENABLED is on (the test
    # suite turns it on so these are always covered). An LLM connection is
    # the requester's own credential — scoped by `user.id`, never by
    # workspace — so it belongs to the same family as passkeys rather than
    # to workspace data.
    ("POST", "/api/agents/connections"): "the requester's own LLM credentials",
    ("PATCH", "/api/agents/connections/{conn_id}"): "the requester's own LLM credentials",
    ("DELETE", "/api/agents/connections/{conn_id}"): "the requester's own LLM credentials",
    ("POST", "/api/agents/connections/{conn_id}/test"): "probes the requester's own credential",
    # Global, not workspace data: FX rates are shared by the whole instance.
    # Any authenticated user may refresh them, and nothing per-workspace is
    # touched. Flagged here so a future rate limit or admin floor is a
    # decision rather than an oversight.
    ("POST", "/api/fx-rates/refresh"): "refreshes instance-wide FX rates, no workspace data",
}


def _api_routes(routes):
    """Descend through FastAPI's lazily-included routers.

    Routers are wrapped in `_IncludedRouter` rather than being flattened into
    `app.routes`, so a naive pass over `app.routes` finds four routes and
    concludes there is nothing to check.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)


def _dependency_names(route: APIRoute) -> set[str]:
    seen: set[int] = set()
    stack = [route.dependant]
    names: set[str] = set()
    while stack:
        dependant = stack.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", str(dependant.call)))
        stack.extend(dependant.dependencies)
    return names


def _mutating_routes():
    for route in _api_routes(app.routes):
        for method in sorted(getattr(route, "methods", set()) & MUTATING):
            yield method, route.path, route


ALL_MUTATING = sorted({(m, p) for m, p, _ in _mutating_routes()})


def test_the_walk_actually_finds_the_routes():
    """Guards the guard. If route discovery silently returns nothing — a
    FastAPI internal renamed, say — every assertion below would pass on an
    empty set and this file would be decoration."""
    assert len(ALL_MUTATING) > 100, f"only found {len(ALL_MUTATING)} mutating routes"


@pytest.mark.parametrize(
    "method,path", ALL_MUTATING, ids=[f"{m} {p}" for m, p in ALL_MUTATING]
)
def test_a_mutating_route_declares_a_permission_decision(method, path):
    if (method, path) in ALLOWLIST:
        pytest.skip(f"allowlisted: {ALLOWLIST[(method, path)]}")

    route = next(r for m, p, r in _mutating_routes() if (m, p) == (method, path))
    names = _dependency_names(route)
    assert names & WRITE_GATES, (
        f"{method} {path} mutates but reaches no write gate.\n"
        f"Add `ctx: WorkspaceContext = Depends(current_writable_workspace)`, "
        f"or add it to ALLOWLIST in this file with the reason it is exempt."
    )


def test_the_allowlist_has_no_stale_entries():
    """An allowlisted route that no longer exists — renamed, deleted, or since
    gated — is a stale exemption that would silently cover a future route
    reusing the path."""
    stale = sorted(set(ALLOWLIST) - set(ALL_MUTATING))
    assert stale == [], f"allowlist entries matching no route: {stale}"


def test_nothing_allowlisted_is_already_gated():
    """The opposite drift: a route that gained the gate but kept its
    exemption. Harmless at runtime, misleading to read."""
    redundant = [
        (m, p) for (m, p) in ALLOWLIST
        if any(
            _dependency_names(r) & WRITE_GATES
            for mm, pp, r in _mutating_routes() if (mm, pp) == (m, p)
        )
    ]
    assert redundant == [], (
        f"these are gated and no longer need an allowlist entry: {redundant}"
    )
