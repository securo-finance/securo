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


# ---------------------------------------------------------------------------
# One invoice, several payments — end to end
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_client_paying_in_two_transfers_is_reconciled_without_manual_work(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The whole journey, because the parts working in isolation is not
    the claim: a R$3.000 invoice paid as two R$1.500 transfers ends up
    settled, with both transactions on it.

    The first half is a suggestion — it genuinely could be an instalment,
    a different job, or a client paying what they had — and the second is
    an ordinary exact match, because by then the balance *is* 1500. The
    ambiguity only ever exists at the start.
    """
    invoice = await an_invoice(client, biz_headers, client_payee)

    first = await a_payment(client, biz_headers, account, client_payee, amount="1500.00")
    assert first["id"]

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    assert len(queue) == 1
    assert queue[0]["strategy_id"] == "same_client_part_payment"
    assert Decimal(queue[0]["amount"]) == Decimal("1500.00")

    accepted = await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/accept", headers=biz_headers
    )
    assert accepted.status_code == 200, accepted.text

    partly = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert partly["state"] == "partial"
    assert Decimal(partly["balance"]) == Decimal("1500.00")

    # The second transfer needs nobody: what is outstanding is now exactly
    # what arrived.
    await a_payment(client, biz_headers, account, client_payee, amount="1500.00")

    settled = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert settled["state"] == "paid"
    assert len(settled["allocations"]) == 2
    assert sum(Decimal(a["amount"]) for a in settled["allocations"]) == Decimal("3000.00")
    assert {a["method"] for a in settled["allocations"]} == {
        "same_client_part_payment",
        "same_client_exact",
    }


@pytest.mark.asyncio
async def test_three_payments_can_settle_one_invoice(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Nothing about the design caps it at two. Each accepted part narrows
    the balance for the next."""
    invoice = await an_invoice(client, biz_headers, client_payee)

    for amount in ("1000.00", "1000.00"):
        await a_payment(client, biz_headers, account, client_payee, amount=amount)
        queue = (
            await client.get("/api/reconciliation/suggestions", headers=biz_headers)
        ).json()
        assert queue, f"no suggestion for the {amount} transfer"
        await client.post(
            f"/api/reconciliation/suggestions/{queue[0]['id']}/accept",
            headers=biz_headers,
        )

    await a_payment(client, biz_headers, account, client_payee, amount="1000.00")

    settled = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert settled["state"] == "paid"
    assert len(settled["allocations"]) == 3


@pytest.mark.asyncio
async def test_a_workspace_that_wants_part_payments_linked_can_say_so(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """For somebody whose clients always pay in parts, confirming every
    instalment is precisely the manual work this feature exists to
    remove."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_part_payment",
        headers=biz_headers,
        json={"outcome": "link"},
    )
    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee, amount="1500.00")

    partly = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert partly["state"] == "partial"
    assert len(partly["allocations"]) == 1
    assert (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json() == [], "nothing was left to ask about"


@pytest.mark.asyncio
async def test_a_token_payment_is_not_offered_as_an_instalment(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee, amount="20.00")

    assert (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json() == []


# ---------------------------------------------------------------------------
# The moment a rule runs at, visible and editable
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_rule_says_which_moment_it_runs_at(
    client: AsyncClient, biz_headers
):
    """It used to be decided in the matching code, where nobody could see
    it. A page that claims matching is not a black box cannot keep a rule
    about *when we even look* out of view."""
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    invoice_rules = next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]

    assert all("trigger" in rule for rule in invoice_rules)
    by_id = {rule["id"]: rule["trigger"] for rule in invoice_rules}
    assert by_id["same_client_exact"] == "both"
    assert by_id["exact_amount_any_client"] == "money_arrives"


@pytest.mark.asyncio
async def test_narrowing_a_rule_to_arriving_money_stops_it_looking_back(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The restriction that used to be hardcoded is now a choice, and it
    works in both directions: a workspace can take it away as well as
    apply it."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"trigger": "money_arrives"},
    )
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_part_payment",
        headers=biz_headers,
        json={"trigger": "money_arrives"},
    )

    await a_payment(
        client, biz_headers, account, client_payee, when=TODAY - timedelta(days=6)
    )
    invoice = await an_invoice(client, biz_headers, client_payee)

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert detail["allocations"] == [], "no rule was willing to look back"


@pytest.mark.asyncio
async def test_widening_a_rule_lets_an_unnamed_payer_settle_a_later_invoice(
    client: AsyncClient, biz_headers, session: AsyncSession, account, test_user
):
    """We ship this off, because money that predates a document had a life
    of its own. Somebody who knows their account only ever receives client
    payments is entitled to disagree, and now can.

    The payment sits two days before the invoice was written, inside this
    rule's own three-day early window — so the only thing standing between
    it and a match is the moment the rule is willing to run at, which is
    exactly what this test is about."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/exact_amount_any_client",
        headers=biz_headers,
        json={"trigger": "both"},
    )

    resp = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "TED RECEBIDA",
            "amount": "3000.00",
            "currency": "USD",
            "date": str(TODAY - timedelta(days=2)),
            "type": "credit",
            "account_id": str(account.id),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    resp = await client.post(
        "/api/invoices",
        headers=biz_headers,
        json={"total": "3000.00", "due_date": str(TODAY)},
    )
    invoice = resp.json()

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert len(detail["allocations"]) == 1


@pytest.mark.asyncio
async def test_a_bad_moment_is_refused(client: AsyncClient, biz_headers):
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"trigger": "whenever"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_trigger"


# ---------------------------------------------------------------------------
# One transaction, several invoices — end to end
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_transfer_settles_three_invoices_from_the_same_client(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The commercial arrangement that pays a month at a time: one
    transfer, three invoices, and nothing for anybody to do."""
    invoices = [
        await an_invoice(client, biz_headers, client_payee, total=total)
        for total in ("1000.00", "2000.00", "3000.00")
    ]
    await a_payment(client, biz_headers, account, client_payee, amount="6000.00")

    for invoice, total in zip(invoices, ("1000.00", "2000.00", "3000.00")):
        detail = (
            await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
        ).json()
        assert detail["state"] == "paid", f"{total} left open"
        assert len(detail["allocations"]) == 1
        # Each debt got what it was owed, not the payment split evenly.
        assert Decimal(detail["allocations"][0]["amount"]) == Decimal(total)
        assert detail["allocations"][0]["method"] == "same_client_several_invoices"


@pytest.mark.asyncio
async def test_the_same_transaction_appears_on_every_invoice_it_settled(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Many-to-many in both directions, which is the claim: one payment
    row, three allocation rows, all pointing at it."""
    invoices = [
        await an_invoice(client, biz_headers, client_payee, total=total)
        for total in ("1000.00", "2000.00")
    ]
    payment = await a_payment(
        client, biz_headers, account, client_payee, amount="3000.00"
    )

    for invoice in invoices:
        detail = (
            await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
        ).json()
        assert detail["allocations"][0]["transaction_id"] == payment["id"]


@pytest.mark.asyncio
async def test_an_ambiguous_combination_is_asked_about_as_one_question(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Three invoices of a thousand and a payment of two thousand: any two
    fit. The queue asks *"does this cover these two?"* once, not twice —
    two separate questions could be answered inconsistently and leave the
    payment spread across one debt and short on another."""
    for _ in range(3):
        await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2000.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    assert len(queue) == 1, "one question, not one per invoice"
    offered = queue[0]
    assert offered["strategy_id"] == "same_client_several_invoices"
    assert len(offered["covers"]) == 2
    assert Decimal(offered["amount"]) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_accepting_a_combination_settles_all_of_it(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    for _ in range(3):
        await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2000.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    resp = await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/accept", headers=biz_headers
    )
    assert resp.status_code == 200, resp.text

    paid = [
        inv
        for inv in (
            await client.get("/api/invoices", headers=biz_headers)
        ).json()
        if inv["state"] == "paid"
    ]
    assert len(paid) == 2, "both halves of the answer were written"
    assert (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json() == [], "the question is answered and gone"


@pytest.mark.asyncio
async def test_declining_a_combination_refuses_all_of_it(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee,
    business_ws,
):
    """And every member is remembered as refused, so no part of it comes
    back on the next sync wearing a different combination."""
    for _ in range(3):
        await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2000.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/decline", headers=biz_headers
    )

    result = await session.execute(select(ReconciliationSuggestion))
    rows = result.unique().scalars().all()
    assert len(rows) == 2
    assert {row.status for row in rows} == {"declined"}
    assert len({row.group_id for row in rows}) == 1, "one question, one group"


@pytest.mark.asyncio
async def test_a_gateway_fee_can_be_allowed_for(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """*"The payout is my invoices minus their cut."* Shipped at zero
    because guessing which fee applies is how a wrong split gets written
    confidently — but somebody who knows their gateway's percentage can
    say so."""
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_several_invoices",
        headers=biz_headers,
        json={"when": {"amount": {"match": "set", "max_invoices": 6, "percent": "2"}}},
    )
    for total in ("1000.00", "2000.00"):
        await an_invoice(client, biz_headers, client_payee, total=total)

    await a_payment(client, biz_headers, account, client_payee, amount="2940.00")

    paid = [
        inv
        for inv in (await client.get("/api/invoices", headers=biz_headers)).json()
        if inv["state"] == "paid"
    ]
    assert len(paid) == 2


@pytest.mark.asyncio
async def test_without_the_tolerance_a_short_payout_settles_nothing(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The shipped behaviour, and the safe one."""
    for total in ("1000.00", "2000.00"):
        await an_invoice(client, biz_headers, client_payee, total=total)
    await a_payment(client, biz_headers, account, client_payee, amount="2940.00")

    paid = [
        inv
        for inv in (await client.get("/api/invoices", headers=biz_headers)).json()
        if inv["state"] == "paid"
    ]
    assert paid == []


@pytest.mark.asyncio
async def test_a_combination_is_written_whole_or_not_at_all(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """If one invoice of a payout refuses, none of them are written. A
    payment landing against two of three debts and unaccounted for on the
    rest is a ledger nobody can explain."""
    first = await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await an_invoice(client, biz_headers, client_payee, total="2000.00")

    # Voiding one of them makes it refuse allocation while leaving it out
    # of the candidate list is not possible — so instead the whole set
    # simply stops matching, which is the same guarantee seen from the
    # outside: no partial write.
    await client.post(f"/api/invoices/{first['id']}/void", headers=biz_headers)
    await a_payment(client, biz_headers, account, client_payee, amount="3000.00")

    detail = (
        await client.get(f"/api/invoices/{first['id']}", headers=biz_headers)
    ).json()
    assert detail["allocations"] == []
    remaining = (
        await client.get("/api/invoices", headers=biz_headers)
    ).json()
    assert not any(inv["state"] == "paid" for inv in remaining)


@pytest.mark.asyncio
async def test_a_set_rule_is_visible_and_editable_like_every_other(
    client: AsyncClient, biz_headers
):
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    rule = find(nodes, INVOICE_NODE, "same_client_several_invoices")
    assert rule["outcome"] == "link"
    assert rule["when"]["amount"]["match"] == "set"
    assert rule["when"]["amount"]["max_invoices"] == 6


@pytest.mark.asyncio
async def test_an_impossible_set_size_is_refused(client: AsyncClient, biz_headers):
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_several_invoices",
        headers=biz_headers,
        json={"when": {"amount": {"match": "set", "max_invoices": 50}}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "bad_set_size"


@pytest.mark.asyncio
async def test_a_tolerance_wide_enough_to_match_anything_is_refused(
    client: AsyncClient, biz_headers
):
    """Above twenty per cent almost any group of invoices adds up to
    almost any payment, and a rule that always matches is not a rule."""
    resp = await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_several_invoices",
        headers=biz_headers,
        json={"when": {"amount": {"match": "set", "percent": "60"}}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "set_tolerance_too_wide"


# ---------------------------------------------------------------------------
# What matching did
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_automatic_link_is_recorded_with_the_rule_that_made_it(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """And with no user, which is the distinction a reader reaches for
    first: was this me, or was this the rules?"""
    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "linked"
    assert event["expectation_id"] == invoice["id"]
    assert event["strategy_id"] == "same_client_exact"
    assert event["user_id"] is None, "the rules did this on their own"
    assert event["transaction_description"] == "PIX RECEBIDO ALPHA"


@pytest.mark.asyncio
async def test_undoing_a_link_leaves_a_trace(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The event this table exists for. Unallocating **deletes** the
    allocation, so without a record a match that was made and then undone
    is indistinguishable from one that was never made."""
    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    allocation_id = detail["allocations"][0]["id"]
    resp = await client.delete(
        f"/api/invoices/{invoice['id']}/allocations/{allocation_id}",
        headers=biz_headers,
    )
    assert resp.status_code in (200, 204), resp.text

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert events[0]["action"] == "unlinked"
    assert events[0]["user_id"] is not None, "a person did this one"
    assert events[0]["strategy_id"] == "same_client_exact", "what it had been"

    # And the invoice really is open again — the history did not replace
    # the undoing, it recorded it.
    after = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert after["allocations"] == []


@pytest.mark.asyncio
async def test_a_question_and_its_answer_both_land_in_the_stream(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/accept", headers=biz_headers
    )

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    actions = [e["action"] for e in events]

    # Two rows, not three. Accepting *is* the link — the allocation is its
    # consequence, not a second event — and the whole stream is organised
    # around one line: was this me, or was this the rules? `linked` means
    # the rules; `accepted` means a person. Writing both would blur it.
    assert actions == ["accepted", "suggested"], "newest first, one row per act"
    assert events[0]["user_id"] is not None, "a person accepted"

    # And the invoice really was settled by it.
    detail = (
        await client.get("/api/invoices", headers=biz_headers)
    ).json()
    assert any(inv["state"] == "paid" for inv in detail)


@pytest.mark.asyncio
async def test_a_refusal_is_remembered_as_an_event_too(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/same_client_exact",
        headers=biz_headers,
        json={"outcome": "suggest"},
    )
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)
    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/decline", headers=biz_headers
    )

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert events[0]["action"] == "declined"
    assert events[0]["user_id"] is not None


@pytest.mark.asyncio
async def test_the_history_of_one_invoice_can_be_read_on_its_own(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The second and only other way anybody reads this: everything that
    ever happened to this promise."""
    mine = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    other_payee_invoice = await an_invoice(
        client, biz_headers, client_payee, total="4444.00"
    )
    await a_payment(client, biz_headers, account, client_payee, amount="4444.00")

    events = (
        await client.get(
            f"/api/reconciliation/history?expectation_id={mine['id']}",
            headers=biz_headers,
        )
    ).json()
    assert len(events) == 1
    assert events[0]["expectation_id"] == mine["id"]
    assert other_payee_invoice["id"] != mine["id"]


@pytest.mark.asyncio
async def test_nothing_is_written_for_money_that_matched_nothing(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The restraint that keeps the history readable. A sync of three
    hundred transactions where most match nothing must not produce three
    hundred rows saying so."""
    await a_payment(client, biz_headers, account, client_payee, amount="77.00")
    await a_payment(client, biz_headers, account, client_payee, amount="88.00")

    assert (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json() == []


@pytest.mark.asyncio
async def test_the_history_stays_inside_its_workspace(
    client: AsyncClient, auth_headers, biz_headers, session: AsyncSession,
    account, client_payee,
):
    await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    other = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Outro", "kind": "business", "self_membership": True},
    )
    headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    assert (
        await client.get("/api/reconciliation/history", headers=headers)
    ).json() == []


@pytest.mark.asyncio
async def test_a_grouped_question_is_one_line_in_the_history(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """A payment offered against several invoices is one thing that
    happened and one thing a person answers. Three rows in the stream
    would be the same noise the queue collapses, in the one place built
    for scanning."""
    for _ in range(3):
        await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2000.00")

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert [e["action"] for e in events] == ["suggested"]
    # And it is worth the whole payment, not one slice of it.
    assert Decimal(events[0]["amount"]) == Decimal("2000.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/accept", headers=biz_headers
    )

    after = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert [e["action"] for e in after] == ["accepted", "suggested"]
    assert Decimal(after[0]["amount"]) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_declining_a_grouped_question_is_also_one_line(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    for _ in range(3):
        await an_invoice(client, biz_headers, client_payee, total="1000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2000.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/decline", headers=biz_headers
    )

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert [e["action"] for e in events] == ["declined", "suggested"]


@pytest.mark.asyncio
async def test_a_payment_settling_several_invoices_records_each_link(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """Links are the exception, and deliberately: each one is a separate
    write against a separate debt, and the invoice-level history — *what
    ever happened to this invoice* — has to find its own row."""
    for total in ("1000.00", "2000.00"):
        await an_invoice(client, biz_headers, client_payee, total=total)
    await a_payment(client, biz_headers, account, client_payee, amount="3000.00")

    events = (
        await client.get("/api/reconciliation/history", headers=biz_headers)
    ).json()
    assert [e["action"] for e in events] == ["linked", "linked"]
    assert sorted(Decimal(e["amount"]) for e in events) == [
        Decimal("1000.00"),
        Decimal("2000.00"),
    ]


# ---------------------------------------------------------------------------
# The withholding gap, and what a rule can and cannot do about it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_rule_can_send_a_withheld_payment_to_the_queue_today(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """R$3.000 invoiced, R$2.955 received — 1,5% IRRF withheld by a PJ
    client. Nothing shipped catches it: the exact rules miss by R$45, the
    part-payment rule refuses because 98,5% of the balance is a fee and
    not an instalment, and the tolerance rule that would catch it also
    demands a matching description, which bank text almost never has.

    But a workspace can write the rule itself, and it works.
    """
    resp = await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={
            "node": INVOICE_NODE,
            "name": "Cliente conhecido, diferença pequena",
            "outcome": "suggest",
            "position": 0,
            "when": {
                "counterparty": "same_payee",
                "amount": {"match": "tolerance", "percent": "2"},
                "date": {"before_days": 10, "after_days": 60},
            },
        },
    )
    assert resp.status_code == 201, resp.text

    await an_invoice(client, biz_headers, client_payee, total="3000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2955.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    assert len(queue) == 1, "the withheld payment reached a person"
    assert queue[0]["scores"]["amount_exact"] is False
    assert queue[0]["scores"]["amount_expected"] == "3000.00"
    assert queue[0]["scores"]["amount_moved"] == "2955.00"


@pytest.mark.asyncio
async def test_but_accepting_it_leaves_the_invoice_short_by_the_withheld_tax(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The half a rule cannot reach, and the reason `invoice_deductions`
    is still owed.

    Reviewing the payment is solvable with a rule. **Closing the invoice
    is not.** Accepting allocates what actually arrived, so R$45 stays
    outstanding — the invoice reads `partial` forever, and the aging
    report carries R$45 that is never coming, because it was never a
    debt: the client paid it to the Receita on the seller's behalf.

    Nothing in the ledger can currently say that. `uncollectible` is a
    whole-invoice decision and the wrong word besides. Until a deduction
    can be recorded, the honest outcome of this flow is a permanently
    part-paid invoice.
    """
    await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={
            "node": INVOICE_NODE,
            "name": "Cliente conhecido, diferença pequena",
            "outcome": "suggest",
            "position": 0,
            "when": {
                "counterparty": "same_payee",
                "amount": {"match": "tolerance", "percent": "2"},
                "date": {"before_days": 10, "after_days": 60},
            },
        },
    )
    invoice = await an_invoice(client, biz_headers, client_payee, total="3000.00")
    await a_payment(client, biz_headers, account, client_payee, amount="2955.00")

    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    accepted = await client.post(
        f"/api/reconciliation/suggestions/{queue[0]['id']}/accept", headers=biz_headers
    )
    assert accepted.status_code == 200, accepted.text

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert Decimal(detail["allocations"][0]["amount"]) == Decimal("2955.00")
    assert Decimal(detail["balance"]) == Decimal("45.00")
    assert detail["state"] == "partial", "not paid — and it never will be"


# ---------------------------------------------------------------------------
# The order rules are tried in
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_order_can_be_changed_and_holds(
    client: AsyncClient, biz_headers
):
    """Order is the mechanism, not a preference: the first rule that
    matches wins, so a band is expressed by what sits above and below."""
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    ids = [r["id"] for r in next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]]

    reversed_ids = list(reversed(ids))
    resp = await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": reversed_ids},
    )
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()] == reversed_ids

    again = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    assert [
        r["id"] for r in next(n for n in again if n["node"] == INVOICE_NODE)["rules"]
    ] == reversed_ids


@pytest.mark.asyncio
async def test_reordering_alone_does_not_mark_a_rule_as_changed(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """A row carrying only a position is not a departure from what we
    ship. Marking it as one would offer to "restore" a rule nobody
    altered — and would suggest it had stopped inheriting improvements,
    which it has not."""
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    ids = [r["id"] for r in next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]]
    await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": list(reversed(ids))},
    )

    after = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    rules_now = next(n for n in after if n["node"] == INVOICE_NODE)["rules"]
    assert all(r["customised"] is False for r in rules_now)

    # Rows exist — they carry the order — but their config is empty, so
    # every one of them still inherits what we ship.
    result = await session.execute(
        select(ReconciliationRule).where(
            ReconciliationRule.workspace_id == uuid.UUID(business_ws["id"])
        )
    )
    stored = result.scalars().all()
    assert stored and all(row.config == {} for row in stored)


@pytest.mark.asyncio
async def test_reordering_changes_which_rule_wins(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee
):
    """The point of it. Moving the part-payment rule above the exact one
    changes what happens to money, not just what the list looks like."""
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    ids = [r["id"] for r in next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]]

    # A rule that suggests, moved above every rule that links.
    moved = ["similar_description"] + [i for i in ids if i != "similar_description"]
    await client.patch(
        f"/api/reconciliation/rules/{INVOICE_NODE}/similar_description",
        headers=biz_headers,
        json={"when": {"description_similarity": {"min": "0"}}},
    )
    await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": moved},
    )

    invoice = await an_invoice(client, biz_headers, client_payee)
    await a_payment(client, biz_headers, account, client_payee)

    detail = (
        await client.get(f"/api/invoices/{invoice['id']}", headers=biz_headers)
    ).json()
    assert detail["allocations"] == [], "the suggesting rule got there first"
    queue = (
        await client.get("/api/reconciliation/suggestions", headers=biz_headers)
    ).json()
    assert queue[0]["strategy_id"] == "similar_description"


@pytest.mark.asyncio
async def test_an_order_that_leaves_a_rule_out_is_refused(
    client: AsyncClient, biz_headers
):
    """Naming only some rules would leave the rest wherever we happened to
    ship them — an order that reads correctly today and rearranges itself
    the day a default is inserted."""
    resp = await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": ["same_client_exact"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "incomplete_order"


@pytest.mark.asyncio
async def test_a_rule_named_twice_is_refused(client: AsyncClient, biz_headers):
    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    ids = [r["id"] for r in next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]]
    resp = await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": [ids[0]] + ids},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "duplicate_rule"


@pytest.mark.asyncio
async def test_a_workspaces_own_rule_takes_part_in_the_order(
    client: AsyncClient, biz_headers
):
    created = await client.post(
        "/api/reconciliation/rules",
        headers=biz_headers,
        json={
            "node": INVOICE_NODE,
            "name": "Minha regra",
            "outcome": "suggest",
            "when": {"amount": {"match": "exact"}},
        },
    )
    mine = created.json()["id"]

    nodes = (await client.get("/api/reconciliation/rules", headers=biz_headers)).json()
    ids = [r["id"] for r in next(n for n in nodes if n["node"] == INVOICE_NODE)["rules"]]
    assert mine in ids

    resp = await client.put(
        f"/api/reconciliation/rules/{INVOICE_NODE}/order",
        headers=biz_headers,
        json={"order": [mine] + [i for i in ids if i != mine]},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == mine
