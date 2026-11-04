"""Refuse requests for a module this workspace does not have.

Module resolution started as a visibility rule — the nav hid what a
workspace did not need, and there was nothing behind the entries to
enforce. The invoicing ledger is the first module with its own data, so
hiding the link is no longer the whole answer: a personal workspace must
not be able to create an invoice by calling the API directly.

This is deliberately *not* a permission system. It answers "does this
workspace have this module", never "may this user write" — that stays
with `require_write` and the role on the membership. Both have to pass,
and they are asked separately because they are different questions.
"""
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException

from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.services.module_service import ModuleId, resolve_modules


def _assert_enabled(ctx: WorkspaceContext, module: ModuleId) -> None:
    if module.value not in resolve_modules(ctx.workspace):
        # 404, not 403. A workspace without the module should not be
        # able to tell that the feature exists at all, and "forbidden"
        # would say it does.
        raise HTTPException(status_code=404, detail="Not found")


def require_module(module: ModuleId) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Read access to a module's routes."""

    async def dependency(
        ctx: WorkspaceContext = Depends(current_workspace),
    ) -> WorkspaceContext:
        _assert_enabled(ctx, module)
        return ctx

    return dependency


def require_module_write(module: ModuleId) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Write access: the workspace has the module *and* the role can write."""

    async def dependency(
        ctx: WorkspaceContext = Depends(current_writable_workspace),
    ) -> WorkspaceContext:
        _assert_enabled(ctx, module)
        return ctx

    return dependency


def require_any_module(*modules: ModuleId) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Read access to routes that serve more than one module.

    Matching is the case this exists for: its rules cover invoices and
    recurring bills, which are separate modules a workspace may have one
    of, both, or neither. Gating the router on either one alone would
    have hidden a real feature from the workspaces it was written for.
    A route reached this way still has to check the module of whatever it
    is addressed at.
    """

    async def dependency(
        ctx: WorkspaceContext = Depends(current_workspace),
    ) -> WorkspaceContext:
        _assert_any_enabled(ctx, modules)
        return ctx

    return dependency


def require_any_module_write(
    *modules: ModuleId,
) -> Callable[..., Awaitable[WorkspaceContext]]:
    """The same, for writes: any of the modules, and a role that may write."""

    async def dependency(
        ctx: WorkspaceContext = Depends(current_writable_workspace),
    ) -> WorkspaceContext:
        _assert_any_enabled(ctx, modules)
        return ctx

    return dependency


def _assert_any_enabled(ctx: WorkspaceContext, modules: tuple[ModuleId, ...]) -> None:
    enabled = resolve_modules(ctx.workspace)
    if not any(module.value in enabled for module in modules):
        raise HTTPException(status_code=404, detail="Not found")
