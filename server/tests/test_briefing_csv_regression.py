"""Regression tests for:
1. Issue 1: Combined Q10 morning briefing date validation across timezones and older runs
2. Issue 2: CSV file delivery, download links, and offer wording consistency
3. Issue 3: Filtered-list single-turn guarantee (no duplicate answers/done frames)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import pytest
from langchain_core.messages import AIMessage

from app.agents.chat import (
    _answer_consistency_errors,
    _evidence_summary,
    _format_tool_csv,
    _format_tool_table,
    execute_chat,
    run_chat,
    stream_chat,
    validate_numeric_claims,
)
from app.agents.chat_tools import invoke_tool
from app.services import periods, processors
from test_chat import FakeCollection, FakeDatabase, FakeModel, plain_answer
from test_chat_data_tools import Collection, business_db


def _tool_message(index: int, name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call-{index}", "type": "tool_call"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


# ---------------------------------------------------------------------------
# ISSUE 1: Combined Q10 Morning Briefing & Reference Frames
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combined_q10_exact_conditions_passes_without_fallback(monkeypatch):
    """(i) Exact live conditions (Nov 3 ~01:37 PKT):
    - Karachi: 2026-11-03 01:37 PKT (today = 2026-11-03)
    - Central: 2026-11-02 15:37 Central (today = 2026-11-02)
    - Run as_of: 2026-11-02T20:06:20.749689+00:00 (Nov 2)
    - Digest payments: Nov 1 (2026-11-01, count 0)
    - Waiting queue: 6 contacts waiting
    Outcome: Full briefing passes validation, no 'couldn't verify' fallback.
    """
    fixed_now = datetime(2026, 11, 2, 20, 37, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(periods, "business_today", lambda: date(2026, 11, 2))

    run_doc = {
        "_id": "run-nov2-live",
        "started_at": "2026-11-02T20:06:20.749689+00:00",
        "finished_at": "2026-11-02T20:06:20.749689+00:00",
        "crm_engagement": [
            {"contact_name": f"Contact {i}", "channel": "Email", "waiting_days": i, "last_customer_message": "test"}
            for i in range(1, 7)
        ],
        "crm_deal_watch": [],
    }
    db = business_db(
        agent_runs=Collection([run_doc]),
        customer360_payments=Collection([]),
    )

    briefing_answer = (
        "Morning briefing (CRM) as of agent run Nov 2, 2026, 8:06 PM UTC:\n\n"
        "1) Yesterday's CRM payments (for 2026-11-01) — Status: empty - "
        "No CRM payments were recorded for 2026-11-01 (count: 0).\n\n"
        "2) CRM follow-up waiting queue: 6 contacts waiting in the queue (Email: 6).\n"
        "Status: normal.\n\n"
        "3) Stalled deals: 0 deals idle."
    )

    model = FakeModel([
        _tool_message(1, "get_crm_morning_briefing", {}),
        plain_answer(briefing_answer),
    ])

    outcome = await execute_chat(
        "What is today's briefing? Show me the CRM follow-up queue",
        model=model,
        database=db,
    )

    assert outcome["validation_replaced"] is False, f"Replaced with reason: {outcome.get('validation_error')}"
    assert "couldn't verify that figure" not in outcome["text"]
    assert "2026-11-01" in outcome["text"]
    assert "6 contacts waiting" in outcome["text"]


@pytest.mark.asyncio
async def test_older_run_briefing_passes_with_explicit_dates(monkeypatch):
    """(ii) Older-run case: run date is days before chat date.
    - Chat date: 2026-11-02
    - Run date: 2026-10-25 (7 days earlier)
    - Payments date: 2026-10-24
    Briefing with explicit calendar dates passes without fallback.
    """
    monkeypatch.setattr(periods, "business_today", lambda: date(2026, 11, 2))

    run_doc = {
        "_id": "run-older",
        "started_at": "2026-10-25T10:00:00+00:00",
        "finished_at": "2026-10-25T10:00:00+00:00",
        "crm_engagement": [
            {"contact_name": "Alice", "channel": "WhatsApp", "waiting_days": 3, "last_customer_message": "hello"}
        ],
        "crm_deal_watch": [],
    }
    db = business_db(
        agent_runs=Collection([run_doc]),
        customer360_payments=Collection([]),
    )

    older_answer = (
        "Morning briefing (CRM) as of agent run 2026-10-25T10:00:00+00:00:\n\n"
        "1) CRM payments for 2026-11-01 — Status: empty - "
        "No CRM payments were recorded for 2026-11-01 (count: 0).\n\n"
        "2) CRM follow-up waiting queue: 1 contacts waiting in the queue (WhatsApp: 1).\n\n"
        "3) Stalled deals: 0 deals idle."
    )

    model = FakeModel([
        _tool_message(1, "get_crm_morning_briefing", {}),
        plain_answer(older_answer),
    ])

    outcome = await execute_chat(
        "What is today's briefing?",
        model=model,
        database=db,
    )

    assert outcome["validation_replaced"] is False
    assert "couldn't verify that figure" not in outcome["text"]
    assert "2026-11-01" in outcome["text"]
    assert "2026-10-25" in outcome["text"]


def test_genuinely_contradictory_briefing_draft_is_rejected():
    """(iii) Negative tests: guards are NOT weakened.
    Genuinely contradictory period wording or fabricated counts are caught.
    """
    tool_results = [{
        "tool": "get_crm_morning_briefing",
        "result": {
            "source": "crm_morning_briefing",
            "as_of_run": "2026-11-02T20:06:20.749689+00:00",
            "date": "2026-11-01",
            "payments": {"date": "2026-11-01", "count": 0, "total_received": 0.0},
            "waiting": {"total": 6, "whatsapp_total": 0, "email_total": 6, "top": []},
            "stalled_deals": {"total": 0, "top": []},
        },
    }]

    # Case A: Model claims 'last year' for a 2026-11-01 block
    bad_draft_period = (
        "Morning briefing (CRM) as of agent run 2026-11-02T20:06:20.749689+00:00:\n\n"
        "1) Last year's CRM payments for 2026-11-01: count: 0.\n\n"
        "2) CRM follow-up waiting queue: 6 contacts waiting."
    )
    errors = _answer_consistency_errors(bad_draft_period, tool_results, named_period="today")
    assert any("contradicts that block's date" in err for err in errors), f"Expected block date error, got: {errors}"

    # Case B: Unrelated turn where 'yesterday' contradicts 'today' without dated tool block
    general_tool_results = [{
        "tool": "get_revenue",
        "result": {"revenue_total": "$500", "count": 2},
    }]
    bad_draft_general = "Yesterday's revenue was $500."
    errors_gen = _answer_consistency_errors(bad_draft_general, general_tool_results, named_period="today")
    assert any("contradicts the resolved range (today)" in err for err in errors_gen)

    # Case C: Numeric guard catches invented count (claims 45 waiting instead of 6)
    invented_count = (
        "Morning briefing (CRM) as of agent run 2026-11-02T20:06:20.749689+00:00:\n\n"
        "1) CRM payments for 2026-11-01: count: 0.\n\n"
        "2) CRM follow-up waiting queue: 45 contacts waiting."
    )
    numeric_errors = validate_numeric_claims(invented_count, tool_results)
    assert len(numeric_errors) > 0, "Numeric guard should have caught invented number 45"


# ---------------------------------------------------------------------------
# ISSUE 2: CSV File Delivery & Offer Wording
# ---------------------------------------------------------------------------

def test_csv_offer_wording_matrix():
    """Consistency check matrix:
    - Text-only with pinned wording passes.
    - File/download/export wording without a file link is rejected.
    - File wording with real file link and CSV content passes.
    """
    tool_results = [{"tool": "get_whatsapp_latest_messages", "result": {"total_count": 26, "items": []}}]

    # 1. Pinned text-only wording passes
    pinned_text = (
        "Here are the latest messages. If you prefer, I can print it as CSV text "
        "here in chat to compare offline."
    )
    assert _answer_consistency_errors(pinned_text, tool_results, None) == []

    # 2. File/download wording without file link is REJECTED
    bad_offer_download = "Here are the messages. I can provide CSV text to download."
    errors_dl = _answer_consistency_errors(bad_offer_download, tool_results, None)
    assert any("contains no downloadable file link" in err for err in errors_dl)

    bad_offer_export = "Here are the messages, exported as CSV text for offline use."
    errors_exp = _answer_consistency_errors(bad_offer_export, tool_results, None)
    assert any("contains no downloadable file link" in err for err in errors_exp)

    bad_offer_file = "Would you like me to send you the CSV file?"
    errors_file = _answer_consistency_errors(bad_offer_file, tool_results, None)
    assert any("contains no downloadable file link" in err for err in errors_file)

    # 3. Real file link with CSV content PASSES
    valid_csv_file = (
        "Here is the CSV export with 2 rows:\n\n"
        "[Download whatsapp-messages.csv](#download:whatsapp-messages.csv)\n\n"
        "```csv\n"
        "Name,Record Type,Company,Latest Message\n"
        "Alice,Customer,Acme Inc,Hello\n"
        "Bob,Lead,Beta LLC,Hi\n"
        "```"
    )
    assert _answer_consistency_errors(valid_csv_file, tool_results, None) == []


@pytest.mark.asyncio
async def test_csv_request_interception_generates_file_export():
    """CSV request after listing retrieves all rows across pages and produces CSV."""
    db = business_db()
    db.customer360_whatsapp = Collection([
        {"_id": f"wa-{i}", "record_id": f"wa-{i}", "record_type": "Lead",
         "name": f"Contact {i}", "company": f"Co {i}", "whatsapp_count": 1,
         "latest_text": f"msg {i}", "latest_at": "2026-11-02T10:00:00+00:00",
         "latest_direction": "Inbound", "indexed_at": "2026-11-02T11:00:00+00:00"}
        for i in range(1, 15)
    ])
    db.chat_threads = FakeCollection([{
        "thread_id": "thread-csv-test",
        "last_query": {
            "tool": "get_whatsapp_latest_messages",
            "args": {"limit": 10, "offset": 0},
            "total_count": 14,
        },
        "turns": [{
            "question": "show me latest whatsapp messages",
            "answer": "Here are 10 messages...",
        }],
    }])
    db.chat_logs = FakeCollection()

    csv_response = (
        "Here is the complete CSV export (14 rows):\n\n"
        "[Download whatsapp-messages-2026-11-02.csv](#download:whatsapp-messages-2026-11-02.csv)\n\n"
        "```csv\n"
        "Name,Record Type,Company,Latest Message Date,Latest Direction,Latest Message\n"
        + "\n".join(f"Contact {i},Lead,Co {i},2026-11-02T10:00:00+00:00,Inbound,msg {i}" for i in range(1, 15))
        + "\n```"
    )

    model = FakeModel([plain_answer(csv_response)])

    result = await run_chat(
        "thread-csv-test",
        "give me this as CSV",
        database=db,
        model=model,
    )

    assert result["validation_replaced"] is False
    assert "#download:whatsapp-messages-2026-11-02.csv" in result["text"]
    assert "14 rows" in result["text"]
    assert "Contact 14" in result["text"]


# ---------------------------------------------------------------------------
# ISSUE 3: Single Final Answer per Turn (No Double-Emit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filtered_list_turn_yields_exactly_one_final_answer():
    """Issue 3: Verify that a filtered-list turn records exactly one turn entry in
    chat_threads, emits exactly one 'done' event, and records exactly one chat log.
    """
    db = business_db()
    db.chat_threads = FakeCollection([{"thread_id": "thread-filter-1", "turns": []}])
    db.chat_logs = FakeCollection()

    model = FakeModel([
        _tool_message(1, "get_whatsapp_latest_messages", {"date_from": "2026-11-01", "date_to": "2026-11-02"}),
        plain_answer("Found 1 message in range: Mrs Saad (Lead) on 2026-11-01."),
    ])

    events = [
        event async for event in stream_chat(
            "thread-filter-1",
            "messages from Nov 1 to Nov 2",
            database=db,
            model=model,
        )
    ]

    done_events = [e for e in events if e.get("event") == "done"]
    assert len(done_events) == 1, f"Expected exactly 1 done event, got {len(done_events)}"

    # Check database persistence
    stored_thread = db.chat_threads.updated
    turns = stored_thread.get("turns", [])
    assert len(turns) == 1, f"Expected exactly 1 recorded turn, got {len(turns)}"
    assert turns[0]["question"] == "messages from Nov 1 to Nov 2"
    assert "Mrs Saad" in turns[0]["answer"]
    assert len(db.chat_logs.inserted) == 1


# ---------------------------------------------------------------------------
# ISSUE 1: CSV emission after multi-page walk (270 rows) + negative tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_csv_request_multi_page_walk_emits_file_card_and_all_270_rows(monkeypatch):
    """Issue 1: When user asks for CSV after an overdue listing of 270 rows (11 pages),
    the emission produces a file link + full CSV block with all 270 rows, stated count 270,
    and no 'more on later pages' contradiction."""
    monkeypatch.setattr(periods, "business_today", lambda: date(2026, 11, 2))

    # Generate 270 mock invoices
    invoices = []
    for i in range(1, 271):
        invoices.append({
            "_id": f"inv-{i}",
            "id": i,
            "inv_ref": f"INV-{i:04d}",
            "customer_id": f"cust-{i}",
            "customer": {"name": f"Customer {i}"},
            "total_amount": 100.0,
            "amount_paid": 0.0,
            "payment_status_label": "Unpaid",
            "status_label": "Approved",
            "invoice_date": "2026-09-01",
            "due_date": "2026-10-01",
        })

    db = business_db()
    db.orders_flat = Collection(invoices)
    db.chat_threads = FakeCollection([{
        "thread_id": "thread-overdue-csv",
        "last_query": {
            "tool": "get_invoices",
            "args": {"overdue": True},
            "total_count": 270,
            "limit": 26,
            "offset": 0,
        },
        "turns": [{
            "question": "Which invoices are overdue?",
            "answer": "There are 270 overdue invoices totaling $27,000.00.",
        }],
    }])
    db.chat_logs = FakeCollection()

    # Model tries to emit only an evidence summary without file link or CSV code block
    bad_model_answer = "Verified from backend records: $27,000.00 across 270 matching invoices; 243 more are on later pages and can be fetched."
    model = FakeModel([plain_answer(bad_model_answer), plain_answer(bad_model_answer)])

    result = await run_chat(
        "thread-overdue-csv",
        "give this in CSV......",
        database=db,
        model=model,
    )

    # Must contain downloadable file link with correct date
    assert "[Download invoices-2026-11-02.csv](#download:invoices-2026-11-02.csv)" in result["text"]
    # Must contain ```csv code block
    assert "```csv" in result["text"]
    # Must contain all 270 rows
    assert "INV-0270" in result["text"]
    assert "Customer 270" in result["text"]
    # Stated row count must be 270 rows
    assert "270 rows" in result["text"]
    # Must NOT claim unfetched later pages exist
    assert "more are on later pages" not in result["text"]
    assert "can be fetched" not in result["text"]
    # Structured data is attached
    assert result.get("csv_export") is not None
    assert result["csv_export"]["row_count"] == 270
    assert result["csv_export"]["filename"] == "invoices-2026-11-02.csv"


@pytest.mark.asyncio
async def test_csv_request_single_row_pluralization(monkeypatch):
    """Issue 1 & 5: Single-row CSV export uses '1 row' (never '1 rows')."""
    monkeypatch.setattr(periods, "business_today", lambda: date(2026, 11, 2))

    single_invoice = [{
        "_id": "inv-1",
        "id": 1,
        "inv_ref": "INV-0001",
        "customer_id": "cust-1",
        "customer": {"name": "Customer 1"},
        "total_amount": 500.0,
        "amount_paid": 0.0,
        "payment_status_label": "Unpaid",
        "status_label": "Approved",
        "invoice_date": "2026-09-01",
        "due_date": "2026-10-01",
    }]

    db = business_db()
    db.orders_flat = Collection(single_invoice)
    db.chat_threads = FakeCollection([{
        "thread_id": "thread-single-csv",
        "last_query": {
            "tool": "get_invoices",
            "args": {"overdue": True},
            "total_count": 1,
            "limit": 26,
            "offset": 0,
        },
        "turns": [{
            "question": "Which invoices are overdue?",
            "answer": "There is 1 overdue invoice.",
        }],
    }])
    db.chat_logs = FakeCollection()

    model = FakeModel([plain_answer("Here is the CSV: 1 rows"), plain_answer("Here is the CSV: 1 rows")])

    result = await run_chat(
        "thread-single-csv",
        "give this as CSV",
        database=db,
        model=model,
    )

    assert "1 row" in result["text"]
    assert "1 rows" not in result["text"]
    assert "[Download invoices-2026-11-02.csv](#download:invoices-2026-11-02.csv)" in result["text"]


def test_negative_draft_csv_checks():
    """Negative tests for CSV turns:
    - Missing download link or CSV block is rejected.
    - Contradictory claim of later pages after walk_complete is rejected.
    - Incorrect stated row count is rejected.
    """
    tool_results = [{
        "tool": "get_invoices",
        "result": {
            "items": [{"id": 1, "inv_ref": "INV-1"}, {"id": 2, "inv_ref": "INV-2"}],
            "total_count": 2,
            "walk_complete": True,
            "is_csv_turn": True,
        },
    }]

    # Case A: Missing link/block
    bad_no_link = "I have fetched 2 rows for you in CSV format."
    errors_no_link = _answer_consistency_errors(bad_no_link, tool_results, None)
    assert any("missing a download link or CSV block" in err for err in errors_no_link)

    # Case B: Claims more records exist when walk_complete is True
    bad_more_pages = (
        "Here is the CSV with 2 rows:\n\n"
        "[Download test.csv](#download:test.csv)\n\n"
        "```csv\nName,ID\nA,1\nB,2\n```\n\n"
        "243 more are on later pages and can be fetched."
    )
    errors_more_pages = _answer_consistency_errors(bad_more_pages, tool_results, None)
    assert any("claims unfetched later pages exist after a complete walk" in err for err in errors_more_pages)

    # Case C: Claims wrong number of rows
    bad_row_count = (
        "Here is the CSV with 50 rows:\n\n"
        "[Download test.csv](#download:test.csv)\n\n"
        "```csv\nName,ID\nA,1\nB,2\n```"
    )
    errors_row_count = _answer_consistency_errors(bad_row_count, tool_results, None)
    assert any("claims 50 rows but walked 2 rows" in err for err in errors_row_count)


# ---------------------------------------------------------------------------
# ISSUE 2: Summary-Collapse Prevention
# ---------------------------------------------------------------------------

def test_summary_collapse_ranking_page_3_renders_table_and_metadata():
    """Ranking page 3 shows the final 6 rows with page 3 of 3 metadata."""
    items = [
        {"name": f"Contact {i}", "record_type": "Lead", "company": f"Co {i}",
         "whatsapp_count": 30 - i, "inbound_count": 2, "outbound_count": 28 - i, "latest_at": "2026-11-01"}
        for i in range(21, 27)
    ]
    tool_results = [{
        "tool": "get_whatsapp_top_contacts",
        "result": {
            "source": "customer360_whatsapp",
            "items": items,
            "total_count": 26,
            "offset": 20,
            "limit": 10,
            "returned": 6,
            "remaining_count": 0,
            "has_more": False,
            "page": 3,
            "pages_total": 3,
            "all_records_returned": True,
        },
    }]

    summary = _evidence_summary(tool_results, None)
    assert summary is not None
    # Must contain page 3 of 3 metadata
    assert "Page 3 of 3 (6 shown of 26 total)" in summary
    # Must contain table rows
    assert "| Contact 21 |" in summary
    assert "| Contact 26 |" in summary
    assert summary.count("| Contact") == 6


def test_summary_collapse_whole_list_renders_all_26_rows():
    """Whole-list latest shows all 26 rows."""
    items = [
        {"name": f"Person {i}", "record_type": "Lead", "company": "",
         "latest_direction": "Inbound", "latest_at": "2026-11-01", "latest_text": f"Msg {i}"}
        for i in range(1, 27)
    ]
    tool_results = [{
        "tool": "get_whatsapp_latest_messages",
        "result": {
            "source": "customer360_whatsapp",
            "items": items,
            "total_count": 26,
            "offset": 0,
            "limit": 26,
            "returned": 26,
            "remaining_count": 0,
            "has_more": False,
            "page": 1,
            "pages_total": 1,
            "all_records_returned": True,
        },
    }]

    summary = _evidence_summary(tool_results, None)
    assert summary is not None
    assert "| Person 1 |" in summary
    assert "| Person 26 |" in summary
    assert summary.count("| Person") == 26


def test_summary_collapse_treatday_renders_10_rows():
    """TREATDAY payments shows all 10 rows."""
    items = [
        {"id": f"pay-{i}", "payment_id": i, "customer": "TREATDAY",
         "amount": 100.0, "date": "2026-10-20", "method": "Card", "status": "Completed"}
        for i in range(1, 11)
    ]
    tool_results = [{
        "tool": "get_payments",
        "result": {
            "source": "erp_payments",
            "items": items,
            "total_count": 10,
            "offset": 0,
            "limit": 10,
            "returned": 10,
            "remaining_count": 0,
            "has_more": False,
            "money_total": 1000.0,
            "page": 1,
            "pages_total": 1,
        },
    }]

    summary = _evidence_summary(tool_results, None)
    assert summary is not None
    assert "| pay-1 |" in summary
    assert "| pay-10 |" in summary
    assert summary.count("| pay-") == 10


def test_negative_list_answer_zero_rows_rejected():
    """A draft list answer with zero rows and no file is rejected by consistency checks."""
    tool_results = [{
        "tool": "get_whatsapp_top_contacts",
        "result": {
            "items": [{"name": f"Contact {i}", "whatsapp_count": 5} for i in range(1, 7)],
            "total_count": 26,
            "has_more": False,
        },
    }]

    zero_row_draft = "Verified from backend records: all 26 matching records."
    errors = _answer_consistency_errors(zero_row_draft, tool_results, None)
    assert any("list answer contains zero rows" in err for err in errors)


# ---------------------------------------------------------------------------
# ISSUE 3: Human-Readable Dates
# ---------------------------------------------------------------------------

def test_human_readable_dates_consistency():
    """Raw microsecond ISO timestamps are prohibited in visible prose;
    clean human-readable dates pass."""
    tool_results = [{
        "tool": "get_crm_morning_briefing",
        "result": {
            "source": "crm_morning_briefing",
            "as_of_run": "2026-11-02T21:54:10.670038+00:00",
            "as_of_run_human": "Nov 2, 2026, 9:54 PM UTC",
        },
    }]

    bad_text = "Morning briefing as of agent run 2026-11-02T21:54:10.670038+00:00: all systems normal."
    errors_bad = _answer_consistency_errors(bad_text, tool_results, None)
    assert any("raw microsecond timestamp" in err for err in errors_bad)

    good_text = "Morning briefing as of agent run Nov 2, 2026, 9:54 PM UTC: all systems normal."
    errors_good = _answer_consistency_errors(good_text, tool_results, None)
    assert not any("raw microsecond timestamp" in err for err in errors_good)


# ---------------------------------------------------------------------------
# ISSUE 4: Queue Swing 6 -> 0 Explanation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_swing_zero_queue_explains_previous_run():
    """When latest run has 0 waiting and previous run had 5 waiting,
    get_crm_follow_up_queue provides previous_run_waiting and explanatory note."""
    from app.agents.chat_tools import get_crm_follow_up_queue

    run1_prev = {
        "_id": "run-prev",
        "started_at": "2026-11-02T20:51:00+00:00",
        "finished_at": "2026-11-02T20:51:00+00:00",
        "crm_engagement": [
            {"contact_name": f"User {i}", "channel": "WhatsApp", "waiting_days": 2, "last_customer_message": "hi"}
            for i in range(5)
        ],
        "crm_deal_watch": [],
    }
    run2_latest = {
        "_id": "run-latest",
        "started_at": "2026-11-02T21:54:10.670038+00:00",
        "finished_at": "2026-11-02T21:54:10.670038+00:00",
        "crm_engagement": [],
        "crm_deal_watch": [{"deal_name": "Deal 1", "amount": 1000}],
    }

    db = business_db(
        agent_runs=Collection([run2_latest, run1_prev]),
    )

    result = await get_crm_follow_up_queue(database=db)
    assert result["waiting_total"] == 0
    assert result.get("previous_run_waiting") == 5
    assert "No one waiting in the latest run (previous run had 5 waiting)." in result.get("note", "")


# ---------------------------------------------------------------------------
# ISSUE 5: Pluralization & Re-Index Runner Command
# ---------------------------------------------------------------------------

def test_pluralization_and_reindex_command_checks():
    """- '1 rows' rejected, '1 row' passes.
    - 'runwhatsappindex' rejected, 'server/run_whatsapp_index.py --full' passes.
    """
    tool_results = [{"tool": "get_payments", "result": {"total_count": 1}}]

    # Pluralization
    err_plural = _answer_consistency_errors("Exported 1 rows.", tool_results, None)
    assert any("pluralization error" in err for err in err_plural)

    ok_plural = _answer_consistency_errors("Exported 1 row.", tool_results, None)
    assert not any("pluralization error" in err for err in ok_plural)

    # Re-index runner
    err_runner = _answer_consistency_errors("Please run runwhatsappindex to update.", tool_results, None)
    assert any("re-index command must be" in err for err in err_runner)

    ok_runner = _answer_consistency_errors("Please run server/run_whatsapp_index.py --full to update.", tool_results, None)
    assert not any("re-index command must be" in err for err in ok_runner)


# ---------------------------------------------------------------------------
# ISSUE 6: Overdue Boundary Clarity (0 days overdue)
# ---------------------------------------------------------------------------

def test_overdue_boundary_label():
    """An invoice with 30 days age exceeding 30-day terms is overdue today:
    days_overdue = 0, labeled '0 days past 30-day terms (due today / first day overdue)'.
    """
    from app.agents.chat_tools import _invoice_row
    from datetime import datetime, timezone

    now = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
    # 30.8 days age: issued Oct 3 at 00:00, now is Nov 2 at 20:00
    row = {
        "id": 33245,
        "inv_ref": "33245-2026-10",
        "customer": {"name": "Test Customer"},
        "total_amount": 1200.0,
        "amount_paid": 0.0,
        "payment_status_label": "Unpaid",
        "status_label": "Approved",
        "invoice_date": "2026-10-03",
        "due_date": "2026-11-02",
    }
    result = _invoice_row(row, now)
    assert result["overdue"] is True
    assert result["days_overdue"] == 0
    assert result["days_overdue_label"] == "0 days past 30-day terms (due today / first day overdue)"


# ---------------------------------------------------------------------------
# ISSUE 1: CSV Scoping (New entity overrides previous listing)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_csv_scope_entity_overrides_previous_listing():
    """Test (b): overdue list in prior turn -> 'give invoice 33245 in CSV' returns
    1 row (only invoice 33245, stated '1 row'), NOT the 270 overdue rows.
    """
    invoice_33245 = {
        "_id": 33245,
        "id": 33245,
        "inv_ref": "33245-2026-10",
        "customer": {"id": 2207, "company_name": "Pure 7"},
        "total_amount": 70425.0,
        "amount_paid": 0.0,
        "payment_status_label": "No payment recorded",
        "status_label": "Approved",
        "invoice_date": "2026-10-03T03:53:45-05:00",
    }
    other_invoices = [
        {
            "_id": i,
            "id": i,
            "inv_ref": f"INV-{i}",
            "customer": {"id": i, "company_name": f"Customer {i}"},
            "total_amount": 100.0,
            "amount_paid": 0.0,
            "payment_status_label": "Unpaid",
            "status_label": "Approved",
            "invoice_date": "2026-09-01",
        }
        for i in range(1, 270)
    ]
    all_invoices = [invoice_33245] + other_invoices

    db = business_db()
    db.orders_flat = Collection(all_invoices)
    db.chat_threads = FakeCollection([{
        "thread_id": "thread-csv-scope",
        "last_query": {
            "tool": "get_invoices",
            "args": {"overdue": True},
            "total_count": 270,
            "limit": 26,
            "offset": 0,
        },
        "turns": [{
            "question": "Which invoices are overdue?",
            "answer": "There are 270 overdue invoices totaling $1,777,747.31.",
        }],
    }])
    db.chat_logs = FakeCollection()

    model = FakeModel([
        plain_answer(
            "I have generated a CSV file with 1 row for invoice 33245.\n\n"
            "[Download invoice-33245.csv](#download:invoice-33245.csv)\n\n"
            "```csv\n"
            "Invoice Number,Customer,Amount,Balance,Payment Status,Order Status,Invoice Date,Due Date,Days Overdue\n"
            '33245-2026-10,Pure 7,"$70,425.00","$70,425.00",No payment recorded,Approved,2026-10-03T03:53:45-05:00,,0 days past 30-day terms (due today / first day overdue)\n'
            "```"
        )
    ])

    result = await run_chat(
        "thread-csv-scope",
        "give invoice 33245 in CSV",
        database=db,
        model=model,
    )

    assert result["validation_replaced"] is False
    assert "1 row" in result["text"]
    assert "270" not in result["text"]
    assert "#download:invoice-33245.csv" in result["text"]
    assert result.get("csv_export") is not None
    assert result["csv_export"]["filename"] == "invoice-33245.csv"
    assert result["csv_export"]["row_count"] == 1


@pytest.mark.asyncio
async def test_csv_scope_fresh_entity_with_no_prior_listing():
    """Test (c): Fresh 'give invoice 33245 in CSV' with no prior listing -> 1 row."""
    invoice_33245 = {
        "_id": 33245,
        "id": 33245,
        "inv_ref": "33245-2026-10",
        "customer": {"id": 2207, "company_name": "Pure 7"},
        "total_amount": 70425.0,
        "amount_paid": 0.0,
        "payment_status_label": "No payment recorded",
        "status_label": "Approved",
        "invoice_date": "2026-10-03T03:53:45-05:00",
    }
    db = business_db()
    db.orders_flat = Collection([invoice_33245])
    db.chat_threads = FakeCollection([{"thread_id": "thread-fresh-csv", "turns": []}])
    db.chat_logs = FakeCollection()

    model = FakeModel([
        plain_answer(
            "I have generated a CSV file with 1 row for invoice 33245.\n\n"
            "[Download invoice-33245.csv](#download:invoice-33245.csv)\n\n"
            "```csv\n"
            "Invoice Number,Customer,Amount,Balance,Payment Status,Order Status,Invoice Date,Due Date,Days Overdue\n"
            '33245-2026-10,Pure 7,"$70,425.00","$70,425.00",No payment recorded,Approved,2026-10-03T03:53:45-05:00,,0 days past 30-day terms (due today / first day overdue)\n'
            "```"
        )
    ])

    result = await run_chat(
        "thread-fresh-csv",
        "give invoice 33245 in CSV",
        database=db,
        model=model,
    )

    assert result["validation_replaced"] is False
    assert "1 row" in result["text"]
    assert "#download:invoice-33245.csv" in result["text"]
    assert result.get("csv_export") is not None
    assert result["csv_export"]["row_count"] == 1


# ---------------------------------------------------------------------------
# ISSUE 2: Summary-Collapse Coverage Across Listing Tools
# ---------------------------------------------------------------------------

def test_summary_collapse_coverage_all_listing_tools():
    """Audit collapse guard + deterministic table fallback across ALL listing tools:
    get_whatsapp_latest_messages, get_whatsapp_top_contacts, get_payments,
    get_invoices, get_quotes, get_orders, get_leads, get_crm_follow_up_queue.
    """
    from app.agents.chat import _LISTING_TOOLS, _format_tool_table, _evidence_summary

    expected_tools = {
        "get_whatsapp_latest_messages",
        "get_whatsapp_top_contacts",
        "get_payments",
        "get_invoices",
        "get_quotes",
        "get_orders",
        "get_leads",
        "get_crm_follow_up_queue",
    }
    assert expected_tools.issubset(_LISTING_TOOLS), f"Missing tools in _LISTING_TOOLS: {expected_tools - _LISTING_TOOLS}"

    # WhatsApp latest messages candidate table passes validate_numeric_claims
    raw_time = "2026-11-02T19:08:00+00:00"
    wa_items = [
        {
            "name": f"Contact {i}",
            "record_type": "Lead",
            "company": f"Company {i}",
            "latest_direction": "Inbound",
            "latest_at": raw_time,
            "latest_text": f"Hello there message {i}",
        }
        for i in range(1, 27)
    ]
    table = _format_tool_table("get_whatsapp_latest_messages", wa_items)
    tool_results = [{
        "tool": "get_whatsapp_latest_messages",
        "result": {
            "items": wa_items,
            "total_count": 26,
            "returned": 26,
            "offset": 0,
            "limit": 50,
            "has_more": False,
            "all_records_returned": True,
        },
    }]
    errors = validate_numeric_claims(table, tool_results, "", "")
    assert errors == [], f"WhatsApp latest messages table failed validation: {errors}"


# ---------------------------------------------------------------------------
# ISSUE 3: Standalone Queue Fallback
# ---------------------------------------------------------------------------

def test_standalone_queue_fallback_passes():
    """Standalone queue turn delivers waiting queue rows and as_of_human date,
    never falling back to 'couldn't verify that figure'.
    """
    queue_data = {
        "source": "crm_follow_up_queue",
        "available": True,
        "run_status": "finished",
        "as_of": "2026-11-02T22:39:00+00:00",
        "as_of_human": "Nov 2, 2026, 10:39 PM UTC",
        "waiting_total": 5,
        "by_channel": {"WhatsApp": 3, "Email": 2},
        "queue": [
            {
                "name": f"Customer {i}",
                "company": f"Company {i}",
                "channel": "WhatsApp" if i <= 3 else "Email",
                "waiting_days": i + 1,
                "last_customer_message": f"Help on order {i}",
            }
            for i in range(1, 6)
        ],
    }
    tool_results = [{"tool": "get_crm_follow_up_queue", "result": queue_data}]
    summary = _evidence_summary(tool_results, question="Show me the follow-up queue")
    assert summary is not None
    assert "Nov 2, 2026, 10:39 PM UTC" in summary
    assert "5 customers waiting" in summary
    assert "Customer 1" in summary


# ---------------------------------------------------------------------------
# ISSUE 4: Never-Messaged Regression & Disclaimer Exemption
# ---------------------------------------------------------------------------

def test_never_messaged_evidence_summary_refusal_and_coverage():
    """When a never-messaged question fails validation, _evidence_summary emits
    the honest refusal + coverage + 3 options with the exact runner command,
    never '26 matching records'.
    """
    tool_results = [{
        "tool": "get_whatsapp_latest_messages",
        "result": {
            "items": [{"name": f"Person {i}", "whatsapp_count": 1} for i in range(26)],
            "total_count": 51,
            "returned": 26,
            "offset": 0,
            "limit": 50,
            "has_more": True,
            "coverage": {
                "indexed_person_records": 51,
                "total_person_records": 31349,
                "label": "Indexed 51 of 31,349 person records, as of Nov 2, 2026, 10:56 PM UTC",
            },
        },
    }]
    summary = _evidence_summary(
        tool_results,
        question="Who has never had a WhatsApp message?",
    )
    assert summary is not None
    assert "cannot reliably list people who have never had a WhatsApp message" in summary
    assert "Indexed 51 of 31,349 person records" in summary
    assert "server/run_whatsapp_index.py --full" in summary
    assert "matching records" not in summary
    assert "more are on later pages" not in summary


def test_never_messaged_disclaimer_not_flagged_as_csv_file_wording():
    """The disclaimer '(This is CSV text in chat, not a downloadable file.)' must
    NOT be rejected by csv_file_wording consistency check.
    """
    text = (
        "The WhatsApp index is partial — I cannot reliably list people who have never had a WhatsApp message.\n\n"
        "Options:\n"
        "(a) Page through the records that have messages.\n"
        "(b) Export them as CSV text in chat to compare offline. (This is CSV text in chat, not a downloadable file.)\n"
        "(c) Run a full index sweep via `server/run_whatsapp_index.py --full`."
    )
    tool_results = [{
        "tool": "get_whatsapp_latest_messages",
        "result": {
            "coverage": {
                "label": "Indexed 51 of 31,349 person records, as of Nov 2, 2026, 10:56 PM UTC",
            },
        },
    }]
    errors = _answer_consistency_errors(text, tool_results, None)
    assert not any("the answer describes CSV with file/download/export wording" in err for err in errors), f"Unexpected error: {errors}"


# ---------------------------------------------------------------------------
# ISSUE 5: Detail-View Overdue Label & Human Dates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detail_view_overdue_label_and_human_dates():
    """get_record for invoice 33245-2026-10 populates overdue boundary label and invoice_date_human."""
    from app.agents.chat_tools import get_record

    raw_invoice = {
        "_id": 33245,
        "id": 33245,
        "inv_ref": "33245-2026-10",
        "customer": {"id": 2207, "company_name": "Pure 7"},
        "total_amount": 70425.0,
        "amount_paid": 0.0,
        "payment_status_label": "No payment recorded",
        "status_label": "Approved",
        "invoice_date": "2026-10-03T03:53:45-05:00",
    }
    db = business_db()
    db.orders_flat = Collection([raw_invoice])

    res = await get_record("invoices", "33245-2026-10", database=db)
    assert res["found"] is True
    rec = res["record"]
    assert rec["days_overdue_label"] == "0 days past 30-day terms (due today / first day overdue)"
    assert "invoice_date_human" in rec
    assert "Oct 3, 2026" in rec["invoice_date_human"]


# ---------------------------------------------------------------------------
# MINOR ITEM (a): WhatsApp Top Contacts Direction Breakdown Mismatch
# ---------------------------------------------------------------------------

def test_whatsapp_top_contacts_direction_breakdown_mismatch_shows_dash():
    """When Inbound + Outbound does not equal Total, table displays '—' for Inbound and Outbound."""
    items = [{
        "name": "Jane Doe",
        "record_type": "Contact",
        "company": "Acme Corp",
        "whatsapp_count": 2,
        "inbound_count": 0,
        "outbound_count": 0,
    }]
    table = _format_tool_table("get_whatsapp_top_contacts", items)
    assert "| Jane Doe | Contact | Acme Corp | 2 | — | — |" in table

