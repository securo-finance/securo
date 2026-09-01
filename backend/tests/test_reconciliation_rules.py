"""Rules a workspace can see and change, and what changing them does.

The claim under all of it: **matching is not a black box.** Every number
the engine consults is visible, editable, and reversible, and an untouched
rule keeps improving with the product rather than freezing on the day the
workspace was created.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.payee import Payee
from app.models.reconciliation import ReconciliationRule, ReconciliationSuggestion
from app.models.transaction import Transaction
from app.services import reconciliation_policy, reconciliation_rule_service as rules

TODAY = date.today()
INVOICE_NODE = reconciliation_policy.MATCH_INVOICE["node"]
RECURRING_NODE = reconciliation_policy.MATCH_RECURRING["node"]


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Estudio", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def account(session: AsyncSession, business_ws, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Conta PJ",
        type="checking",
        currency="USD",
        balance=Decimal("0"),
    )
    session.add(acc)
    await session.commit()
    return acc


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Alpha Tecnologia",
        source="manual",
    )
    session.add(payee)
    await session.commit()
    return payee


async def a_payment(
    client: AsyncClient, headers: dict, account: Account, payee: Payee,
    *, amount: str = "3000.00", when: date | None = None,
) -> dict:
    resp = await client.post(
        "/api/transactions",
        headers=headers,
        json={
            "description": "PIX RECEBIDO ALPHA",
            "amount": amount,
            "currency": "USD",
            "date": str(when or TODAY),
            "type": "credit",
            "account_id": str(account.id),
            "payee_id": str(payee.id),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def an_invoice(
    client: AsyncClient, headers: dict, payee: Payee, *, total: str = "3000.00"
) -> dict:
    resp = await client.post(
        "/api/invoices",
        headers=headers,
        json={"total": total, "due_date": str(TODAY), "payee_id": str(payee.id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def find(nodes: list[dict], node: str, rule_id: str) -> dict:
    for entry in nodes:
        if entry["node"] == node:
            for rule in entry["rules"]:
                if rule["id"] == rule_id:
                    return rule
    raise AssertionError(f"{rule_id} not found in {node}")


# ---------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_shipped_rules_are_visible_without_anyone_creating_them(
    client: AsyncClient, biz_headers
):
    """Nothing was written to the database, and yet the rules are there.

    That is the design: the defaults ship with the image, so a workspace
    created a year ago sees the rules we ship today rather than the ones
    that existed the day it was made."""
    resp = await client.get("/api/reconciliation/rules", headers=biz_headers)
    assert resp.status_code == 200, resp.text
    nodes = resp.json()

    assert {n["node"] for n in nodes} == {INVOICE_NODE, RECURRING_NODE}
    exact = find(nodes, INVOICE_NODE, "same_client_exact")
    assert exact["enabled"] is True
    assert exact["outcome"] == "link"
    assert exact["customised"] is False
    assert exact["when"]["amount"]["match"] == "exact"


@pytest.mark.asyncio
async def test_a_personal_workspace_sees_recurring_rules_but_not_live_invoice_ones(
    client: AsyncClient, auth_headers
):
    """Reconciliation is not a business feature. Somebody who never issues
    a document still has promises, and the rules that match them are
    theirs to change."""
    resp = await client.get("/api/reconciliation/rules", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    by_node = {n["node"]: n for n in resp.json()}

    assert by_node[RECURRING_NODE]["active"] is True
    assert by_node[RECURRING_NODE]["rules"], "a personal workspace has recurring rules"
    # Shown, but honestly marked: pretending the invoice rules were live
    # here would be a lie the page tells on every load.
    assert by_node[INVOICE_NODE]["active"] is False


@pytest.mark.asyncio
async def test_the_placeholder_rules_are_not_offered(client: AsyncClient, biz_headers):
    """It decides whether an arriving charge is a row we generated
    ourselves — bookkeeping about our own duplicates, not a judgement
    about whose money this is. A lever whose only effect is duplicate rows
    does not belong on a page."""
    resp = await client.get("/api/reconciliation/rules", headers=biz_headers)
    assert "reconciliation.match_placeholder" not in {n["node"] for n in resp.json()}


# ---------------------------------------------------------------------------
# Changing them
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_turning_a_rule_off_stops_it_matching(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The whole point of the page: a number in the engine is now a
    decision somebody can reverse."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"enabled": False},
    )
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/exact_amount_any_client",
        headers=biz_headers,
        json={"enabled": False},
    )

    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == []


@pytest.mark.asyncio
async def test_a_change_stores_only_what_changed(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """A workspace that widened the window must keep every other signal
    live, including improvements shipped later. Storing the whole rule
    would freeze the parts nobody touched."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"date": {"before_days": 30, "after_days": 90}}},
    )

    result = await session.execute(
        select(ReconciliationRule).where(
            ReconciliationRule.workspace_id == uuid.UUID(business_ws["id"])
        )
    )
    row = result.scalar_one()
    assert row.config == {"when": {"date": {"before_days": 30, "after_days": 90}}}
    assert "amount" not in row.config["when"], "an untouched signal is not stored"

    # And the composed rule still carries the shipped parts.
    resp = await client.get("/api/reconciliation/rules", headers=biz_headers)
    rule = find(resp.json(), INVOICE_NODE, "same_client_exact")
    assert rule["when"]["date"] == {"before_days": 30, "after_days": 90}
    assert rule["when"]["amount"]["match"] == "exact"
    assert rule["customised"] is True


@pytest.mark.asyncio
async def test_resetting_returns_to_what_ships_today(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """Not to what shipped when it was changed. Deleting the row puts the
    rule back under the live default, which is the entire reason the
    defaults are not copied in."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"enabled": False},
    )
    resp = await client.delete(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
    )
    assert resp.status_code == 204

    rule = find(
        (await client.get("/api/reconciliation/rules", headers=biz_headers)).json(),
        INVOICE_NODE,
        "same_client_exact",
    )
    assert rule["enabled"] is True
    assert rule["customised"] is False


@pytest.mark.asyncio
async def test_a_rule_that_would_match_everything_is_refused(
    client: AsyncClient, biz_headers
):
    """Configurable is not the same as unguarded. A tolerance above 100%
    matches every amount there is, and a rule that links everything to
    everything is not a preference — it is a broken ledger."""
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"amount": {"match": "tolerance", "percent": "400"}}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "tolerance_too_wide"


@pytest.mark.asyncio
async def test_a_shape_the_engine_cannot_read_is_refused_on_the_way_in(
    client: AsyncClient, biz_headers
):
    """A rule naming a match mode the engine never heard of would not fail
    loudly — it would quietly stop matching, and nobody would find out
    until a month of payments had gone unreconciled."""
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"amount": {"match": "vibes"}}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_amount"


# ---------------------------------------------------------------------------
# Rules a workspace writes itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_workspace_can_write_its_own_rule_and_run_it_first(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Order matters — the first rule that matches wins — so a rule
    somebody wrote is worth little if it can only ever run last."""
    resp = await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={
            "node": INVOICE_NODE,
            "name": "Cliente conhecido, valor aproximado",
            "outcome": "suggest",
            "position": 0,
            "when": {
                "counterparty": "same_payee",
                "amount": {"match": "tolerance", "percent": "5"},
                "date": {"before_days": 10, "after_days": 60},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["origin"] == "custom"
    assert created["id"].startswith("custom_")

    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    invoice_rules = next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]
    assert invoice_rules[0]["id"] == created["id"], "it runs before the shipped ones"


@pytest.mark.asyncio
async def test_a_custom_rule_cannot_claim_the_name_of_one_of_ours(
    client: AsyncClient, biz_headers
):
    """Ids share a namespace, so a workspace rule is prefixed. Otherwise a
    rule could shadow a shipped one and the override table would stop
    meaning what it says."""
    resp = await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={
            "node": INVOICE_NODE,
            "name": "same_client_exact",
            "outcome": "link",
            "when": {"amount": {"match": "exact"}},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "custom_same_client_exact"


@pytest.mark.asyncio
async def test_a_custom_rule_without_conditions_is_refused(
    client: AsyncClient, biz_headers
):
    resp = await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={"node": INVOICE_NODE, "name": "Tudo", "outcome": "link", "when": {}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "conditions_required"


# ---------------------------------------------------------------------------
# The doubtful space
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_demoting_a_rule_sends_the_match_to_the_queue_instead(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The switch that would be a lie if the queue did not exist: a rule
    set to suggest has to produce something a person can see."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == [], "nothing was linked"

    queue = await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    assert queue.status_code == 200, queue.text
    assert len(queue.json()) == 1
    offered = queue.json()[0]
    assert offered["expectation_kind"] == "invoice"
    assert offered["expectation_id"] == invoice["id"]
    assert offered["strategy_id"] == "same_client_exact"


@pytest.mark.asyncio
async def test_the_queue_shows_the_evidence_and_not_a_single_number(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """"We are 78% sure" is not something anyone can check. "The amount is
    exact and the payer is known" is."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    offered = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()[0]

    assert offered["scores"]["amount_exact"] is True
    assert offered["scores"]["same_counterparty"] is True
    assert offered["scores"]["days_apart"] == 0
    assert offered["transaction"]["description"] == "PIX RECEBIDO ALPHA"
    # And the promise is named the way every invoice screen names it, so
    # the queue and the document the client is holding agree.
    assert offered["expectation_label"] == "1"


@pytest.mark.asyncio
async def test_accepting_settles_the_invoice_and_records_which_rule_agreed(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)
    offered = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()[0]

    resp = await client.post(
        f"/api/reconciliation/suggestions/{offered['id']}/accept", headers=biz_headers
    )
    assert resp.status_code == 200, resp.text

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert len(detail["allocations"]) == 1
    # The person agreed with a specific rule, and that is worth keeping.
    assert detail["allocations"][0]["method"] == "same_client_exact"
    # `state`, not `status`: the stored column records what a person
    # decided, and paid is a fact about money that is derived per read.
    assert detail["state"] == "paid"


@pytest.mark.asyncio
async def test_a_declined_suggestion_never_comes_back(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee,
    business_ws,
):
    """The single most important behaviour in the queue. Re-asking
    yesterday's question is how people stop reading it — and then they
    stop reading the good ones too."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    await an_invoice(client, biz_headers, client_payee)
    payment = await a_payment(client, biz_headers, account, client_payee)
    offered = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()[0]

    await client.post(
        f"/api/reconciliation/suggestions/{offered['id']}/decline", headers=biz_headers
    )

    # Run matching over the same money again, exactly as the next sync
    # would. Nothing new may appear.
    from app.services import reconciliation_service

    result = await session.execute(
        select(Transaction).where(Transaction.id == uuid.UUID(payment["id"]))
    )
    transaction = result.scalar_one()
    await reconciliation_service.match_incoming(
        session, uuid.UUID(business_ws["id"]), [transaction]
    )
    await session.commit()

    assert (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json() == []

    result = await session.execute(select(ReconciliationSuggestion))
    rows = result.unique().scalars().all()
    assert len(rows) == 1 and rows[0].status == "declined", "the refusal is remembered"


@pytest.mark.asyncio
async def test_a_question_nobody_answered_expires_on_its_own(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """A queue that only grows is an archive. Expired rather than deleted,
    so the forgotten question is not immediately re-asked."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    result = await session.execute(select(ReconciliationSuggestion))
    row = result.unique().scalar_one()
    row.created_at = row.created_at - timedelta(days=90)
    await session.commit()

    assert (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json() == []

    await session.refresh(row)
    assert row.status == "expired"


@pytest.mark.asyncio
async def test_a_viewer_can_read_the_rules_but_not_change_them(
    client: AsyncClient, viewer_auth_headers, biz_headers, business_ws
):
    """Matching decides what a ledger says, so who may change it is not a
    cosmetic question."""
    headers = {**viewer_auth_headers, "X-Workspace-Id": business_ws["id"]}
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=headers,
        json={"enabled": False},
    )
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Composition, below HTTP
# ---------------------------------------------------------------------------
def test_a_sparse_patch_leaves_everything_it_did_not_mention():
    row = ReconciliationRule(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        node=INVOICE_NODE,
        strategy_id="same_client_exact",
        origin="default",
        config={"enabled": False},
    )
    composed = rules.compose(INVOICE_NODE, [row])
    changed = next(s for s in composed["strategies"] if s["id"] == "same_client_exact")

    assert changed["enabled"] is False
    assert changed["outcome"] == "link", "untouched, so still what we ship"
    assert changed["when"]["amount"]["match"] == "exact"


def test_the_shipped_document_is_never_mutated_by_composing():
    """`compose` runs on every match. One caller editing the module-level
    default would change matching for every workspace in the process."""
    row = ReconciliationRule(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        node=INVOICE_NODE,
        strategy_id="same_client_exact",
        origin="default",
        config={"enabled": False},
    )
    rules.compose(INVOICE_NODE, [row])

    shipped = reconciliation_policy.MATCH_INVOICE["strategies"][0]
    assert shipped["id"] == "same_client_exact"
    assert shipped["enabled"] is True


def test_narrowing_for_a_weekly_bill_only_ever_tightens():
    """Somebody who widened the monthly window said something reasonable
    about how late their bills post. Letting that reach into next week's
    occurrence is not a preference they expressed."""
    row = ReconciliationRule(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        node=RECURRING_NODE,
        strategy_id="same_account_exact",
        origin="default",
        config={"when": {"date": {"before_days": 10, "after_days": 10}}},
    )
    composed = rules.compose(RECURRING_NODE, [row])
    monthly = rules.narrow_for_frequency(composed, "monthly")
    weekly = rules.narrow_for_frequency(composed, "weekly")

    assert monthly["strategies"][0]["when"]["date"] == {
        "before_days": 10,
        "after_days": 10,
    }
    assert weekly["strategies"][0]["when"]["date"] == {
        "before_days": 2,
        "after_days": 2,
    }


def test_a_tighter_choice_survives_the_narrowing():
    """It clamps, it does not overwrite: somebody who asked for one day
    keeps one day, not the two a weekly bill would allow."""
    row = ReconciliationRule(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        node=RECURRING_NODE,
        strategy_id="same_account_exact",
        origin="default",
        config={"when": {"date": {"before_days": 1, "after_days": 1}}},
    )
    weekly = rules.narrow_for_frequency(
        rules.compose(RECURRING_NODE, [row]), "weekly"
    )
    assert weekly["strategies"][0]["when"]["date"] == {
        "before_days": 1,
        "after_days": 1,
    }


# ---------------------------------------------------------------------------
# What a rule may say, and what it may not
# ---------------------------------------------------------------------------
class TestConditionValidation:
    """Writes are checked on the way in, never on the way out.

    A malformed rule discovered during a sync would stop matching
    silently, and the person who broke it would be nowhere near a screen
    by then. So every shape the engine can read is accepted here, every
    shape it cannot is refused with a code the UI can translate, and the
    stored document is the cleaned version rather than whatever arrived.
    """

    def check(self, when: dict, *, whole: bool = False) -> dict:
        return rules.validate_config(
            {"outcome": "link", "when": when}, whole=whole
        )["when"]

    def refused(self, when: dict) -> str:
        with pytest.raises(rules.RuleError) as caught:
            rules.validate_config({"outcome": "link", "when": when}, whole=False)
        return caught.value.code

    # -- accounts ----------------------------------------------------------
    def test_accounts_are_stored_as_canonical_ids(self):
        account = uuid.uuid4()
        assert self.check({"accounts": {"in": [str(account).upper()]}}) == {
            "accounts": {"in": [str(account)]}
        }

    def test_a_bare_list_of_accounts_is_accepted(self):
        """The screen sends `{in: [...]}`; a script may reasonably send the
        list itself. Both mean the same thing."""
        account = uuid.uuid4()
        assert self.check({"accounts": [str(account)]}) == {
            "accounts": {"in": [str(account)]}
        }

    def test_something_that_is_not_an_account_is_refused(self):
        assert self.refused({"accounts": {"in": ["the checking one"]}}) == "bad_accounts"

    def test_an_empty_account_list_is_refused_rather_than_ignored(self):
        """An empty list reads as "no accounts", which would silently
        disable the rule. Leaving the condition out is how you say "any"."""
        assert self.refused({"accounts": {"in": []}}) == "bad_accounts"

    # -- payees ------------------------------------------------------------
    def test_payees_are_validated_like_accounts(self):
        payee = uuid.uuid4()
        assert self.check({"payees": {"in": [str(payee)]}}) == {
            "payees": {"in": [str(payee)]}
        }
        assert self.refused({"payees": {"in": ["Alpha"]}}) == "bad_payees"

    # -- direction ---------------------------------------------------------
    @pytest.mark.parametrize("value", ["any", "credit", "debit"])
    def test_the_three_directions_are_accepted(self, value):
        assert self.check({"direction": value}) == {"direction": value}

    def test_a_direction_that_is_neither_in_nor_out_is_refused(self):
        assert self.refused({"direction": "sideways"}) == "bad_direction"

    # -- currency ----------------------------------------------------------
    def test_currency_codes_are_normalised_to_upper_case(self):
        assert self.check({"currency": {"in": ["usd", "eur"]}}) == {
            "currency": {"conversion": "reject", "in": ["USD", "EUR"]}
        }

    def test_something_that_is_not_a_currency_code_is_refused(self):
        assert self.refused({"currency": {"in": ["dollars"]}}) == "bad_currency_list"

    def test_foreign_is_kept_only_when_asked_for(self):
        assert "foreign" not in self.check({"currency": {"foreign": False}})["currency"]
        assert self.check({"currency": {"foreign": True}})["currency"]["foreign"] is True

    def test_conversion_still_defaults_to_refusing(self):
        """Binding across currencies means inventing a rate nobody chose,
        so silence has to mean no."""
        assert self.check({"currency": {"in": ["USD"]}})["currency"]["conversion"] == "reject"

    def test_an_unknown_conversion_mode_is_refused(self):
        assert self.refused({"currency": {"conversion": "guess"}}) == "bad_currency"

    # -- amount ------------------------------------------------------------
    def test_a_band_is_stored_alongside_the_comparison(self):
        assert self.check(
            {"amount": {"match": "exact", "min": "100", "max": "10000"}}
        ) == {"amount": {"match": "exact", "min": "100", "max": "10000"}}

    def test_an_empty_bound_is_dropped_rather_than_stored_as_zero(self):
        """A cleared field means "no limit". Storing it as zero would turn
        an erased ceiling into a floor that blocks nothing but reads as a
        rule somebody wrote."""
        assert self.check({"amount": {"match": "exact", "min": "", "max": None}}) == {
            "amount": {"match": "exact"}
        }

    def test_a_negative_limit_is_refused(self):
        assert (
            self.refused({"amount": {"match": "exact", "min": "-5"}})
            == "bad_amount_bound"
        )

    def test_a_band_that_can_never_match_is_refused(self):
        """Above 10000 and below 100 is not a narrow rule, it is a rule
        that will never fire — and it would look fine on the screen."""
        assert (
            self.refused({"amount": {"match": "exact", "min": "10000", "max": "100"}})
            == "impossible_amount_band"
        )

    def test_a_tolerance_over_a_hundred_percent_is_still_refused(self):
        assert (
            self.refused({"amount": {"match": "tolerance", "percent": "150"}})
            == "tolerance_too_wide"
        )

    def test_the_withholding_ratios_stay_out_of_a_workspaces_hands(self):
        """A wrong rate would auto-link a wrong amount. The rates come from
        the jurisdiction pack, and anything sent here is discarded."""
        checked = self.check(
            {"amount": {"match": "ratio", "ratios": ["0.5"], "epsilon": "0.02"}}
        )
        assert checked["amount"]["ratios"] == "@jurisdiction.withholding_ratios"

    # -- text --------------------------------------------------------------
    def test_text_conditions_are_trimmed(self):
        assert self.check({"text": {"contains": "  PIX  "}}) == {
            "text": {"contains": "PIX"}
        }

    def test_both_sides_of_a_text_condition_can_be_set(self):
        assert self.check({"text": {"contains": "PIX", "not_contains": "ESTORNO"}}) == {
            "text": {"contains": "PIX", "not_contains": "ESTORNO"}
        }

    def test_an_empty_text_condition_is_refused(self):
        assert self.refused({"text": {"contains": ""}}) == "bad_text"

    def test_an_essay_is_refused(self):
        assert self.refused({"text": {"contains": "x" * 200}}) == "bad_text"

    # -- shape -------------------------------------------------------------
    def test_an_unknown_signal_is_dropped_rather_than_stored(self):
        """Storing a key the engine cannot read would produce a rule that
        looks stricter on screen than it is in fact."""
        assert "vibes" not in self.check({"vibes": "good", "direction": "credit"})

    def test_a_window_beyond_a_year_is_refused(self):
        assert (
            self.refused({"date": {"before_days": 5, "after_days": 900}})
            == "window_out_of_range"
        )

    def test_a_similarity_outside_zero_to_one_is_refused(self):
        assert (
            self.refused({"description_similarity": {"min": "5"}})
            == "similarity_out_of_range"
        )

    def test_a_workspaces_own_rule_needs_at_least_one_condition(self):
        with pytest.raises(rules.RuleError) as caught:
            rules.validate_config({"outcome": "link", "when": {}}, whole=True)
        assert caught.value.code == "conditions_required"

    def test_a_patch_over_a_shipped_rule_may_touch_a_single_signal(self):
        """The whole point of a sparse patch: widening one window must not
        require restating every other condition."""
        patch = rules.validate_config(
            {"when": {"date": {"before_days": 20, "after_days": 90}}}, whole=False
        )
        assert patch == {"when": {"date": {"before_days": 20, "after_days": 90}}}


# ---------------------------------------------------------------------------
# The conditions, through the API, against a real workspace
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_rule_limited_to_one_account_leaves_another_alone(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee,
    test_user, business_ws,
):
    """The end-to-end shape of "only automate the account the gateway pays
    into"."""
    from app.models.account import Account as AccountModel

    other = AccountModel(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Conta operacional",
        type="checking",
        currency="USD",
        balance=Decimal("0"),
    )
    session.add(other)
    await session.commit()

    for rule_id in ("same_client_exact", "exact_amount_any_client"):
        await client.patch(
            f"/api/reconciliation/rules/{INVOICE_NODE}/{rule_id}",
            headers=biz_headers,
            json={"when": {"accounts": {"in": [str(account.id)]}}},
        )

    invoice = await an_invoice(client, biz_headers, client_payee)
    resp = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "PIX RECEBIDO ALPHA",
            "amount": "3000.00",
            "currency": "USD",
            "date": str(TODAY),
            "type": "credit",
            "account_id": str(other.id),
            "payee_id": str(client_payee.id),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == [], "the rule was not written for that account"


@pytest.mark.asyncio
async def test_a_ceiling_keeps_a_large_payment_out_of_the_automatic_tier(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """*"Anything over a thousand I want to look at myself."*"""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"amount": {"match": "exact", "max": "1000"}}},
    )
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/exact_amount_any_client",
        headers=biz_headers,
        json={"when": {"amount": {"match": "exact", "max": "1000"}}},
    )

    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == []


@pytest.mark.asyncio
async def test_a_text_condition_keeps_a_reversal_from_settling_an_invoice(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The amount and the client are both right, which is exactly why the
    statement text has to be able to overrule them."""
    for rule_id in ("same_client_exact", "exact_amount_any_client"):
        await client.patch(
            f"/api/reconciliation/rules/{INVOICE_NODE}/{rule_id}",
            headers=biz_headers,
            json={"when": {"text": {"not_contains": "ESTORNO"}}},
        )

    invoice = await an_invoice(client, biz_headers, client_payee)
    resp = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "TED ESTORNO PARCIAL ALPHA",
            "amount": "3000.00",
            "currency": "USD",
            "date": str(TODAY),
            "type": "credit",
            "account_id": str(account.id),
            "payee_id": str(client_payee.id),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == []


@pytest.mark.asyncio
async def test_a_rule_naming_one_client_does_not_touch_another(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee,
    test_user, business_ws,
):
    from app.models.payee import Payee as PayeeModel

    stranger = PayeeModel(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Beta Servicos",
        source="manual",
    )
    session.add(stranger)
    await session.commit()

    for rule_id in ("same_client_exact", "exact_amount_any_client"):
        await client.patch(
            f"/api/reconciliation/rules/{INVOICE_NODE}/{rule_id}",
            headers=biz_headers,
            json={"when": {"payees": {"in": [str(stranger.id)]}}},
        )

    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert detail.json()["allocations"] == []


@pytest.mark.asyncio
async def test_a_conditions_effect_is_undone_by_restoring_the_rule(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Every condition has to be reversible, or the page is a trap rather
    than a control."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"amount": {"match": "exact", "max": "10"}}},
    )
    await client.delete(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
    )

    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    assert len(detail.json()["allocations"]) == 1


# ---------------------------------------------------------------------------
# An override stores the disagreement, and nothing else
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_form_that_sends_the_whole_rule_still_stores_only_the_change(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """A form holds every field whether or not somebody touched it, so the
    screen sends the whole rule. Storing that verbatim would freeze the
    signals nobody had an opinion about, and this workspace would stop
    receiving improvements to them — the exact failure the design exists
    to avoid. The difference is taken on the server, where no future
    caller can forget it."""
    shipped = find(
        (await client.get("/api/reconciliation/rules", headers=biz_headers)).json(),
        INVOICE_NODE,
        "same_client_exact",
    )
    whole = {**shipped["when"], "amount": {**shipped["when"]["amount"], "max": "10000"}}

    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": shipped["outcome"], "when": whole},
    )
    assert resp.status_code == 200, resp.text

    result = await session.execute(
        select(ReconciliationRule).where(
            ReconciliationRule.workspace_id == uuid.UUID(business_ws["id"])
        )
    )
    row = result.scalar_one()
    assert row.config == {"when": {"amount": {"max": "10000"}}}, "only the ceiling is ours"

    # And the composed rule still carries everything, so the page reads the
    # same as it did before.
    rule = find(
        (await client.get("/api/reconciliation/rules", headers=biz_headers)).json(),
        INVOICE_NODE,
        "same_client_exact",
    )
    assert rule["when"]["counterparty"] == "same_payee"
    assert rule["when"]["date"] == shipped["when"]["date"]
    assert rule["when"]["amount"]["max"] == "10000"
    assert rule["customised"] is True


@pytest.mark.asyncio
async def test_setting_a_value_back_to_ours_stops_being_an_override(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """Typing the shipped number back in is the same act as restoring the
    rule. Leaving a row that happens to agree today would silently freeze
    that signal tomorrow."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"date": {"before_days": 30, "after_days": 90}}},
    )
    shipped_window = reconciliation_policy.MATCH_INVOICE["strategies"][0]["when"]["date"]

    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"date": dict(shipped_window)}},
    )

    result = await session.execute(select(ReconciliationRule))
    assert result.scalars().all() == [], "nothing left to disagree about"

    rule = find(
        (await client.get("/api/reconciliation/rules", headers=biz_headers)).json(),
        INVOICE_NODE,
        "same_client_exact",
    )
    assert rule["customised"] is False


@pytest.mark.asyncio
async def test_two_separate_edits_accumulate_rather_than_replace(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """Somebody who set a ceiling last week and a text exclusion today has
    said two things, not one."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"amount": {"match": "exact", "max": "10000"}}},
    )
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"when": {"text": {"not_contains": "ESTORNO"}}},
    )

    result = await session.execute(select(ReconciliationRule))
    row = result.scalar_one()
    assert row.config == {
        "when": {
            "amount": {"max": "10000"},
            "text": {"not_contains": "ESTORNO"},
        }
    }


def test_pruning_keeps_only_what_differs():
    shipped = {
        "id": "x",
        "outcome": "link",
        "when": {
            "counterparty": "same_payee",
            "amount": {"match": "exact"},
            "date": {"before_days": 10, "after_days": 60},
        },
    }
    patch = {
        "outcome": "link",
        "when": {
            "counterparty": "same_payee",
            "amount": {"match": "exact", "max": "500"},
            "date": {"before_days": 10, "after_days": 90},
        },
    }
    assert rules._prune(patch, shipped) == {
        "when": {"amount": {"max": "500"}, "date": {"after_days": 90}}
    }


def test_pruning_an_identical_rule_leaves_nothing():
    shipped = {"outcome": "link", "when": {"amount": {"match": "exact"}}}
    assert rules._prune(dict(shipped), shipped) == {}
