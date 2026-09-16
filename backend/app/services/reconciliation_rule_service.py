"""Turning shipped rules plus a workspace's changes into one policy.

The rules a workspace actually runs are **not** stored anywhere. They are
computed, on every read, from two things: the document that shipped with
the image, and the rows recording what somebody chose to change. That is
the whole idea, and it costs one small query per match.

The alternative, writing the defaults into each workspace at creation,
looks simpler for about six months, until the day a better default ships
and reaches nobody who had already opened the page. Under this model an
untouched rule keeps improving with the product, and a row exists exactly
where a person decided otherwise.

## What a person may change, and what they may not

Everything the engine reads: whether a rule runs, whether it links or
merely suggests, how close the amount and the date have to be, how
similar the description, whether the payer must be known. They may also
write rules of their own, and put them before ours.

They may not change the *shape*. A rule whose amount rule names a match
mode the engine has never heard of would not fail loudly, it would
quietly stop matching, and nobody would find out until a month of
payments had gone unreconciled. So writes are validated against the
schema before they are stored, and a stored rule that no longer parses is
skipped with its reason recorded rather than crashing the sync.
"""
from __future__ import annotations

import copy
import re
import uuid
from decimal import Decimal
from typing import Any, Collection, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationRule
from app.services import reconciliation_policy
from app.services.module_service import ModuleId

#: The nodes a person is shown and may edit, and the module each needs.
#:
#: `match_placeholder` is absent, and only it. That one decides whether an
#: arriving charge is the row *we* generated from a schedule: bookkeeping
#: about our own duplicates, whose only visible effect is a line appearing
#: twice. Nobody should be offered that lever.
#:
#: `match_recurring` was removed alongside it for a while, under the same
#: description, and the description was wrong. It decides whether the
#: charge that arrived is the bill you told us to expect, which is a
#: judgement about your money and the personal-workspace counterpart of
#: matching an invoice. Its thresholds are exactly what somebody wants to
#: change: rent that posts anywhere between the 1st and the 8th, a gym
#: that bills a different amount every month.
#:
#: The confusion that removed it was real but came from the labels. "An
#: invoice can itself be recurring" made "invoices" and "recurring" read
#: as two kinds of invoice, and then no answer existed for where a monthly
#: retainer belonged. The sets are not two kinds of thing: they are what
#: the money is matched **against**. A document you issued and are owed
#: for, or a bill you asked us to expect. A monthly retainer's invoice
#: goes against the invoice; a subscription charge against the bill.
#:
#: Each carries the module it needs, because they are not the same
#: feature: a workspace with recurring bills and no invoicing sees one
#: set, one with both sees both, and one with neither never reaches the
#: page.
EDITABLE_NODES_BY_MODULE: dict[str, ModuleId] = {
    reconciliation_policy.MATCH_INVOICE["node"]: ModuleId.INVOICES,
    reconciliation_policy.MATCH_RECURRING["node"]: ModuleId.RECURRING,
}

#: Every node a rule may be written against, in the order they are shown.
EDITABLE_NODES = tuple(EDITABLE_NODES_BY_MODULE)


def nodes_for(enabled_modules: Collection[str]) -> tuple[str, ...]:
    """The sets this workspace may see, in shipped order."""
    return tuple(
        node
        for node, module in EDITABLE_NODES_BY_MODULE.items()
        if module.value in enabled_modules
    )


#: A custom rule's id: what the workspace typed, reduced to something
#: safe to store, compare and show. Ids are compared against shipped ones,
#: so they live in the same namespace and follow the same shape.
_ID_SHAPE = re.compile(r"[^a-z0-9_]+")

#: Reserved prefix so a workspace can never write a rule that shadows one
#: of ours by claiming its id.
CUSTOM_PREFIX = "custom_"


class ExistingPolicyError(Exception):
    """Importing would replace matching rules this workspace already has.

    Its own type rather than a `RuleError`, because it is not a bad
    request: the file is fine, and the answer is a question for the
    person rather than a correction to the file.
    """


class RuleError(Exception):
    """A rule that cannot be stored, with a code the UI can translate."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def slug(name: str) -> str:
    base = _ID_SHAPE.sub("_", name.strip().lower()).strip("_")[:48]
    return f"{CUSTOM_PREFIX}{base or uuid.uuid4().hex[:8]}"


def _merge(shipped: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a sparse patch over a shipped rule.

    Recursive, so a workspace that only widened the date window keeps
    every other signal live: including improvements shipped later. A
    patch that replaced the whole `when` block would silently freeze the
    signals it did not mention, which is the failure this exists to
    avoid.
    """
    result = copy.deepcopy(shipped)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _prune(patch: dict[str, Any], shipped: dict[str, Any]) -> dict[str, Any]:
    """Drop everything the patch says that we already say ourselves.

    The screen sends a whole rule, because a form holds a whole rule:
    every field, whether or not somebody touched it. Storing that as the
    override would quietly freeze the parts nobody meant to change, and
    the workspace would stop receiving improvements to signals it never
    had an opinion about. That is the exact failure this design exists to
    avoid, so the difference is taken **here**, where it cannot be
    forgotten by a future caller.

    Recursive, and it drops sub-objects that end up empty: a `when` block
    identical to ours leaves nothing behind rather than an empty husk that
    would still read as "customised" on the page.
    """
    out: dict[str, Any] = {}
    for key, value in patch.items():
        theirs = shipped.get(key)
        if isinstance(value, dict) and isinstance(theirs, dict):
            nested = _prune(value, theirs)
            if nested:
                out[key] = nested
        elif key not in shipped or theirs != value:
            out[key] = value
    return out


async def overrides_for(
    session: AsyncSession, workspace_id: uuid.UUID, node: Optional[str] = None
) -> list[ReconciliationRule]:
    query = select(ReconciliationRule).where(
        ReconciliationRule.workspace_id == workspace_id
    )
    if node is not None:
        query = query.where(ReconciliationRule.node == node)
    result = await session.execute(query)
    return list(result.scalars().all())


def compose(node: str, rows: list[ReconciliationRule]) -> dict[str, Any]:
    """The policy this workspace actually runs, for one node.

    Shipped rules in shipped order, each with its patch applied; the
    workspace's own rules interleaved by position. A rule with no position
    stays where the shipped document put it, so adding one rule does not
    require renumbering the rest.
    """
    policy = reconciliation_policy.default_policy(node)
    patches = {row.strategy_id: row for row in rows if row.origin == "default"}
    # Thrown away by this workspace. Still shipped, so we still know its
    # name, which is the only reason the page can offer it back.
    discarded: list[dict[str, Any]] = []
    # Sort key: where it sits, then whether somebody *asked* for that spot,
    # then the shipped order. The middle term is what makes "put this
    # first" mean first: an explicit position beats a rule that merely
    # happens to occupy that index, while a rule nobody positioned never
    # displaces one that was.
    ordered: list[tuple[float, int, int, dict[str, Any]]] = []

    for index, strategy in enumerate(policy["strategies"]):
        row = patches.get(strategy["id"])
        if row is not None and row.deleted:
            discarded.append({"id": strategy["id"], "node": node})
            continue
        merged = _merge(strategy, row.config) if row else strategy
        merged["origin"] = "default"
        # "Changed" means the *rule* differs, not that a row exists. A row
        # carrying only a position: written when somebody reordered the
        # list: is not a departure from what we ship, and marking it as
        # one would offer to "restore" a rule that was never altered.
        merged["customised"] = bool(row and row.config)
        asked = row is not None and row.position is not None
        place = float(row.position) if asked and row else float(index)
        ordered.append((place, 0 if asked else 1, index, merged))

    for offset, row in enumerate(r for r in rows if r.origin == "custom"):
        strategy = copy.deepcopy(row.config)
        strategy["id"] = row.strategy_id
        strategy["origin"] = "custom"
        strategy["customised"] = True
        strategy["name"] = row.name
        asked = row.position is not None
        place = (
            float(row.position)
            if asked
            else float(len(policy["strategies"]) + offset)
        )
        ordered.append(
            (place, 0 if asked else 1, len(policy["strategies"]) + offset, strategy)
        )

    ordered.sort(key=lambda item: (item[0], item[1], item[2]))
    policy["strategies"] = [strategy for _, _, _, strategy in ordered]
    policy["discarded"] = discarded
    return policy


async def resolve(
    session: AsyncSession, workspace_id: uuid.UUID, node: str
) -> dict[str, Any]:
    """The live policy for one node in one workspace."""
    return compose(node, await overrides_for(session, workspace_id, node))


def narrow_for_frequency(policy: dict[str, Any], frequency: str) -> dict[str, Any]:
    """Clamp a recurring policy to what this frequency can safely allow.

    Applied *after* the workspace's changes, and it only ever narrows.
    Somebody who widened the monthly window to ten days has said something
    reasonable about how late their bills post; letting that same ten days
    apply to a weekly bill would let a charge match the neighbouring
    occurrence, which is not a preference they expressed: it is a fact
    about weekly bills sitting seven days apart. So the workspace's number
    wins wherever it is tighter, and loses only where physics disagrees.

    A copy, because the caller holds one composed policy and narrows it
    differently for each bill in the batch.
    """
    window = reconciliation_policy.RECURRING_WINDOW_BY_FREQUENCY.get(frequency)
    if not window:
        return policy
    narrowed = copy.deepcopy(policy)
    for strategy in narrowed["strategies"]:
        date_rule = strategy.get("when", {}).get("date")
        if not isinstance(date_rule, dict):
            continue
        date_rule["before_days"] = min(
            int(date_rule.get("before_days", 0)), window["before_days"]
        )
        date_rule["after_days"] = min(
            int(date_rule.get("after_days", 0)), window["after_days"]
        )
    return narrowed


async def resolve_recurring(
    session: AsyncSession, workspace_id: uuid.UUID, frequency: str
) -> dict[str, Any]:
    """The recurring policy for one bill, workspace changes and all."""
    policy = await resolve(
        session, workspace_id, reconciliation_policy.MATCH_RECURRING["node"]
    )
    return narrow_for_frequency(policy, frequency)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
_OUTCOMES = ("link", "suggest")
_AMOUNT_MATCHES = ("exact", "tolerance", "ratio", "partial", "set")
_COUNTERPARTIES = ("any", "same_payee")


def validate_config(config: dict[str, Any], *, whole: bool) -> dict[str, Any]:
    """Check a rule before it is stored.

    `whole` distinguishes a workspace's own rule, which must stand on its
    own, from a patch over one of ours, where every key is optional and
    absence means "leave it as shipped".

    Validating on the way in rather than on the way out is deliberate: a
    malformed rule discovered during a sync would stop matching silently,
    and the person who broke it would be nowhere near a screen.
    """
    clean: dict[str, Any] = {}

    if "enabled" in config:
        clean["enabled"] = bool(config["enabled"])

    if "trigger" in config:
        if config["trigger"] not in ("money_arrives", "invoice_issued", "both"):
            raise RuleError("bad_trigger", "Unknown moment for a rule to run")
        clean["trigger"] = config["trigger"]

    if "outcome" in config:
        if config["outcome"] not in _OUTCOMES:
            raise RuleError("bad_outcome", "A rule either links or suggests")
        clean["outcome"] = config["outcome"]
    elif whole:
        raise RuleError("outcome_required", "Say whether this rule links or suggests")

    when = config.get("when")
    if when is None:
        if whole:
            raise RuleError("conditions_required", "A rule needs at least one condition")
        return clean
    if not isinstance(when, dict):
        raise RuleError("bad_conditions", "Conditions must be a set of named signals")

    checked: dict[str, Any] = {}

    if "counterparty" in when:
        if when["counterparty"] not in _COUNTERPARTIES:
            raise RuleError("bad_counterparty", "Unknown counterparty condition")
        checked["counterparty"] = when["counterparty"]

    if "amount" in when:
        checked["amount"] = _validate_amount(when["amount"])

    if "date" in when:
        checked["date"] = _validate_window(when["date"])

    if "description_similarity" in when:
        checked["description_similarity"] = _validate_similarity(
            when["description_similarity"]
        )

    if "currency" in when:
        checked["currency"] = _validate_currency(when["currency"])

    if "accounts" in when:
        checked["accounts"] = {"in": _validate_ids(when["accounts"], "bad_accounts")}

    if "payees" in when:
        checked["payees"] = {"in": _validate_ids(when["payees"], "bad_payees")}

    if "direction" in when:
        if when["direction"] not in ("any", "credit", "debit"):
            raise RuleError("bad_direction", "Money either comes in or goes out")
        checked["direction"] = when["direction"]

    if "text" in when:
        checked["text"] = _validate_text(when["text"])

    for flag in ("same_account", "unique_candidate"):
        if flag in when:
            checked[flag] = bool(when[flag])

    if whole and not checked:
        raise RuleError("conditions_required", "A rule needs at least one condition")

    clean["when"] = checked
    return clean


def _positive_number(value: Any, code: str, message: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuleError(code, message) from None
    if number < 0:
        raise RuleError(code, message)
    return str(value)


def _validate_amount(rule: Any) -> dict[str, Any]:
    """Two different questions live in this block, and they are not the same.

    `match` asks how close the money has to be **to the promise**: exact,
    within a margin, net of withholding. `min` and `max` ask whether the
    rule applies to money of this size at all, which is what somebody
    means by "don't link anything over ten thousand on its own". Keeping
    both here rather than in separate blocks is how every tool that
    offers them presents them, and it reads as one sentence about amount.
    """
    if not isinstance(rule, dict):
        raise RuleError("bad_amount", "Unknown amount condition")
    match = rule.get("match", "exact")
    if match not in _AMOUNT_MATCHES:
        raise RuleError("bad_amount", "Unknown amount condition")
    checked: dict[str, Any] = {"match": match}

    for bound in ("min", "max"):
        value = rule.get(bound)
        if value in (None, ""):
            continue
        checked[bound] = _positive_number(
            value, "bad_amount_bound", "An amount limit must not be negative"
        )
    if "min" in checked and "max" in checked:
        if Decimal(checked["min"]) > Decimal(checked["max"]):
            raise RuleError(
                "impossible_amount_band",
                "The lower limit is above the upper one, so nothing can match",
            )
    if match == "tolerance":
        percent = _positive_number(
            rule.get("percent", "0"), "bad_tolerance", "The tolerance must not be negative"
        )
        if float(percent) > 100:
            raise RuleError(
                "tolerance_too_wide",
                "A tolerance above 100% would match every amount there is",
            )
        checked["percent"] = percent
    if match == "partial":
        # A floor, so a token payment against a large invoice is not
        # offered as an instalment. Expressed as a fraction of what is
        # outstanding rather than an absolute, because "at least five per
        # cent" means the same thing on a small invoice and a large one.
        for bound, default in (("min_ratio", "0.05"), ("max_ratio", "0.95")):
            raw = rule.get(bound, default)
            try:
                value = Decimal(str(raw))
            except (ArithmeticError, TypeError, ValueError):
                raise RuleError("bad_part_ratio", "That is not a fraction") from None
            if not 0 <= value <= 1:
                raise RuleError(
                    "bad_part_ratio", "A part payment is between none and all of it"
                )
            checked[bound] = str(raw)
        if Decimal(checked["min_ratio"]) > Decimal(checked["max_ratio"]):
            raise RuleError(
                "impossible_part_band",
                "The lower share is above the upper one, so nothing can match",
            )

    if match == "set":
        # How many promises one payment may cover, and how far the total
        # may be from what arrived. The cap is bounded again in the engine
        #: searching which invoices add up to a payment grows explosively,
        # and a sync that hangs is worse than a match that is not made.
        try:
            most = int(rule.get("max_invoices", 6))
        except (TypeError, ValueError):
            raise RuleError("bad_set_size", "That is not a number of invoices") from None
        if not 2 <= most <= 6:
            raise RuleError(
                "bad_set_size", "A combination holds between two and six invoices"
            )
        checked["max_invoices"] = most
        checked["percent"] = _positive_number(
            rule.get("percent", "0"), "bad_tolerance", "The tolerance must not be negative"
        )
        if Decimal(checked["percent"]) > 20:
            raise RuleError(
                "set_tolerance_too_wide",
                "Above twenty per cent almost any group of invoices adds up",
            )

    if match == "ratio":
        # The ratios themselves come from the jurisdiction pack, never from
        # a workspace: a wrong withholding rate would auto-link a wrong
        # amount, which is worse than asking.
        checked["ratios"] = "@jurisdiction.withholding_ratios"
        checked["epsilon"] = _positive_number(
            rule.get("epsilon", "0.02"), "bad_epsilon", "The margin must not be negative"
        )
        checked["difference_kind"] = "withholding_tax"
    return checked


def _validate_currency(rule: Any) -> dict[str, Any]:
    """Two independent questions again, and conflating them would hide one.

    `conversion` says whether a movement may settle a promise held in a
    different currency: a comparison rule. `in` and `foreign` say which
    money the rule is written for at all. Somebody who wants "dollars are
    reviewed by hand" needs the second, and has no use for the first.
    """
    if not isinstance(rule, dict):
        raise RuleError("bad_currency", "Unknown currency condition")
    # `allow` used to be accepted here and meant "stop comparing
    # currencies", which is not conversion: it settled a euro invoice
    # with dollars at face value. Refused on the way in rather than
    # quietly ignored, so a policy file carrying it is reported instead
    # of silently doing something other than what it says.
    conversion = rule.get("conversion", "reject")
    if conversion == "allow":
        raise RuleError(
            "conversion_unsupported",
            "Money in another currency is never matched. Converting it needs a rate and a place to book the difference.",
        )
    if conversion != "reject":
        raise RuleError("bad_currency", "Unknown currency condition")
    checked: dict[str, Any] = {"conversion": conversion}

    codes = rule.get("in")
    if codes:
        if not isinstance(codes, list) or not all(
            isinstance(code, str) and len(code) == 3 for code in codes
        ):
            raise RuleError("bad_currency_list", "Currencies are three-letter codes")
        checked["in"] = [code.upper() for code in codes]
    if rule.get("foreign"):
        checked["foreign"] = True
    return checked


def _validate_ids(rule: Any, code: str) -> list[str]:
    """A list of accounts or clients the rule is limited to.

    Stored as strings rather than UUID objects because this lives in JSON
    and has to survive a round trip through the database unchanged. They
    are compared as strings too, so a malformed id narrows the rule to
    nothing rather than widening it to everything: the safe direction to
    fail in when the thing being decided is whether money moves.
    """
    if isinstance(rule, dict):
        rule = rule.get("in")
    if not isinstance(rule, list) or not rule:
        raise RuleError(code, "Choose at least one, or leave the condition out")
    out = []
    for value in rule:
        try:
            out.append(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError):
            raise RuleError(code, "That is not something we can identify") from None
    return out


def _validate_text(rule: Any) -> dict[str, str]:
    """What the statement line has to say, or must not.

    The plainest of the new conditions and probably the most used: bank
    text is where the real identity of a payment hides, and matching on a
    fragment of it is how somebody encodes "this is the client whose
    transfers always carry their tax number" long before we can read that
    number structurally.
    """
    if not isinstance(rule, dict):
        raise RuleError("bad_text", "Unknown text condition")
    checked: dict[str, Any] = {}
    for key in ("contains", "not_contains"):
        value = rule.get(key)
        if value in (None, "", []):
            continue
        # One word or several. Several means *any of them*, which is how
        # somebody receiving through three acquirers writes one rule
        # instead of three that differ by a word.
        words = [value] if isinstance(value, str) else value
        if not isinstance(words, list):
            raise RuleError("bad_text", "Say what the text must contain")
        cleaned = []
        for word in words:
            if not isinstance(word, str) or len(word) > 120:
                raise RuleError("bad_text", "Keep the text short and plain")
            word = word.strip()
            if word:
                cleaned.append(word)
        if not cleaned:
            continue
        if len(cleaned) > 12:
            raise RuleError(
                "too_many_words",
                "A rule this long is easier to read as two rules",
            )
        # Stored as it was written: one word stays a string, so a rule
        # nobody touched does not show up as changed.
        checked[key] = cleaned[0] if len(cleaned) == 1 else cleaned
    if not checked:
        raise RuleError("bad_text", "Say what the text must contain")
    return checked


def _validate_window(rule: Any) -> dict[str, int]:
    if not isinstance(rule, dict):
        raise RuleError("bad_window", "Unknown date condition")
    checked = {}
    for side in ("before_days", "after_days"):
        try:
            days = int(rule.get(side, 0))
        except (TypeError, ValueError):
            raise RuleError("bad_window", "The date window must be in whole days") from None
        if days < 0 or days > 365:
            raise RuleError(
                "window_out_of_range", "The date window must be between 0 and 365 days"
            )
        checked[side] = days
    return checked


def _validate_similarity(rule: Any) -> dict[str, str]:
    if not isinstance(rule, dict):
        raise RuleError("bad_similarity", "Unknown description condition")
    try:
        minimum = float(rule.get("min", 0))
    except (TypeError, ValueError):
        raise RuleError("bad_similarity", "Unknown description condition") from None
    if not 0 <= minimum <= 1:
        raise RuleError("similarity_out_of_range", "Similarity runs from 0 to 1")
    # The value the check above validated, not a second lookup: a
    # `description_similarity` carrying no `min` passed on the default
    # and was then stored as the string "None", which no consumer can
    # parse.
    return {"min": str(rule.get("min", 0))}


async def upsert_override(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    node: str,
    strategy_id: str,
    config: dict[str, Any],
    position: Optional[int] = None,
) -> ReconciliationRule:
    """Record a change to one of the rules we ship."""
    if node not in EDITABLE_NODES:
        raise RuleError("unknown_node", "That set of rules cannot be edited")
    shipped = {s["id"] for s in reconciliation_policy.default_policy(node)["strategies"]}
    if strategy_id not in shipped:
        raise RuleError("unknown_rule", "No such rule in this set")

    clean = validate_config(config, whole=False)
    row = await _find(session, workspace_id, node, strategy_id)

    # Merged rather than replaced: a screen that sends one changed field
    # must not erase a change made from another screen, or last week.
    # Then pruned, so what remains is only the disagreement.
    merged = _merge(row.config if row else {}, clean)
    shipped_strategy = next(
        s
        for s in reconciliation_policy.default_policy(node)["strategies"]
        if s["id"] == strategy_id
    )
    pruned = _prune(merged, shipped_strategy)

    keeps_position = row is not None and row.position is not None
    if (
        not pruned
        and position is None
        and not keeps_position
        and not (row is not None and row.deleted)
    ):
        # Nothing left to disagree about. Setting a value back to what we
        # ship is the same act as restoring it, so the row goes and the
        # rule returns to the live default rather than lingering as a
        # copy that happens to agree today.
        #
        # Except when the row is a tombstone: "I do not want this rule"
        # is a disagreement even when every threshold matches ours, and
        # dropping the row here would quietly bring back a rule somebody
        # deleted. Or when it carries a position: reordering writes one
        # for every rule in the node, so deleting the row would drop this
        # rule back to its shipped index while its siblings kept theirs,
        # and the order somebody set would rearrange itself.
        if row is not None:
            await session.delete(row)
            await session.flush()
        return row or ReconciliationRule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user_id,
            node=node,
            strategy_id=strategy_id,
            origin="default",
            config={},
            policy_version=reconciliation_policy.POLICY_VERSION,
        )

    if row is None:
        row = ReconciliationRule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user_id,
            node=node,
            strategy_id=strategy_id,
            origin="default",
            policy_version=reconciliation_policy.POLICY_VERSION,
        )
        session.add(row)
    row.config = pruned
    if position is not None:
        row.position = position
    await session.flush()
    return row


async def reorder(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    node: str,
    strategy_ids: list[str],
) -> None:
    """Fix the order rules are tried in.

    Writes an explicit position for **every** rule in the node rather than
    only the one that moved. Order is the mechanism the whole feature
    rests on: the first rule that matches wins, so a band is expressed by
    what sits above and below it, and half-implicit ordering, where some
    rules carry a position and others fall back to where we happened to
    ship them, is the kind of thing that reads correctly today and
    silently rearranges the day a default is inserted.

    A row holding only a position is not a customised rule: its `config`
    stays empty, so it keeps inheriting every improvement.
    """
    if node not in EDITABLE_NODES:
        raise RuleError("unknown_node", "That set of rules cannot be reordered")

    known = {s["id"] for s in reconciliation_policy.default_policy(node)["strategies"]}
    rows = {row.strategy_id: row for row in await overrides_for(session, workspace_id, node)}
    known |= {row_id for row_id, row in rows.items() if row.origin == "custom"}
    # A rule this workspace deleted is not in the list any more, so
    # naming every rule cannot mean naming that one.
    known -= {row_id for row_id, row in rows.items() if row.deleted}

    unknown = [rid for rid in strategy_ids if rid not in known]
    if unknown:
        raise RuleError("unknown_rule", "No such rule in this set")
    if len(set(strategy_ids)) != len(strategy_ids):
        raise RuleError("duplicate_rule", "A rule cannot be in two places at once")
    if set(strategy_ids) != known:
        raise RuleError(
            "incomplete_order",
            "Reordering names every rule in the set, so none is left implicit",
        )

    for place, strategy_id in enumerate(strategy_ids):
        row = rows.get(strategy_id)
        if row is None:
            row = ReconciliationRule(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                user_id=user_id,
                node=node,
                strategy_id=strategy_id,
                origin="default",
                config={},
                policy_version=reconciliation_policy.POLICY_VERSION,
            )
            session.add(row)
        row.position = place
    await session.flush()


async def create_custom(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    node: str,
    name: str,
    config: dict[str, Any],
    position: Optional[int] = None,
) -> ReconciliationRule:
    """Store a rule the workspace wrote itself."""
    if node not in EDITABLE_NODES:
        raise RuleError("unknown_node", "That set of rules cannot be edited")
    if not name.strip():
        raise RuleError("name_required", "Give the rule a name")

    strategy_id = slug(name)
    if await _find(session, workspace_id, node, strategy_id) is not None:
        strategy_id = f"{strategy_id}_{uuid.uuid4().hex[:6]}"

    row = ReconciliationRule(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        node=node,
        strategy_id=strategy_id,
        origin="custom",
        name=name.strip(),
        position=position,
        config=validate_config(config, whole=True),
        policy_version=reconciliation_policy.POLICY_VERSION,
    )
    session.add(row)
    await session.flush()
    return row


async def update_custom(
    session: AsyncSession,
    row: ReconciliationRule,
    name: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    position: Optional[int] = None,
) -> ReconciliationRule:
    if name is not None:
        if not name.strip():
            raise RuleError("name_required", "Give the rule a name")
        row.name = name.strip()
    if config is not None:
        row.config = validate_config(config, whole=True)
    if position is not None:
        row.position = position
    await session.flush()
    return row


async def delete_rule(
    session: AsyncSession, workspace_id: uuid.UUID, node: str, strategy_id: str
) -> None:
    """Get rid of a rule, whoever wrote it.

    A rule the workspace wrote is deleted by deleting its row: it exists
    nowhere else. One of ours cannot be, because it is a document in the
    image and would be back on the next start, so the row records that
    this workspace does not want it, and `compose` leaves it out.

    There is no rule we refuse to remove. A matching policy decides what
    happens to somebody's money, and a default we happen to believe in is
    not a reason to make them keep it. What we do keep is the name, so
    the page can offer it back.
    """
    if node not in EDITABLE_NODES:
        raise RuleError("unknown_node", "That set of rules cannot be edited")

    row = await _find(session, workspace_id, node, strategy_id)
    if row is not None and row.origin == "custom":
        await session.delete(row)
        await session.flush()
        return

    shipped = {s["id"] for s in reconciliation_policy.default_policy(node)["strategies"]}
    if strategy_id not in shipped:
        raise RuleError("unknown_rule", "No such rule in this set")

    if row is None:
        row = ReconciliationRule(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            node=node,
            strategy_id=strategy_id,
            origin="default",
            config={},
            policy_version=reconciliation_policy.POLICY_VERSION,
        )
        session.add(row)
    row.deleted = True
    await session.flush()


async def reset(
    session: AsyncSession, workspace_id: uuid.UUID, node: str, strategy_id: str
) -> None:
    """Forget a change, and go back to whatever we ship *today*.

    Not "go back to what shipped when you changed it": deleting the row
    puts the rule back under the live default, which is the point of not
    copying defaults in the first place.

    One row holds every kind of disagreement, so one verb undoes all of
    them: a threshold somebody moved, a place in the order, and a rule
    somebody deleted all go back to shipped together.
    """
    row = await _find(session, workspace_id, node, strategy_id)
    if row is not None:
        await session.delete(row)
        await session.flush()


async def _find(
    session: AsyncSession, workspace_id: uuid.UUID, node: str, strategy_id: str
) -> Optional[ReconciliationRule]:
    result = await session.execute(
        select(ReconciliationRule).where(
            ReconciliationRule.workspace_id == workspace_id,
            ReconciliationRule.node == node,
            ReconciliationRule.strategy_id == strategy_id,
        )
    )
    return result.scalar_one_or_none()
