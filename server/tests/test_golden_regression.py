"""Golden regression set, Oct 27, 2026 test run.

Every test here pins a failure the user pasted from a live 31-question
run, and every test goes through the LIVE path (invoke_tool / execute_chat),
because the Oct 26 CRM-payments "fix" passed its unit test while live chat
still failed: that test called the function directly and never met the
argument validator that kills dateless calls in invoke_tool.

The human-readable list with expected facts lives in GOLDEN_QUESTIONS.md
next to this file; these tests lock the tool-level behaviour so a prompt
or tool change cannot silently reintroduce a fixed class.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from app.agents.chat import (
    CHAT_SYSTEM,
    _answer_consistency_errors,
    execute_chat,
    validate_numeric_claims,
)
from app.agents.chat_tools import CHAT_TOOL_MAP, invoke_tool
from app.agents import chat_tools
from app.services import periods, processors
from app.services.customer360_client import Customer360Error
from test_chat import FakeModel, plain_answer
from test_chat_data_tools import Collection, business_db, lead, order, payment, quote

NOW = datetime.now(timezone.utc)


# ------------------------------------------------------- ERP payments, live path (Nov 2: ERP only)


@pytest.mark.asyncio
async def test_erp_payments_dateless_customer_scans_all_time():
    """Oct 27 (carried): a dateless customer call died in invoke_tool with
    'date_from and date_to are required' - the function-level fallback was
    unreachable behind the validator. Through invoke_tool it must now
    return the customer's full set, ERP only: no CRM leg, no gap."""
    db = business_db(payments_flat=Collection([payment(index=1, amount=1250.0)]))
    result = await invoke_tool("get_payments", {"customer": "Picazo"}, database=db)
    assert result["total_count"] == 1
    assert result["sum_amount"] == "$1,250"
    assert len(result["items"]) == 1
    assert result["has_more"] is False
    assert "crm" not in result and "ledger_note" not in result


@pytest.mark.asyncio
async def test_erp_payments_single_ledger_no_crm_keys():
    """Canyon Plastics, twice: the ERP leg alone, CRM 'not checked yet'.
    Nov 2: the CRM leg is gone by design - one call returns the ERP
    total only, never a second ledger to skip or reconcile."""
    db = business_db(payments_flat=Collection([payment(index=1, amount=1250.0)]))
    result = await invoke_tool("get_payments", {"customer": "Picazo"}, database=db)
    assert result["total_count"] == 1
    assert result["sum_amount"] == "$1,250"
    assert "crm" not in result
    assert "ledger_difference" not in result["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["all time", "all_time", "all"])
async def test_all_time_period_accepted_on_payments_tool(period):
    """Same Canyon failure mode: any spelling of all time must yield the
    full window, never a validation error the model has to work around."""
    db = business_db(payments_flat=Collection([payment(index=1, amount=1250.0)]))
    result = await invoke_tool(
        "get_payments", {"period": period, "customer": "Picazo"}, database=db
    )
    assert result["filters_applied"]["date_from"] == "2000-01-01"
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_erp_payments_today_is_the_erp_business_day():
    """Oct 27 contradiction: payments 'today' $0 vs revenue payments today
    12 - two tools defined 'today' differently. A named period resolves
    on the ERP business calendar, the revenue tile's day."""
    db = business_db()
    result = await invoke_tool("get_payments", {"period": "today"}, database=db)
    business_day = periods.business_today().isoformat()
    assert result["filters_applied"]["date_from"] == business_day
    assert result["filters_applied"]["date_to"] == business_day


# ------------------------------------------------------- ERP payments pagination (Oct 31, ERP-only Nov 2)


@pytest.mark.asyncio
async def test_erp_payments_small_set_fits_one_page():
    """Oct 31 transcript: 'fetch next page' fetched pages 2 AND 3 and the
    answer narrated offset/limit/has_more instead of listing a payment.
    A set that fits one page comes back whole, and the summary states
    the ERP total first."""
    db = business_db(payments_flat=Collection([
        payment(index=1, amount=501.0),
        payment(index=2, amount=502.0),
        payment(index=3, amount=503.0),
    ]))
    result = await invoke_tool("get_payments", {"customer": "Picazo"}, database=db)
    assert result["summary"]["next_offset"] is None
    assert result["total_count"] == 3
    assert len(result["items"]) == 3
    assert result["has_more"] is False
    assert result["summary"]["total"] == "$1,506"
    assert result["summary"]["count"] == 3
    assert "ledger_difference" not in result["summary"]


@pytest.mark.asyncio
async def test_erp_payments_large_set_still_pages_one_call():
    """Larger sets page, one call per page, and the page block names its
    page - the model pages with one call instead of one call per ledger."""
    db = business_db(payments_flat=Collection(
        [payment(index=i, amount=100.0) for i in range(1, 56)]
    ))
    result = await invoke_tool("get_payments", {"customer": "Picazo"}, database=db)
    assert result["summary"]["next_offset"] == 10
    assert len(result["items"]) == 10
    assert result["total_count"] == 55
    assert result["has_more"] is True
    assert result["page"] == 1
    assert result["pages_total"] == 6


@pytest.mark.asyncio
async def test_erp_payments_long_names_still_page_honestly():
    """Very long customer names must page honestly rather than collapse
    into a truncated preview: the first page names its cursor."""
    long_name = "Picazo " + "P" * 400
    db = business_db(payments_flat=Collection([
        payment(index=i, amount=100.0, customer={"id": 6079, "company_name": long_name})
        for i in range(1, 26)
    ]))
    result = await invoke_tool("get_payments", {"customer": "Picazo"}, database=db)
    assert result["has_more"] is True
    assert result["page"] == 1
    assert result["summary"]["next_offset"] == 10


# ------------------------------------------------------- stuck leads next_action


def test_lead_next_action_names_the_missing_field():
    assert processors.lead_next_action(["material"], "Orthoza") == (
        "Ask Orthoza for the missing material."
    )
    assert processors.lead_next_action(["quantity", "size"], None).startswith(
        "Ask the customer for the missing quantity."
    )
    assert processors.lead_next_action([]) == (
        "Confirm the order details and convert the lead to an order."
    )


@pytest.mark.asyncio
async def test_stuck_leads_carry_next_action():
    """Orthoza: the tool must hand the model the exact question, so the
    model copies it instead of recommending the missing material."""
    orthoza = lead(
        firstName="Orthoza",
        lastName="",
        company="",
        status="New",
        description=(
            "product: Vinyl Labels\nquantity: 10000\nsize: 3 x 3 inch\n"
            "finish: gloss_laminated"
        ),
        email="orthoza@example.com",
        lastActivityAt=NOW.isoformat(),
        createdAt=NOW.isoformat(),
    )
    db = business_db(leads=Collection([orthoza]))
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["missing"] == ["material"]
    assert item["next_action"] == "Ask Orthoza for the missing material."


def _valerie_lead():
    """The live Oct 27 late 'top lead' answer: every spec parsed, artwork
    unstated. The old fallback called it ready to convert, and the model
    filled the gap with invented 'typical blockers' plus a draft-email
    offer. The tool must instead name the one open item."""
    return lead(
        firstName="Valerie",
        lastName="Eskelsen",
        company="Balaika",
        status="New",
        description=(
            '{"product": "Custom Quote", "quantity": "15000", '
            '"size": "5 x 2 inch", "material": "Foil", '
            '"finish": "Softouch Lamination, Spot Gloss"}'
        ),
        email="valerie@example.com",
        lastActivityAt=(NOW - timedelta(days=7)).isoformat(),
        createdAt=(NOW - timedelta(days=30)).isoformat(),
    )


@pytest.mark.asyncio
async def test_stuck_lead_with_specs_but_artwork_unstated_asks_artwork():
    db = business_db(leads=Collection([_valerie_lead()]))
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["missing"] == []
    assert item["artwork"] == "Not stated"
    assert item["next_action"] == "Ask Valerie Eskelsen to confirm the artwork."
    assert "artwork not confirmed" in item["conversion_reason"]
    assert "typical" not in item["conversion_reason"]


@pytest.mark.asyncio
async def test_stuck_lead_ready_to_convert_says_so_plainly():
    ready = _valerie_lead()
    ready["description"] = (
        '{"product": "Custom Quote", "quantity": "15000", '
        '"size": "5 x 2 inch", "material": "Foil", '
        '"finish": "Softouch Lamination, Spot Gloss", "artwork": ["proof.pdf"]}'
    )
    db = business_db(leads=Collection([ready]))
    result = await invoke_tool("get_stuck_leads", {}, database=db)
    item = result["items"][0]
    assert item["artwork"] == "Provided"
    assert item["next_action"] == (
        "Confirm the order details and convert the lead to an order."
    )
    assert item["conversion_reason"].startswith("Nothing missing")


@pytest.mark.asyncio
async def test_get_leads_carries_artwork_and_artwork_question():
    """get_leads keeps the artwork state and the corrected next_action,
    but deliberately carries NO conversion_reason: its pages must match
    get_stuck_leads row-for-row, and the ~10,500-char response cap trims
    rows off the page when one more sentence per row is added (caught
    by test_both_stuck_lead_views_page_the_same_sequence)."""
    db = business_db(leads=Collection([_valerie_lead()]))
    result = await invoke_tool(
        "get_leads", {"stuck_only": True}, database=db
    )
    assert result["total_count"] == 1
    item = result["items"][0]
    assert item["artwork"] == "Not stated"
    assert item["next_action"] == "Ask Valerie Eskelsen to confirm the artwork."
    assert "conversion_reason" not in item


# ------------------------------------------------------- status words


@pytest.mark.asyncio
async def test_unpaid_and_open_are_not_label_substrings():
    """Oct 27: 'unpaid invoices' 0 while overdue balances existed, and
    'open quotes' 0 while 365 stale quotes existed - no ERP label
    contains those words."""
    db = business_db(
        orders=[order(payment_status_label="Not Paid", total_amount=4000.0, amount_paid=0.0)],
        quotes=[quote(status="Sent", amount=1000.0)],
    )
    invoices = await invoke_tool("get_invoices", {"status": "unpaid"}, database=db)
    assert invoices["total_count"] >= 1
    quotes_result = await invoke_tool("get_quotes", {"status": "open"}, database=db)
    assert quotes_result["total_count"] >= 1


# ------------------------------------------------------- guard: list ids, recall dates


def test_guard_accepts_a_list_leading_record_id():
    """Oct 27: the stale-quotes list died on every quote id ('- 20550: ...')."""
    result = {
        "tool": "get_quotes",
        "result": {
            "items": [{"quote_id": 20550, "amount": 348000}],
            "total_count": 1, "returned": 1, "has_more": False,
        },
    }
    assert validate_numeric_claims("- 20550: Jasper United LLC, $348,000", [result]) == []
    # a fabricated id still fails
    assert validate_numeric_claims("- 99999: Nobody, $348,000", [result])


@pytest.mark.asyncio
async def test_history_answer_survives_its_own_dates():
    """Oct 27: 'what did we discuss last time' collapsed to 'couldn't
    verify' because recall results were excluded from the guard's
    evidence, so their own updated_at dates failed validation."""
    doc = {
        "thread_id": "thread-1",
        "title": "Liza John january sales 2026 report",
        "updated_at": "2026-10-26 22:57:18",
        "turns": [{"question": "Liza John january sales 2026 report", "answer": "Quotes: 41"}],
    }
    db = business_db(chat_threads=Collection([doc]))
    model = FakeModel([
        AIMessage(
            content="",
            tool_calls=[{"name": "recall_conversations", "args": {}, "id": "call-1", "type": "tool_call"}],
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        ),
        plain_answer(
            "- Liza John january sales 2026 report (updated 2026-10-26 22:57:18) — 1 turn"
        ),
    ])
    outcome = await execute_chat(
        "What did we discuss last time?", model=model, database=db
    )
    assert outcome["validation_replaced"] is False
    assert "Liza John january sales 2026 report" in outcome["text"]
    assert "2026-10-26 22:57:18" in outcome["text"]


def _crm_client_for_analyses():
    class FakeCRM:
        async def fetch_analysis(self, record_id, record_type):
            return {
                "success": True,
                "apiKeyStatus": "Verified",
                "analysis": {"urgency": "High", "record": record_id},
            }

    return FakeCRM()


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: _crm_client_for_analyses())
    return chat_tools


@pytest.mark.asyncio
async def test_both_analyses_return_in_one_call(crm):
    """Oct 27: 'Frank Jr ke dono records ki analysis do' failed as a
    timeout - two sequential slow CRM calls. The parallel tool returns
    both, and one failure must not take the other down."""
    result = await invoke_tool(
        "get_customer360_analyses",
        {"records": [
            {"record_id": "aaa", "record_type": "Lead"},
            {"record_id": "bbb", "record_type": "Contact"},
        ]},
        database=business_db(),
    )
    assert len(result["analyses"]) == 2
    assert all(item["available"] is True for item in result["analyses"])
    assert result["analyses"][0]["analysis"]["record"] == "aaa"
    assert result["analyses"][1]["analysis"]["record"] == "bbb"


# -------------------------------------------- live Nov 2 failures, pinned


def _tool_message(index: int, name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )


class _TimeoutCRM:
    """The Nov 2 CRM path: search answers from the local index only (the
    live call times out) and nothing in chat can retry it into a ledger."""

    async def search(self, query):
        raise Customer360Error("network error: ReadTimeout")


@pytest.mark.asyncio
async def test_crm_named_payments_question_is_steered_back_to_the_erp_ledger(monkeypatch):
    """Nov 2 (P4): 'Show me TREATDAY's CRM payments' went down the CRM path,
    timed out, and ended as a bare choice question with no ERP figure. The
    routing nudges must send that turn back to the tools, and the answer
    that ships must carry the ERP total."""
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: _TimeoutCRM())
    db = business_db(payments_flat=Collection([
        payment(index=index, amount=1250.0,
                customer={"id": 691, "company_name": "TREATDAY LLC"})
        for index in (1, 2)
    ]))
    model = FakeModel([
        _tool_message(1, "search_customer360", {"query": "TREATDAY LLC"}),
        plain_answer(
            "The CRM fetch timed out. Pull ERP payments for TREATDAY LLC, "
            "or retry the CRM fetch?"
        ),
        _tool_message(2, "get_payments", {"customer": "TREATDAY"}),
        plain_answer("TREATDAY LLC paid $2,500 across 2 payments (all time)."),
    ])
    outcome = await execute_chat("Show me TREATDAY's CRM payments", model=model, database=db)
    assert outcome["validation_replaced"] is False
    assert "$2,500" in outcome["text"]
    assert "retry" not in outcome["text"].lower()
    # the backend directive reached the model before the ERP call
    final_prompt = " ".join(
        str(getattr(message, "content", "")) for message in model.seen_messages[-1]
    )
    assert "answered from the ERP ledger only" in final_prompt


class _FrankJrCRM:
    """The Nov 2 (C) path: one name, two exact matches - the Lead and the
    Contact - and a CRM AI analysis that answers in seconds."""

    async def search(self, query):
        return [
            {"id": "aaa111ccc222ddd333eee444", "type": "Lead", "name": "Frank Jr",
             "email": "marcellecaples@yahoo.com", "phone": "12143923587",
             "company": "Cape,s", "status": "New"},
            {"id": "bbb111ccc222ddd333eee555", "type": "Contact", "name": "Frank Jr",
             "email": "marcellecaples@yahoo.com", "phone": "2143923587",
             "company": "", "status": "Contact"},
        ]

    async def fetch_analysis(self, record_id, record_type):
        return {
            "success": True,
            "apiKeyStatus": "Verified",
            "analysis": {
                "followUpRequired": True,
                "urgency": "High",
                "reasoning": f"Follow up with this {record_type}.",
                "actionNeeded": "Call the customer.",
                "emailDraft": {"subject": "Following up", "body": "Hi Frank,"},
            },
        }


@pytest.mark.asyncio
async def test_analysis_question_ending_in_lead_or_contact_is_analyzed_for_both(monkeypatch):
    """Nov 2 (C): 'Find Frank Jr ... give me the CRM AI analysis' stopped at
    'Which should I run the analysis for - the Lead or the Contact?'. The
    nudges must force the parallel analysis of both records into one answer."""
    monkeypatch.setattr(chat_tools, "_c360_client", lambda: _FrankJrCRM())
    model = FakeModel([
        _tool_message(1, "search_customer360", {"query": "Frank Jr"}),
        plain_answer(
            "I found two exact matches. Which should I run the CRM AI analysis "
            "for - the Lead or the Contact?"
        ),
        _tool_message(2, "get_customer360_analyses", {"records": [
            {"record_id": "aaa111ccc222ddd333eee444", "record_type": "Lead"},
            {"record_id": "bbb111ccc222ddd333eee555", "record_type": "Contact"},
        ]}),
        plain_answer(
            "CRM AI analysis for the Lead (Verified): urgency High - call the customer. "
            "CRM AI analysis for the Contact (Verified): urgency High - call the customer."
        ),
    ])
    outcome = await execute_chat(
        "Find Frank Jr, open his record, and give me the CRM AI analysis",
        model=model, database=business_db(),
    )
    assert outcome["validation_replaced"] is False
    assert "Lead" in outcome["text"] and "Contact" in outcome["text"]
    assert "Which should I" not in outcome["text"]
    final_prompt = " ".join(
        str(getattr(message, "content", "")) for message in model.seen_messages[-1]
    )
    assert "get_customer360_analyses ONCE" in final_prompt


@pytest.mark.asyncio
async def test_a_trimmed_page_reports_the_row_size_it_really_carried():
    """Nov 2 (Q2 header): the answer read "page 1 of 8; limit 50" - pages
    computed over the 26 trimmed rows quoted against the 50 that were asked
    for. The page block must carry the effective page size and one copyable
    display line so page count, page size and returned rows agree."""
    rows = [payment(index=index, payment_ref=f"REF-{index}-" + "p" * 400)
            for index in range(1, 31)]
    db = business_db(payments_flat=Collection(rows))

    page = await invoke_tool("get_payments", {"limit": 30}, database=db)

    assert page["total_count"] == 30
    assert page["returned"] < 30 and page["has_more"] is True
    delivered = page["returned"]
    assert page["page_size"] == delivered
    assert page["pages_total"] == -(-30 // delivered)
    assert page["page_display"] == (
        f"{delivered} shown of 30 (page 1 of {page['pages_total']}, {delivered} rows per page)"
    )


def test_stuck_lead_answer_may_not_ship_placeholders_or_example_specs():
    """Nov 2 (Q6): the why-answer shipped a draft "Hi {Name}" plus invented
    example specs for missing fields ("e.g. foil/film/paper", "e.g. gloss,
    matte"). The consistency guard rejects both; a draft quoting the
    record's own name and values passes."""
    lead_results = [{
        "tool": "get_stuck_leads",
        "result": {
            "items": [{
                "lead_id": "L-1",
                "contact_name": "Rashad",
                "missing": ["quantity"],
                "next_action": "Ask Rashad for the missing quantity.",
            }],
            "total_count": 1, "returned": 1, "has_more": False,
        },
    }]
    draft = (
        "Draft message to send (sign as Print247 Team): Hi {Name}, please confirm "
        "the material (e.g. foil/film/paper), size, finish (e.g. gloss, matte). "
        "Ask Rashad for the missing quantity."
    )
    errors = _answer_consistency_errors(draft, lead_results, None)
    assert any("placeholder" in error for error in errors), errors
    assert any("example specs" in error for error in errors), errors

    grounded = (
        "Draft message (sign as Print247 Team): Hi Rashad, the record lists the "
        "product mylar bag - please confirm the quantity. "
        "Ask Rashad for the missing quantity."
    )
    assert _answer_consistency_errors(grounded, lead_results, None) == []


# ------------------------------------------- briefing/queue, live Nov 2

class _RunsCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, _key, _direction=-1):
        self._rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
        return self

    async def to_list(self, limit):
        return self._rows[:limit]


class _Runs:
    """agent_runs fake ordered by started_at, newest first - the shape the
    real Mongo sort returns, so run selection is tested, not faked away."""

    def __init__(self, docs):
        self._docs = list(docs)

    async def find_one(self, _query, sort=None):
        rows = sorted(self._docs, key=lambda d: str(d.get("started_at") or ""), reverse=True)
        return rows[0] if rows else None

    def find(self, _query=None, _projection=None):
        return _RunsCursor(self._docs)


class _RunsDb:
    def __init__(self, docs):
        self.agent_runs = _Runs(docs)


FINISHED_RUN = {
    "run_id": "run-finished",
    "started_at": "2026-11-01T06:00:00+00:00",
    "finished_at": "2026-11-01T06:05:00+00:00",
    "finished_epoch": 1793513100.0,
    "master": {"bullets": ["Revenue is up"], "priorities": [], "conflicts_resolved": []},
    "negotiation": [], "collections": [], "lead_followup": [],
    "crm_engagement": [
        {"contact_name": "Wa Person", "company": "WA Co", "channel": "WhatsApp",
         "waiting_days": 2, "last_customer_message": "price please"},
    ],
    "crm_deal_watch": [],
}
RUNNING_STUB = {
    "run_id": "run-running",
    "started_at": "2026-11-02T06:00:00+00:00",
    "started_epoch": 1793600000.0,
    "status": "running",
}
FAILED_STUB = {
    "run_id": "run-failed",
    "started_at": "2026-11-02T07:00:00+00:00",
    "finished_at": "2026-11-02T07:00:30+00:00",
    "status": "failed",
    "errors": ["RuntimeError: boom"],
}


@pytest.mark.asyncio
async def test_queue_skips_a_running_stub_and_reads_the_finished_run():
    """Nov 2: the newest doc was a running stub with no sections, and the
    queue answered as if nobody was waiting. The finished run's items are
    the queue; the stub is only reported as newer_run_status."""
    result = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([FINISHED_RUN, RUNNING_STUB]))
    assert result["available"] is True
    assert result["waiting_total"] == 1
    assert result["queue"][0]["name"] == "Wa Person"
    assert result["as_of"] == FINISHED_RUN["started_at"]
    assert result["newer_run_status"] == "running"


@pytest.mark.asyncio
async def test_queue_reports_no_finished_run_instead_of_a_false_zero():
    """With only stubs there is no queue at all: waiting_total is None (not
    0) and the note carries the plain no-finished-run sentence."""
    running = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([RUNNING_STUB]))
    assert running["available"] is False
    assert running["reason"] == "no_finished_run"
    assert running["waiting_total"] is None
    assert "in progress" in running["note"]
    assert running["latest_run_status"] == "running"

    failed = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([FAILED_STUB]))
    assert failed["available"] is False
    assert failed["reason"] == "no_finished_run"
    assert "No finished agent run" in failed["note"]
    assert failed["latest_run_status"] == "failed"


@pytest.mark.asyncio
async def test_digest_marks_run_blocks_unavailable_without_a_finished_run():
    """Per-block honesty: payments are computed live and stay present/empty;
    waiting and deals come from a run and are named unavailable - never a
    zero that the model could report as fact."""
    digest = await invoke_tool("get_crm_morning_briefing", {}, database=_RunsDb([RUNNING_STUB]))
    assert digest["run_status"] == "no_finished_run"
    assert digest["waiting"]["status"] == "unavailable"
    assert digest["stalled_deals"]["status"] == "unavailable"
    assert digest["payments"]["status"] == "empty"
    assert "No finished agent run" in digest["note"]

    full = await invoke_tool("get_crm_morning_briefing", {}, database=_RunsDb([FINISHED_RUN]))
    assert full["run_status"] == "finished"
    assert full["waiting"]["status"] == "present"
    assert full["stalled_deals"]["status"] == "empty"
    assert full["waiting"]["total"] == 1


@pytest.mark.asyncio
async def test_latest_briefing_skips_sectionless_run_docs():
    stubbed = await invoke_tool("get_latest_briefing", {}, database=_RunsDb([RUNNING_STUB]))
    assert stubbed["available"] is False
    assert stubbed["reason"] == "no_finished_run"
    assert "Run agents button" in stubbed["message"]

    saved = await invoke_tool("get_latest_briefing", {}, database=_RunsDb([FINISHED_RUN, RUNNING_STUB]))
    assert saved["available"] is True
    assert saved["run_id"] == "run-finished"
    assert saved["briefing"] == ["Revenue is up"]
    assert saved["newer_run_status"] == "running"


@pytest.mark.asyncio
async def test_briefing_route_never_serves_a_sectionless_stub():
    """The live Nov 2 client crash: approval-inbox read briefing.negotiation
    on the running-stub doc /api/briefing/latest served as "latest". The
    route must hand the newest finished run (sections included), and the
    no-run message shape when none exists."""
    from app.api import routes

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(routes, "get_db", lambda: _RunsDb([FINISHED_RUN, RUNNING_STUB]))
        doc = await routes.latest_briefing()
        assert doc["run_id"] == "run-finished"
        assert doc["negotiation"] == []
        assert doc["newer_run_status"] == "running"

        monkeypatch.setattr(routes, "get_db", lambda: _RunsDb([RUNNING_STUB]))
        doc = await routes.latest_briefing()
        assert doc["run_id"] is None
        assert "no agent run yet" in doc["message"]
        assert doc["latest_run_status"] == "running"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_briefing_question_with_no_finished_run_gets_the_plain_sentence():
    """The live Nov 2 turn: 'What is today's briefing? Show me the CRM
    follow-up queue' against a running stub ended in the generic
    couldn't-verify fallback. Both tools answer with the no-finished-run
    state and the model's plain sentence (no figures but the 6 from the
    tool's own note) must pass the guard."""
    model = FakeModel([
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_crm_morning_briefing", "args": {}, "id": "call-1", "type": "tool_call"},
                {"name": "get_crm_follow_up_queue", "args": {}, "id": "call-2", "type": "tool_call"},
            ],
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        ),
        plain_answer(
            "No finished agent run yet - the briefing needs a run first. "
            "Start one from the Run agents button (or wait for the 6 AM daily run). "
            "A run is in progress right now, so the queue will fill when it finishes."
        ),
    ])
    outcome = await execute_chat(
        "What is today's briefing? Show me the CRM follow-up queue",
        model=model, database=_RunsDb([RUNNING_STUB]),
    )
    assert outcome["validation_replaced"] is False
    assert "No finished agent run yet" in outcome["text"]
    assert "couldn't verify" not in outcome["text"]


# The exact live Nov 2 shape: mark_stale_runs_abandoned rewrites the running
# stub with status "abandoned" AND a finished_at stamp, but no sections -
# and a saved run carries no status field at all.

ABANDONED_STUB = {
    "run_id": "run-abandoned",
    "started_at": "2026-11-02T18:02:14+00:00",
    "finished_at": "2026-11-02T18:10:00+00:00",
    "status": "abandoned",
    "errors": ["this run was still marked running when the API started"],
}


@pytest.mark.asyncio
async def test_abandoned_stub_with_a_finished_stamp_is_not_a_briefing():
    result = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([FINISHED_RUN, ABANDONED_STUB]))
    assert result["available"] is True
    assert result["waiting_total"] == 1
    assert result["newer_run_status"] == "abandoned"

    alone = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([ABANDONED_STUB]))
    assert alone["available"] is False
    assert alone["reason"] == "no_finished_run"

    digest = await invoke_tool("get_crm_morning_briefing", {}, database=_RunsDb([ABANDONED_STUB]))
    assert digest["run_status"] == "no_finished_run"
    assert digest["waiting"]["status"] == "unavailable"


# ------------------------------------------- HTML snippet soup, live Nov 2
#
# The stored crm_engagement snippets are 200-char fragments of raw email
# HTML cut mid-tag (the pipeline truncated before stripping). Autumn's
# fragment keeps its greeting after the tags; Lance's is nothing but
# opening tags - no readable words at all.

AUTUMN_FRAGMENT = (
    '<meta content="text/html;charset=UTF-8"><div><div style="font-family: '
    'Verdana, Arial, Helvetica, sans-serif; font-size: 10pt"><p style="margin-'
    'top:0;margin-bottom:0"><span><b>Hi Autumn,</b></span><br></p'
)
ALL_TAGS_FRAGMENT = (
    '<meta content="text/html;charset=UTF-8"><div><div style="font-family:'
    ' Verdana"><p><span><br></span'
)


def test_readable_text_strips_before_it_truncates():
    from app.services import processors

    # entities decoded, whitespace collapsed, tags gone
    assert processors.readable_text("<p>Tom &amp; Jerry&nbsp; 2025</p>") == "Tom & Jerry 2025"
    # a mid-tag fragment keeps its words and loses the dangling markup
    assert processors.readable_text(AUTUMN_FRAGMENT) == "Hi Autumn,"
    # a fragment of nothing but tags yields no text (never raw markup)
    assert processors.readable_text(ALL_TAGS_FRAGMENT) == ""
    # truncation runs AFTER stripping: 500 words of body come back as 200
    # readable chars, never a run of opening tags
    body = "<div><p>" + ("proof " * 100) + "</p></div>"
    snippet = processors.readable_text(body, 200)
    assert "<" not in snippet and len(snippet) == 200 and snippet.startswith("proof")
    # already-clean text is untouched
    assert processors.readable_text("Can you send the proofs today?") == (
        "Can you send the proofs today?"
    )


def test_readable_snippet_names_the_unsalvageable_fragment():
    from app.services import processors

    assert processors.readable_snippet(AUTUMN_FRAGMENT) == "Hi Autumn,"
    assert processors.readable_snippet(ALL_TAGS_FRAGMENT) == processors.SNIPPET_NO_READABLE_TEXT
    assert processors.readable_snippet(None) is None
    assert processors.readable_snippet("   ") is None


def _fragment_run():
    return {
        "run_id": "run-fragments",
        "started_at": "2026-11-02T17:50:00+00:00",
        "finished_at": "2026-11-02T17:55:00+00:00",
        "finished_epoch": 1793642100.0,
        "master": {"bullets": [], "priorities": [], "conflicts_resolved": []},
        "negotiation": [], "collections": [], "lead_followup": [],
        "crm_engagement": [
            {"contact_name": "Autumn Geier", "company": "A Co", "channel": "Email",
             "waiting_days": 3, "last_customer_message": AUTUMN_FRAGMENT},
            {"contact_name": "Lance James Customer", "company": "L Co", "channel": "Email",
             "waiting_days": 2, "last_customer_message": ALL_TAGS_FRAGMENT},
            {"contact_name": "Stephanie Fields", "company": "S Co", "channel": "WhatsApp",
             "waiting_days": 1, "last_customer_message": "any update on the proofs?"},
        ],
        "crm_deal_watch": [],
    }


@pytest.mark.asyncio
async def test_queue_snippets_never_carry_raw_tags_to_the_model():
    """The stored fragments are defended at the chat-tool layer, so the
    current run answers with readable words or the honest placeholder -
    no '<' markup in any snippet field."""
    import json as _json

    result = await invoke_tool("get_crm_follow_up_queue", {}, database=_RunsDb([_fragment_run()]))
    assert "<" not in _json.dumps(result)
    by_name = {row["name"]: row["last_customer_message"] for row in result["queue"]}
    assert by_name["Autumn Geier"] == "Hi Autumn,"
    assert by_name["Lance James Customer"] == (
        "(message body has no readable text in the stored snippet)"
    )
    assert by_name["Stephanie Fields"] == "any update on the proofs?"


@pytest.mark.asyncio
async def test_briefing_waiting_snippets_are_defended_too():
    import json as _json

    digest = await invoke_tool("get_crm_morning_briefing", {}, database=_RunsDb([_fragment_run()]))
    assert "<" not in _json.dumps(digest["waiting"])
    messages = {row["name"]: row["last_customer_message"] for row in digest["waiting"]["top"]}
    assert messages["Autumn Geier"] == "Hi Autumn,"
    assert messages["Lance James Customer"] is not None and "<" not in messages["Lance James Customer"]


def test_conversation_formatter_strips_before_the_600_char_cut():
    """The shared summarizer the queue reads from: a long HTML email body
    lands as readable words, never a mid-tag fragment."""
    from app.services.customer360 import _conversation

    body = '<div style="font-family: Verdana"><p>' + ("Please review the artwork. " * 40) + "</p>"
    (entry,) = _conversation([{"type": "Email", "direction": "Inbound", "content": body}])
    assert "<" not in entry["content"]
    assert entry["content"].startswith("Please review the artwork.")
    assert len(entry["content"]) == 600


# --------------------------------- Issue A (Nov 2 stuck queue): server proof


@pytest.mark.asyncio
async def test_stream_chat_ends_with_done_on_the_queue_tool_path(monkeypatch):
    """The stuck-queue investigation asked whether ANY server path can end a
    delivered answer without a terminal frame - the queue-tool-only /
    validation-passed path included. It cannot: the stream always closes with
    a done event (or an error), so the dropped terminal transition is not
    server-side."""
    from app.agents.chat import stream_chat

    class _NoEvents:
        async def emit(self, **_kwargs):
            return None

    monkeypatch.setattr("app.agents.chat.get_event_service", lambda: _NoEvents())
    db = business_db(agent_runs=Collection([dict(FINISHED_RUN)]))
    model = FakeModel([
        _tool_message(1, "get_crm_follow_up_queue", {}),
        plain_answer("The queue is from the latest finished run."),
    ])
    events = [
        event
        async for event in stream_chat(
            "thread-1", "Show me the CRM follow-up queue", database=db, model=model
        )
    ]
    names = [event["event"] for event in events]
    assert "task" in names and "tool" in names
    assert names[-1] == "done", names
    assert events[-1]["data"]["text"] == "The queue is from the latest finished run."
    assert events[-1]["data"]["task_id"]


# --------------------------------------- Issue B (Nov 2): queue is not an inbox


def test_follow_up_queue_description_is_not_a_message_inbox():
    description = CHAT_TOOL_MAP["get_crm_follow_up_queue"].description
    assert "waiting queue, NOT a message inbox" in description
    assert "cannot fetch, list or show" in description
    assert "never be used for a 'show/fetch all messages'" in description


def test_prompt_routes_cross_contact_message_requests_to_the_whatsapp_index():
    """Nov 2 follow-up: the honest-limit answer was right for the tools that
    existed; the WhatsApp index now IS the source, and the prompt routes
    message questions to it - never to the follow-up queue."""
    prompt = " ".join(CHAT_SYSTEM.split())
    assert '"Fetch/show all latest WhatsApp messages" -> call get_whatsapp_latest_messages' in prompt
    assert '"rank by message count" -> call get_whatsapp_top_contacts' in prompt
    assert 'The follow-up queue is NOT a message source' in prompt
    assert "never present index data as live CRM data" in prompt
    assert "the CRM tracks Leads, Contacts and Customers" in prompt
    assert 'The queue answers "who is waiting", never "what messages exist"' in prompt


@pytest.mark.asyncio
async def test_latest_whatsapp_messages_answer_is_grounded_and_passes_the_guard():
    """End-to-end through execute_chat: the WhatsApp index tool's own rows and
    coverage numbers pass the numeric guard untouched - every figure comes
    from the tool, and the queue is nowhere near the answer."""
    db = business_db()
    db.customer360_whatsapp = Collection([
        {"_id": "Lead:wa-1", "record_id": "wa-1", "record_type": "Lead",
         "name": "Autumn Geier", "company": "A Co", "whatsapp_count": 4,
         "latest_text": "Hi Autumn, please send the proofs",
         "latest_at": "2026-11-02T10:00:00+00:00", "latest_direction": "Inbound",
         "indexed_at": "2026-11-02T11:00:00+00:00"},
    ])
    db.customer360_leads = Collection([{"_id": "wa-1", "firstName": "Autumn", "lastName": "Geier"}])
    db.customer360_contacts = Collection([])
    db.customer360_customers = Collection([])
    model = FakeModel([
        _tool_message(1, "get_whatsapp_latest_messages", {}),
        plain_answer(
            "Autumn Geier (Lead, A Co): latest WhatsApp message "
            "2026-11-02T10:00:00+00:00 - 'Hi Autumn, please send the proofs'. "
            "Indexed 1 of 1 person records."
        ),
    ])
    outcome = await execute_chat(
        "fetch all latest WhatsApp messages for all customers",
        model=model, database=db,
    )
    assert outcome["validation_replaced"] is False
    assert "couldn't verify" not in outcome["text"]
    assert "Autumn Geier" in outcome["text"]
