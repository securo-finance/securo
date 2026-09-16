"""Carrying a matching policy from one Securo to another.

## Why a file at all

The rules that decide whether a payment settles an invoice are the most
carefully tuned thing on the page, and they are worth more than one
workspace. An accountant who has worked out how their clients' banks
actually behave should be able to hand that to the next workspace, and to
the next machine, without retyping eleven thresholds. Categorization
rules already travel this way; there was no reason matching should not.

## What travels, and what cannot

**Ids do not travel.** A rule limited to two bank accounts names them by
UUID, and that UUID means nothing in another database. So the file
carries names, and importing looks them up again. A name that finds
nothing is *not* silently dropped: a rule that was limited to one client
and arrives limited to nobody is a different rule, and a wider one: the
dangerous direction when the decision is whether money moves. Those rules
are skipped and counted, so the number on screen is the truth.

**Shipped rules travel as a reference, not a copy.** The file says "the
rule we call `same_client_exact`, with these thresholds", not the whole
strategy. Copying it in would freeze it, and a workspace that imported a
policy in March would stop receiving improvements to rules it never had
an opinion about. On import an entry becomes what it already was: the
difference between the file and what we ship *today*.

An id in the file that this version no longer ships is skipped rather
than guessed at.

**Deletions travel too.** "I do not run this rule" is part of a policy,
and a file that quietly restored six rules the author had thrown away
would describe a policy nobody chose.

## Why import replaces rather than merges

Merging two orderings has no correct answer, and order is the whole
mechanism here: the first rule that matches wins, so a rule's meaning
depends on what sits above it. A merge would produce a policy neither
side wrote. So an import replaces the workspace's matching rules, and the
screen asks first.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.payee import Payee
from app.services import reconciliation_policy, reconciliation_rule_service as rules

#: What the file says it is. Checked on import, because a categorization
#: export dropped into the matching importer would otherwise arrive as a
#: file with no rules in it and look like it worked.
FORMAT = "securo-reconciliation-rules"

#: The `when` keys holding ids of things that live in a database, and the
#: kind of thing each holds. Everything else in a rule is a number, a word
#: or a flag, and travels as it is.
BY_NAME = {"accounts": "account", "payees": "payee"}


async def _directory(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Ids to names, and names back to ids, for one workspace."""
    accounts = (
        await session.execute(select(Account).where(Account.workspace_id == workspace_id))
    ).scalars().all()
    payees = (
        await session.execute(select(Payee).where(Payee.workspace_id == workspace_id))
    ).scalars().all()

    to_name = {
        "account": {str(a.id): a.name for a in accounts},
        "payee": {str(p.id): p.name for p in payees},
    }
    # Last one wins on a duplicate name, which is the same thing the
    # categorization import does. Two accounts called "Checking" is a
    # problem a person has to fix; guessing between them here would only
    # hide it.
    to_id = {
        "account": {a.name: str(a.id) for a in accounts},
        "payee": {p.name: str(p.id) for p in payees},
    }
    return to_name, to_id


def _swap(
    when: dict[str, Any], table: dict[str, dict[str, str]]
) -> Optional[dict[str, Any]]:
    """Rewrite the id lists in a `when` block through `table`.

    Returns None when any single entry cannot be translated. Not a
    partial answer: half of a two-account restriction is a rule that
    matches money it was told not to.
    """
    out = dict(when)
    for key, kind in BY_NAME.items():
        block = out.get(key)
        if not isinstance(block, dict):
            continue
        values = block.get("in") or []
        swapped = []
        for value in values:
            found = table[kind].get(str(value))
            if found is None:
                return None
            swapped.append(found)
        out[key] = {**block, "in": swapped}
    return out


async def export_policy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    nodes: Optional[tuple[str, ...]] = None,
) -> dict[str, Any]:
    """The matching policy this workspace runs, as a portable document.

    `nodes` narrows it to the sets this workspace has, so a file carries
    what its author could actually see. Defaults to every editable set,
    which is what a caller with no module opinion means.
    """
    to_name, _ = await _directory(session, workspace_id)
    rows = await rules.overrides_for(session, workspace_id)

    carried_nodes = []
    for node in (nodes if nodes is not None else rules.EDITABLE_NODES):
        policy = rules.compose(node, [r for r in rows if r.node == node])
        carried = []
        for strategy in policy["strategies"]:
            when = _swap(strategy.get("when") or {}, to_name)
            if when is None:
                # An account or client the rule names is gone from this
                # workspace. Exporting it by id would produce a file that
                # cannot be read anywhere, including here.
                continue
            carried.append(
                {
                    "id": strategy["id"],
                    "origin": strategy["origin"],
                    "name": strategy.get("name"),
                    "enabled": strategy.get("enabled", True),
                    "outcome": strategy["outcome"],
                    "trigger": strategy.get("trigger", "money_arrives"),
                    "when": when,
                }
            )
        carried_nodes.append(
            {
                "node": node,
                "rules": carried,
                "discarded": [item["id"] for item in policy.get("discarded", [])],
            }
        )

    return {
        "format": FORMAT,
        "version": 1,
        # Which shape the rules were written against, so a file from an
        # older Securo can be recognised rather than half-read.
        "policy_version": reconciliation_policy.POLICY_VERSION,
        "nodes": carried_nodes,
    }


async def import_policy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    overwrite: bool = False,
    nodes: Optional[tuple[str, ...]] = None,
) -> dict[str, int]:
    """Make this workspace's matching policy match the file.

    Replaces rather than merges; see the module docstring. Returns what
    happened in numbers, because "12 imported" and "12 imported, 3
    skipped" are different outcomes and only one of them needs looking
    into.

    **Worked out in full before anything is removed.** Replacing means
    deleting what is here, and a malformed entry discovered after that
    left the workspace with no rules and a count of what was skipped: the
    policy erased, and nothing on screen saying why. So the whole file is
    resolved first and a structurally broken one is refused with its
    reason, while the rules this workspace has are still standing.

    A skip is not a refusal. A set this workspace does not have, an
    account or a person the file names and this workspace never had, a
    rule this version no longer ships: those are things the file can
    legitimately say that cannot land here, and they are counted.
    """
    if payload.get("format") != FORMAT:
        raise rules.RuleError("bad_format", "That is not a matching-rules file")

    incoming_nodes = payload.get("nodes")
    if not isinstance(incoming_nodes, list):
        raise rules.RuleError("bad_file", "That file has no rules in it")

    existing = await rules.overrides_for(session, workspace_id)
    if existing and not overwrite:
        raise rules.ExistingPolicyError(
            "Importing replaces the matching rules this workspace has now"
        )

    _, to_id = await _directory(session, workspace_id)

    allowed = nodes if nodes is not None else rules.EDITABLE_NODES

    skipped = 0
    planned: list[tuple[str, dict[str, Any], dict[str, Any], bool, int]] = []
    discards: list[tuple[str, str]] = []

    for node_data in incoming_nodes:
        node = node_data.get("node")
        # A file may carry a set this workspace does not have. Skipped and
        # counted rather than written: importing it would store rules the
        # author cannot see and cannot undo.
        if node not in allowed:
            skipped += len(node_data.get("rules") or [])
            continue

        shipped = {
            s["id"]: s
            for s in reconciliation_policy.default_policy(node)["strategies"]
        }

        for place, entry in enumerate(node_data.get("rules") or []):
            when = _swap(entry.get("when") or {}, to_id)
            if when is None:
                # Named an account or a client this workspace does not
                # have. Importing it anyway would widen the rule.
                skipped += 1
                continue

            config = {
                "enabled": entry.get("enabled", True),
                "outcome": entry.get("outcome"),
                "trigger": entry.get("trigger", "money_arrives"),
                "when": when,
            }
            custom = entry.get("origin") == "custom"
            if not custom and entry.get("id") not in shipped:
                # A rule this version does not ship. Guessing at what it
                # meant is worse than saying it was skipped.
                skipped += 1
                continue

            # Raises, and the caller answers with the code and the
            # message. This is the line that has to run before the delete
            # below rather than after it.
            rules.validate_config(config, whole=custom)
            planned.append((node, entry, config, custom, place))

        for strategy_id in node_data.get("discarded") or []:
            if strategy_id in shipped:
                discards.append((node, strategy_id))

    for row in existing:
        if row.node in allowed:
            await session.delete(row)
    await session.flush()

    imported = 0

    for node, entry, config, custom, place in planned:
        try:
            if custom:
                await rules.create_custom(
                    session,
                    workspace_id,
                    user_id,
                    node,
                    entry.get("name") or entry.get("id") or "",
                    config,
                    position=place,
                )
            else:
                await rules.upsert_override(
                    session,
                    workspace_id,
                    user_id,
                    node,
                    entry["id"],
                    config,
                    position=place,
                )
        except rules.RuleError:
            # The structural check above already passed, so anything
            # left is about this workspace rather than the file: a
            # name colliding with one already here, say. Counted, not
            # fatal.
            skipped += 1
            continue
        imported += 1

    for node, strategy_id in discards:
        await rules.delete_rule(session, workspace_id, node, strategy_id)

    await session.flush()
    return {"imported": imported, "skipped": skipped}
