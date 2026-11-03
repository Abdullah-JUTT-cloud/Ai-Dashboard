"""The WhatsApp index: builder extraction, resumable sync, and the two chat
tools that read it.

Why the index exists (Phase 0, live Nov 2): the CRM has no messages endpoint
(every message-like route 404s) and message bodies live only inside per-record
detail timelines - so "latest WhatsApp message per person" and "who has the
most WhatsApp messages" must be answered from a local mirror, never from a
per-turn CRM walk and never from the follow-up queue.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agents.chat_tools import invoke_tool
from app.services import whatsapp_index
from test_chat_data_tools import Collection


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, limit):
        return self._rows[:limit]


class FakeCol:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, *args, **kwargs):
        return FakeCursor(self.rows)

    async def find_one(self, query):
        wanted = (query or {}).get("_id")
        return next((row for row in self.rows if row.get("_id") == wanted), None)

    async def count_documents(self, query=None):
        return len(self.rows)

    async def insert_one(self, row):
        self.rows.append(dict(row))

    async def update_one(self, query, update, upsert=False):
        target = (query or {}).get("_id")
        row = next((r for r in self.rows if r.get("_id") == target), None)
        if row is None:
            self.rows.append({"_id": target, **update["$set"]})
        else:
            row.update(update["$set"])


class FakeDb:
    def __init__(self, **cols):
        for name, rows in cols.items():
            setattr(self, name, FakeCol(rows))


def detail_payload(messages):
    return {
        "success": True,
        "timeline": [
            {"type": t, "direction": d, "timestamp": ts, "content": c}
            for (t, d, ts, c) in messages
        ],
    }


# ------------------------------------------------------------------ extraction


def test_extract_counts_whatsapp_only_and_picks_the_latest():
    payload = detail_payload([
        ("Email", "Outbound", "2026-10-30T09:00:00+00:00", "quote attached"),
        ("WhatsApp", "Inbound", "2026-11-01T08:30:00+00:00", "any update on the proofs?"),
        ("WhatsApp", "Outbound", "2026-10-29T12:00:00+00:00", "sent yesterday"),
        ("Call", None, "2026-11-02T01:00:00+00:00", "call outcome"),
    ])
    stats = whatsapp_index.extract_whatsapp_stats(payload)
    assert stats["whatsapp_count"] == 2
    assert stats["latest_text"] == "any update on the proofs?"
    assert stats["latest_at"] == "2026-11-01T08:30:00+00:00"
    assert stats["latest_direction"] == "Inbound"
    assert stats["first_message_at"] == "2026-10-29T12:00:00+00:00"
    assert stats["last_message_at"] == "2026-11-01T08:30:00+00:00"


def test_extract_empty_timeline_is_a_zero_not_an_error():
    stats = whatsapp_index.extract_whatsapp_stats({"success": True, "timeline": []})
    assert stats["whatsapp_count"] == 0
    assert stats["latest_text"] is None


def test_extract_strips_html_from_the_latest_text():
    payload = detail_payload([
        ("WhatsApp", "Inbound", "2026-11-01T08:30:00+00:00",
         "<div><p>Hello &amp; welcome</p><p>please confirm</p></div>"),
    ])
    stats = whatsapp_index.extract_whatsapp_stats(payload)
    assert stats["latest_text"] == "Hello & welcome please confirm"


# ------------------------------------------------------------------ sync pass


class FakeClient:
    def __init__(self, payloads=None, error_after=None):
        self.payloads = payloads or {}
        self.calls: list[tuple[str, str]] = []
        self.error_after = error_after

    async def detail(self, record_id, record_type):
        self.calls.append((record_id, record_type))
        if self.error_after is not None and len(self.calls) > self.error_after:
            raise RuntimeError("HTTP 429 too many requests")
        return self.payloads.get(record_id, detail_payload([]))


@pytest.mark.asyncio
async def test_sync_indexes_cache_for_free_and_fetches_recent_leads():
    db = FakeDb(
        customer360_cache=[
            {"_id": "Lead:c1", "record_id": "c1", "record_type": "Lead",
             "payload": detail_payload([("WhatsApp", "Inbound", "2026-11-01T09:00:00+00:00", "hi")])},
        ],
        customer360_leads=[
            {"_id": "c1", "firstName": "Cached", "lastName": "Person", "company": "C Co",
             "lastActivityAt": "2026-11-01T09:00:00+00:00"},
            {"_id": "n1", "firstName": "New", "lastName": "Lead", "company": "N Co",
             "lastActivityAt": "2026-11-01T10:00:00+00:00"},
            {"_id": "o1", "firstName": "Old", "lastName": "Lead", "company": "O Co",
             "lastActivityAt": "2020-01-01T00:00:00+00:00"},
        ],
        customer360_contacts=[],
        customer360_customers=[],
        customer360_whatsapp=[],
        customer360_whatsapp_runs=[],
    )
    client = FakeClient({"n1": detail_payload([
        ("WhatsApp", "Outbound", "2026-10-31T07:00:00+00:00", "sent the quote"),
    ])})
    summary = await whatsapp_index.run_whatsapp_sync(db, client, days=14, concurrency=2, sleep=lambda _s: _noop())
    assert summary["cache_indexed"] == 1
    assert summary["detail_fetched"] == 1
    assert ("n1", "Lead") in client.calls
    assert all(call[0] != "o1" for call in client.calls), "the stale lead must stay out of the window"
    by_id = {row["_id"]: row for row in db.customer360_whatsapp.rows}
    assert by_id["Lead:n1"]["whatsapp_count"] == 1
    assert by_id["Lead:n1"]["name"] == "New Lead"
    assert by_id["Lead:n1"]["company"] == "N Co"
    assert by_id["Lead:c1"]["source"] == "cache"
    assert summary["coverage"]["people_records"] == 3


async def _noop():
    return None


@pytest.mark.asyncio
async def test_sync_is_resumable_and_backs_off_without_hammering():
    rows = [
        {"_id": f"l{index}", "firstName": f"L{index}", "company": "Co",
         "lastActivityAt": "2026-11-01T10:00:00+00:00"}
        for index in range(10)
    ]
    db = FakeDb(
        customer360_cache=[], customer360_leads=rows,
        customer360_contacts=[], customer360_customers=[],
        customer360_whatsapp=[], customer360_whatsapp_runs=[],
    )
    client = FakeClient(error_after=1)  # first call ok, then every call fails
    slept: list[float] = []

    async def record_sleep(seconds):
        slept.append(seconds)

    summary = await whatsapp_index.run_whatsapp_sync(
        db, client, days=14, concurrency=1, sleep=record_sleep,
    )
    assert summary["detail_failed"] >= 1
    assert summary["aborted_early"] is True
    assert slept, "failures must back off, never hammer"
    assert all(0 < value <= 60 for value in slept)

    # second pass: the record indexed after its activity is skipped (resumed)
    fresh = FakeClient()
    summary_two = await whatsapp_index.run_whatsapp_sync(
        db, fresh, days=14, concurrency=1, sleep=record_sleep,
    )
    assert ("l0", "Lead") not in fresh.calls


@pytest.mark.asyncio
async def test_estimate_reports_the_cost_before_anything_runs():
    db = FakeDb(
        customer360_cache=[],
        customer360_leads=[
            {"_id": "l1", "lastActivityAt": "2026-11-01T10:00:00+00:00"},
            {"_id": "l2", "lastActivityAt": "2020-01-01T00:00:00+00:00"},
        ],
        customer360_contacts=[], customer360_customers=[],
        customer360_whatsapp=[], customer360_whatsapp_runs=[],
    )
    report = await whatsapp_index.estimate(db, days=14, concurrency=4)
    assert report["candidates"] == 1
    assert report["estimated_minutes"] >= 0


@pytest.mark.asyncio
async def test_bounded_window_walks_leads_only_full_walks_everyone_once():
    """contacts/customers updatedAt is re-stamped by any CRM touch, so the
    bounded pass must never re-read them (a hammer); they arrive through the
    cache tier or the warned --full pass, once."""
    rows = {"customer360_leads": [{"_id": "l1", "lastActivityAt": "2026-11-01T10:00:00+00:00"}],
            "customer360_contacts": [{"_id": "k1", "updatedAt": "2026-11-01T10:00:00+00:00"}],
            "customer360_customers": [{"_id": "u1", "updatedAt": "2026-11-01T10:00:00+00:00"}]}

    def fresh_db():
        return FakeDb(customer360_cache=[], customer360_whatsapp=[],
                      customer360_whatsapp_runs=[], **rows)

    bounded = FakeClient()
    await whatsapp_index.run_whatsapp_sync(fresh_db(), bounded, days=14, concurrency=1,
                                           sleep=lambda _s: _noop())
    assert bounded.calls == [("l1", "Lead")], "the bounded pass walks the leads window only"

    full = FakeClient()
    await whatsapp_index.run_whatsapp_sync(fresh_db(), full, days=None, concurrency=1,
                                           sleep=lambda _s: _noop())
    assert {call[0] for call in full.calls} == {"l1", "k1", "u1"}

    # a second full pass re-reads none of them: one-shot records never churn
    again = FakeClient()
    db_two = fresh_db()
    db_two.customer360_whatsapp.rows = [
        {"_id": f"{rtype}:{rid}", "record_id": rid, "record_type": rtype,
         "indexed_at": "2026-11-02T12:00:00+00:00"}
        for rid, rtype in (("l1", "Lead"), ("k1", "Contact"), ("u1", "Customer"))
    ]
    await whatsapp_index.run_whatsapp_sync(db_two, again, days=None, concurrency=1,
                                           sleep=lambda _s: _noop())
    assert again.calls == []


# ------------------------------------------------------------------ chat tools


def indexed_db():
    return FakeDb(
        customer360_whatsapp=[
            {"_id": "Lead:a", "record_id": "a", "record_type": "Lead", "name": "Autumn",
             "company": "A Co", "whatsapp_count": 4, "latest_text": "Hi Autumn,",
             "latest_at": "2026-11-02T10:00:00+00:00", "latest_direction": "Inbound",
             "indexed_at": "2026-11-02T11:00:00+00:00"},
            {"_id": "Contact:b", "record_id": "b", "record_type": "Contact", "name": "Brave",
             "company": "B Co", "whatsapp_count": 9, "latest_text": "invoice?",
             "latest_at": "2026-11-01T10:00:00+00:00", "latest_direction": "Outbound",
             "indexed_at": "2026-11-02T11:00:00+00:00"},
            {"_id": "Customer:c", "record_id": "c", "record_type": "Customer", "name": "Cust",
             "company": None, "whatsapp_count": 0, "latest_text": None,
             "latest_at": None, "latest_direction": None,
             "indexed_at": "2026-11-02T11:00:00+00:00"},
        ],
        customer360_leads=[{"_id": "a"}],
        customer360_contacts=[{"_id": "b"}],
        customer360_customers=[{"_id": "c"}],
    )


@pytest.mark.asyncio
async def test_latest_messages_tool_pages_newest_first_with_coverage():
    result = await invoke_tool("get_whatsapp_latest_messages", {"limit": 1}, database=indexed_db())
    assert result["total_count"] == 2, "the zero-count record is inspected, not listed"
    assert result["items"][0]["name"] == "Autumn"
    assert result["has_more"] is True and result["next_offset"] == 1
    second = await invoke_tool(
        "get_whatsapp_latest_messages", {"limit": 1, "offset": 1}, database=indexed_db()
    )
    assert second["items"][0]["name"] == "Brave"
    coverage = result["coverage"]
    assert coverage == {"indexed_records": 3, "people_records": 3, "as_of": "2026-11-02T11:00:00+00:00"}
    assert "Indexed 3 of 3 person records" in result["note"]
    assert "not live CRM data" in result["note"]


@pytest.mark.asyncio
async def test_ranking_tool_orders_by_count_desc():
    result = await invoke_tool("get_whatsapp_top_contacts", {}, database=indexed_db())
    counts = [row["whatsapp_count"] for row in result["items"]]
    assert counts == [9, 4]
    assert result["items"][0]["name"] == "Brave"


@pytest.mark.asyncio
async def test_whatsapp_tools_make_zero_live_crm_calls(monkeypatch):
    """Both tools read Mongo only: the CRM client must never be touched,
    even when it is a bomb."""
    from app.agents import chat_tools

    def explode():
        raise AssertionError("the WhatsApp tools must not reach the live CRM")

    monkeypatch.setattr(chat_tools, "_c360_client", explode)
    await invoke_tool("get_whatsapp_latest_messages", {}, database=indexed_db())
    await invoke_tool("get_whatsapp_top_contacts", {}, database=indexed_db())


@pytest.mark.asyncio
async def test_empty_index_says_so_instead_of_an_empty_list(monkeypatch):
    from app.agents import chat_tools

    monkeypatch.setattr(chat_tools, "_c360_client", lambda: (_ for _ in ()).throw(AssertionError()))
    db = FakeDb(
        customer360_whatsapp=[], customer360_leads=[],
        customer360_contacts=[], customer360_customers=[],
    )
    result = await invoke_tool("get_whatsapp_latest_messages", {}, database=db)
    assert result["total_count"] == 0
    assert result["items"] == []
    assert "index is empty" in result["note"]


# ------------------------------------------------------------------ filters


@pytest.mark.asyncio
async def test_direction_filter_returns_only_matching_rows():
    result = await invoke_tool(
        "get_whatsapp_latest_messages", {"direction": "inbound", "limit": 10}, database=indexed_db()
    )
    assert result["total_count"] == 1
    assert all(row["latest_direction"] == "Inbound" for row in result["items"])
    assert result["items"][0]["name"] == "Autumn"
    assert result["filters_applied"] == {"direction": "Inbound"}


@pytest.mark.asyncio
async def test_date_range_filter_keeps_only_rows_inside_the_window():
    inside = await invoke_tool(
        "get_whatsapp_latest_messages",
        {"date_from": "2026-11-02", "date_to": "2026-11-02", "limit": 10},
        database=indexed_db(),
    )
    assert [row["name"] for row in inside["items"]] == ["Autumn"]
    assert inside["filters_applied"]["date_from"] == "2026-11-02"
    early = await invoke_tool(
        "get_whatsapp_latest_messages", {"date_to": "2026-11-01"}, database=indexed_db()
    )
    assert [row["name"] for row in early["items"]] == ["Brave"]


# ------------------------------------------------------------ sticky pages


def test_show_me_the_next_page_is_a_next_page_confirmation():
    """The live Nov 2 phrase matched no confirmation, so the executor never
    ran and the model re-queried with a fresh default limit."""
    from app.agents.chat import _confirmation_kind

    assert _confirmation_kind("show me the next page") == "next_page"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_exact_3_turn_live_sequence_whole_listing_already_complete(monkeypatch):
    """The exact 3-turn live sequence from Nov 3:
    Turn 1: 'fetch all latest WhatsApp messages' (limit 50, 22/22, page 1 of 1, has_more=false).
    Turn 2: 'show me the next page' -> returns 'already complete' with zero tool calls,
            no false claim of later pages, no contradictory fetch.
    Turn 3: 'next page' -> returns 'already complete' with zero tool calls,
            no false claim of later pages, no incoherent page 3 of 3."""
    from app.agents import chat as chat_module
    from app.agents.chat import run_chat
    from test_chat import FakeModel
    from test_chat_data_tools import answer, business_db as real_business_db, tool_call

    class _NoEvents:
        async def emit(self, **_kwargs):
            return None

    monkeypatch.setattr(chat_module, "get_event_service", lambda: _NoEvents())

    rows = [
        {"_id": f"Lead:l{index:02d}", "record_id": f"l{index:02d}", "record_type": "Lead",
         "name": f"Lead {index}", "company": None, "whatsapp_count": 1,
         "latest_text": "ping", "latest_at": f"2026-11-01T{index % 24:02d}:00:00+00:00",
         "latest_direction": "Inbound", "indexed_at": "2026-11-02T11:00:00+00:00"}
        for index in range(1, 23)
    ]
    db = real_business_db(
        customer360_whatsapp=Collection(rows),
        customer360_leads=Collection([{"_id": row["_id"].split(":")[1]} for row in rows]),
        customer360_contacts=Collection([]),
        customer360_customers=Collection([]),
    )

    # Turn 1: whole listing fetched at limit 50
    first = FakeModel([
        tool_call("get_whatsapp_latest_messages", {"limit": 50}),
        answer("22 people have WhatsApp messages; all 22 are listed (page 1 of 1)."),
    ])
    outcome_one = await run_chat(
        "thread-whole-seq", "fetch all latest WhatsApp messages for all customers",
        database=db, model=first,
    )
    assert outcome_one["validation_replaced"] is False
    assert outcome_one["tools_used"] == ["get_whatsapp_latest_messages"]
    assert "page 1 of 1" in outcome_one["text"]

    # Turn 2: "show me the next page" -> intercepted immediately by already-complete logic
    second = FakeModel([
        answer("This model should not even need to run"),
    ])
    outcome_two = await run_chat(
        "thread-whole-seq", "show me the next page", database=db, model=second,
    )
    assert outcome_two["validation_replaced"] is False
    assert outcome_two["tools_used"] == []
    assert "already returned on the first page" in outcome_two["text"]
    assert "more records are on later pages" not in outcome_two["text"]
    assert "Page info" not in outcome_two["text"]
    assert "page 3 of 3" not in outcome_two["text"]

    # Turn 3: "next page" -> also intercepted immediately with no false claims
    third = FakeModel([
        answer("This model should not run either"),
    ])
    outcome_three = await run_chat(
        "thread-whole-seq", "next page", database=db, model=third,
    )
    assert outcome_three["validation_replaced"] is False
    assert outcome_three["tools_used"] == []
    assert "already returned on the first page" in outcome_three["text"]
    assert "more records are on later pages" not in outcome_three["text"]
    assert "Page info" not in outcome_three["text"]
    assert "page 3 of 3" not in outcome_three["text"]


@pytest.mark.asyncio
async def test_multipage_listing_chain_limit_10_sticky_offsets_and_labels(monkeypatch):
    """Multi-page listing chain at limit 10:
    Turn 1: offset 0 -> page 1 of 3 (rows 1-10)
    Turn 2: next page -> offset 10 -> page 2 of 3 (rows 11-20)
    Turn 3: next page -> offset 20 -> page 3 of 3 (rows 21-22)
    Turn 4: next page -> offset 22 -> delivered 0 -> past-end (empty page, page is None, no row-implying page number)."""
    from app.agents import chat as chat_module
    from app.agents.chat import run_chat
    from test_chat import FakeModel
    from test_chat_data_tools import answer, business_db as real_business_db, tool_call

    class _NoEvents:
        async def emit(self, **_kwargs):
            return None

    monkeypatch.setattr(chat_module, "get_event_service", lambda: _NoEvents())

    rows = [
        {"_id": f"Lead:l{index:02d}", "record_id": f"l{index:02d}", "record_type": "Lead",
         "name": f"Lead {index}", "company": None, "whatsapp_count": 1,
         "latest_text": "ping", "latest_at": f"2026-11-01T{index % 24:02d}:00:00+00:00",
         "latest_direction": "Inbound", "indexed_at": "2026-11-02T11:00:00+00:00"}
        for index in range(1, 23)
    ]
    db = real_business_db(
        customer360_whatsapp=Collection(rows),
        customer360_leads=Collection([{"_id": row["_id"].split(":")[1]} for row in rows]),
        customer360_contacts=Collection([]),
        customer360_customers=Collection([]),
    )

    # Turn 1: page 1 of 3 (limit 10, offset 0)
    m1 = FakeModel([
        tool_call("get_whatsapp_latest_messages", {"limit": 10, "offset": 0}),
        answer("Page 1 of 3: showing 10 of 22 records."),
    ])
    out1 = await run_chat("thread-chain", "fetch latest WhatsApp messages limit 10", database=db, model=m1)
    assert out1["validation_replaced"] is False
    assert out1["query_context"]["offset"] == 0
    assert out1["query_context"]["limit"] == 10
    assert out1["query_context"]["page"] == 1
    assert out1["query_context"]["pages_total"] == 3
    assert out1["query_context"]["has_more"] is True

    # Turn 2: next page -> offset 10, limit 10
    m2 = FakeModel([
        answer("Page 2 of 3: showing records 11 through 20."),
    ])
    out2 = await run_chat("thread-chain", "show me the next page", database=db, model=m2)
    assert out2["validation_replaced"] is False
    assert out2["tools_used"] == ["get_whatsapp_latest_messages"]
    assert out2["query_context"]["offset"] == 10
    assert out2["query_context"]["limit"] == 10
    assert out2["query_context"]["page"] == 2
    assert out2["query_context"]["pages_total"] == 3
    assert out2["query_context"]["has_more"] is True

    # Turn 3: next page -> offset 20, limit 10 (returns 2 records)
    m3 = FakeModel([
        answer("Page 3 of 3: showing final 2 of 22 records."),
    ])
    out3 = await run_chat("thread-chain", "next page", database=db, model=m3)
    assert out3["validation_replaced"] is False
    assert out3["tools_used"] == ["get_whatsapp_latest_messages"]
    assert out3["query_context"]["offset"] == 20
    assert out3["query_context"]["limit"] == 10
    assert out3["query_context"]["page"] == 3
    assert out3["query_context"]["pages_total"] == 3
    assert out3["query_context"]["has_more"] is False

    # Turn 4: next page -> offset 22 (past the end, returned 0)
    m4 = FakeModel([
        answer("No more rows: that offset is past the end of the 22 matching records; nothing more to fetch."),
    ])
    out4 = await run_chat("thread-chain", "next page", database=db, model=m4)
    assert out4["validation_replaced"] is False
    assert out4["tools_used"] == ["get_whatsapp_latest_messages"]
    assert out4["query_context"]["page"] is None
    assert "past the end" in out4["text"].lower()
    assert "page 3 of 3" not in out4["text"]
    assert "page 4 of 3" not in out4["text"]
    assert "more records are on later pages" not in out4["text"]


# ------------------------------------------------------- honest offers/limits


def test_whatsapp_tool_descriptions_offer_only_real_filters_and_csv_text():
    from app.agents.chat_tools import CHAT_TOOL_MAP

    latest = CHAT_TOOL_MAP["get_whatsapp_latest_messages"].description
    assert "direction ('Inbound' or 'Outbound') and a latest-message date range" in latest
    assert "CSV TEXT printed in the answer, never a file" in latest
    assert "never re-choose the page size" in latest
    ranking = CHAT_TOOL_MAP["get_whatsapp_top_contacts"].description
    assert "ONLY people with at least ONE WhatsApp message" in ranking
    assert "do not call it to fill space" in ranking


def test_prompt_pins_sticky_pages_and_honest_limit_answers():
    from app.agents.chat import CHAT_SYSTEM

    prompt = " ".join(CHAT_SYSTEM.split())
    assert "A continuation keeps the PREVIOUS page's limit" in prompt
    assert "OFFER ONLY WHAT EXISTS" in prompt
    assert "never promise a file, a download or an attachment" in prompt
    assert "never call a listing tool to fill space" in prompt
    assert "COMPANY ATTRIBUTION IN LISTS AND CSV" in prompt
    assert "NEVER-MESSAGED QUESTIONS" in prompt
    assert "PAGINATION HONESTY" in prompt


def test_company_attribution_matches_between_list_and_csv():
    """Item C: Two rows for Autumn Geier exist in the index doc:
    Lead 1: company '19th Hole Recovery'
    Lead 2: company None.
    Both list and CSV must reflect this faithfully; CSV copying is rejected."""
    from app.agents.chat import _answer_consistency_errors

    tool_results = [{
        "tool": "get_whatsapp_latest_messages",
        "result": {
            "items": [
                {"name": "Autumn Geier", "record_type": "Lead", "company": "19th Hole Recovery"},
                {"name": "Autumn Geier", "record_type": "Lead", "company": None},
            ],
            "total_count": 2,
            "has_more": False,
        },
    }]

    # Inconsistent CSV copying company onto row 2
    bad_csv = (
        'Name,Record Type,Company,Direction,Time,Message\n'
        '"Autumn Geier","Lead","19th Hole Recovery","Outbound","2026-07-28T15:33:28+00:00","Hi"\n'
        '"Autumn Geier","Lead","19th Hole Recovery","Outbound","2026-07-28T15:33:28+00:00","Hi"'
    )
    errors = _answer_consistency_errors(bad_csv, tool_results, None)
    assert any("CSV filled missing company for Autumn Geier by copying from another row" in e for e in errors)

    # Consistent CSV preserving empty company for row 2
    good_csv = (
        'Name,Record Type,Company,Direction,Time,Message\n'
        '"Autumn Geier","Lead","19th Hole Recovery","Outbound","2026-07-28T15:33:28+00:00","Hi"\n'
        '"Autumn Geier","Lead","","Outbound","2026-07-28T15:33:28+00:00","Hi"'
    )
    good_errors = _answer_consistency_errors(good_csv, tool_results, None)
    assert not any("CSV filled missing company" in e for e in good_errors)


def test_never_messaged_options_rejects_infeasible_crm_sweep():
    """Item D: An answer offering an infeasible CRM sweep is rejected by the guard."""
    from app.agents.chat import _answer_consistency_errors

    bad_answer = (
        "I cannot list customers with no messages. "
        "Attempt a CRM-side check for customers with no WhatsApp messages (may still be partial; confirm if you want me to run it)."
    )
    errors = _answer_consistency_errors(bad_answer, [], None)
    assert any("offers an infeasible CRM sweep for never-messaged contacts" in e for e in errors)


@pytest.mark.asyncio
async def test_never_messaged_question_answers_with_the_limit_not_a_page_block():
    """The live Nov 2 leak: the correct refusal ALSO pasted the ranking
    tool's page block. The honest shape is limit + coverage + options,
    with no filler call and no page info.

    Reconciles footer '1 backend lookup': when the model calls get_whatsapp_top_contacts
    to inspect index coverage (Indexed N of M), that call is a local Mongo read (0 live CRM calls)
    and is leak-free (no rows pasted)."""
    from app.agents.chat import execute_chat
    from test_chat import FakeModel, plain_answer

    model = FakeModel([plain_answer(
        "The WhatsApp index is partial - it does not yet cover every person "
        "record, so a 'never had a WhatsApp message' list cannot be built "
        "honestly. What CAN be done: (a) page the indexed people who have messages, "
        "(b) CSV text export to compare offline, (c) full re-index via the runner."
    )])
    outcome = await execute_chat(
        "which customers have never had a WhatsApp message?",
        model=model, database=indexed_db(),
    )
    assert outcome["validation_replaced"] is False
    assert "Page info" not in outcome["text"]
    assert "Brave" not in outcome["text"]
    assert "CRM-side check" not in outcome["text"]
    assert "sweep" not in outcome["text"].lower()

