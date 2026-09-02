"""The module gate: does this workspace have this module at all?

Separate from `require_write`, and deliberately so — they answer
different questions and both have to pass. This file pins the gate's
own behaviour, including the choice of 404 over 403.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.core.module_gate import _assert_enabled, require_module, require_module_write
from app.core.workspace_context import WorkspaceContext
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.module_service import ModuleId


def context(kind: str, role: str = "owner") -> WorkspaceContext:
    """A context built by hand: the gate reads only the workspace's kind
    and never touches the database, which is the point of testing it
    here rather than through a route."""
    user = User(id=uuid.uuid4(), email="gate@example.com", hashed_password="x")
    workspace = Workspace(id=uuid.uuid4(), name="ws", kind=kind)
    member = WorkspaceMember(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role=role
    )
    return WorkspaceContext(workspace=workspace, member=member, user=user)


class TestAssertEnabled:
    def test_business_workspace_passes_for_invoices(self):
        _assert_enabled(context("business"), ModuleId.INVOICES)  # does not raise

    def test_personal_workspace_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            _assert_enabled(context("personal"), ModuleId.INVOICES)
        assert exc.value.status_code == 404

    def test_refusal_says_nothing_about_the_feature(self):
        """404 rather than 403 on purpose: a workspace without the module
        should not be able to learn the feature exists."""
        with pytest.raises(HTTPException) as exc:
            _assert_enabled(context("personal"), ModuleId.INVOICES)
        assert exc.value.detail == "Not found"
        assert "invoice" not in str(exc.value.detail).lower()
        assert "module" not in str(exc.value.detail).lower()

    def test_a_module_every_workspace_has_passes_for_both_kinds(self):
        for kind in ("personal", "business"):
            _assert_enabled(context(kind), ModuleId.TRANSACTIONS)

    def test_an_unknown_kind_falls_back_to_the_catalog(self):
        """A workspace stored before a kind was retired still has to
        render something sane — and must not gain a business module."""
        _assert_enabled(context("legacy_kind"), ModuleId.TRANSACTIONS)
        with pytest.raises(HTTPException):
            _assert_enabled(context("legacy_kind"), ModuleId.INVOICES)


class TestDependencyFactories:
    @pytest.mark.asyncio
    async def test_read_gate_returns_the_context_it_was_given(self):
        dependency = require_module(ModuleId.INVOICES)
        ctx = context("business")
        assert await dependency(ctx) is ctx

    @pytest.mark.asyncio
    async def test_read_gate_refuses_a_personal_workspace(self):
        dependency = require_module(ModuleId.INVOICES)
        with pytest.raises(HTTPException) as exc:
            await dependency(context("personal"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_write_gate_checks_the_module_too(self):
        """The write dependency wraps `current_writable_workspace`, so the
        role is already checked upstream; this asserts it still asks the
        module question rather than trusting the wrap."""
        dependency = require_module_write(ModuleId.INVOICES)
        with pytest.raises(HTTPException) as exc:
            await dependency(context("personal", role="owner"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_write_gate_lets_an_editor_through_on_a_business_workspace(self):
        dependency = require_module_write(ModuleId.INVOICES)
        ctx = context("business", role="editor")
        assert await dependency(ctx) is ctx
