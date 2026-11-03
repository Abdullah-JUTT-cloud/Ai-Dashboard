"""Tests for voice_summary computation in chat turns."""

import pytest
from app.agents.chat import build_voice_summary, validate_numeric_claims


def test_voice_summary_overdue_fixture_exact_two_sentence():
    """Overdue fixture produces exact expected 2-sentence summary."""
    tool_results = [
        {
            "tool": "get_invoices",
            "result": {
                "sum_balance": 1777747.0,
                "total_count": 270,
                "filters_applied": {"overdue_only": True},
                "items": [
                    {"customer_name": "Pure 7", "balance": 357500.0},
                ],
            },
        }
    ]
    summary = build_voice_summary(
        "There are 270 overdue invoices totaling $1,777,747.",
        tool_results,
        question="which invoices are overdue",
    )
    expected = "Overdue balance is $1,777,747 across 270 invoices. Largest is Pure 7 at $357,500."
    assert summary == expected
    assert len(summary) <= 280
    assert summary.count(".") == 2
    assert validate_numeric_claims(summary, tool_results, "", "") == []


def test_voice_summary_stale_quotes_fixture():
    """Stale-quotes fixture produces exact expected summary."""
    tool_results = [
        {
            "tool": "get_quotes",
            "result": {
                "sum_amount": 1328472.0,
                "total_count": 341,
                "filters_applied": {"stale_only": True},
                "items": [
                    {"customer_name": "Jasper United", "amount": 348000.0},
                ],
            },
        }
    ]
    summary = build_voice_summary(
        "We have 341 stale quotes worth $1,328,472.",
        tool_results,
        question="which quotes are stale",
    )
    expected = "341 stale quotes worth $1,328,472. Largest is Jasper United at $348,000."
    assert summary == expected
    assert len(summary) <= 280
    assert summary.count(".") == 2
    assert validate_numeric_claims(summary, tool_results, "", "") == []


def test_voice_summary_queue_fixture():
    """Queue fixture produces exact expected queue summary."""
    tool_results = [
        {
            "tool": "get_crm_follow_up_queue",
            "result": {
                "waiting_total": 3,
                "queue": [
                    {"name": "Customer A", "channel": "WhatsApp"},
                    {"name": "Customer B", "channel": "WhatsApp"},
                    {"name": "Customer C", "channel": "Email"},
                ],
            },
        }
    ]
    summary = build_voice_summary(
        "There are 3 customers waiting in the queue.",
        tool_results,
        question="who is waiting in the queue",
    )
    expected = "3 customers waiting in the follow-up queue."
    assert summary == expected
    assert len(summary) <= 280
    assert summary.count(".") == 1
    assert validate_numeric_claims(summary, tool_results, "", "") == []


def test_voice_summary_non_numeric_first_sentence_fallback():
    """Non-numeric answer produces the first sentence as fallback."""
    tool_results = []
    final_text = (
        "Customer records are updated nightly at midnight UTC. "
        "Please check back tomorrow morning to see newly synced entries."
    )
    summary = build_voice_summary(
        final_text,
        tool_results,
        question="when are records updated",
    )
    assert summary == "Customer records are updated nightly at midnight UTC."
    assert len(summary) <= 280
    assert validate_numeric_claims(summary, tool_results, "", "") == []


def test_voice_summary_cap_drops_to_top_one_no_mid_sentence_cut():
    """Evidence exceeding 280 chars drops to top-1 only without cutting mid-sentence."""
    long_name_1 = "Pure 7 Commercial Cleaning And Industrial Facility Solutions International Corporation"
    long_name_2 = "Jasper United Wholesale And Retail Distribution Logistics Services Global Incorporated At North America Facility"
    long_name_3 = "Super Heavy Duty Equipment Manufacturing And Supply Chain Services Worldwide LLC"
    tool_results = [
        {
            "tool": "get_invoices",
            "result": {
                "sum_balance": 1777747.0,
                "total_count": 270,
                "filters_applied": {"overdue_only": True},
                "items": [
                    {"customer_name": long_name_1, "balance": 357500.0},
                    {"customer_name": long_name_2, "balance": 300000.0},
                    {"customer_name": long_name_3, "balance": 250000.0},
                ],
            },
        }
    ]
    summary = build_voice_summary(
        "Here are the overdue invoices.",
        tool_results,
        question="which invoices are overdue",
    )
    assert len(summary) <= 280
    # Must contain top-1 item
    assert long_name_1 in summary
    assert "$357,500" in summary
    # Must NOT contain item 2 or item 3 because together they exceed 280 characters
    assert long_name_2 not in summary
    assert long_name_3 not in summary
    # Never truncate mid-sentence: ends cleanly with a period
    assert summary.endswith(".")
    assert summary.count(".") <= 2
    assert validate_numeric_claims(summary, tool_results, "", "") == []


def test_voice_summary_guard_tampered_number_falls_back():
    """A tampered summary containing a number not in evidence is rejected -> safe fallback."""
    tool_results = [
        {
            "tool": "get_invoices",
            "result": {
                "sum_balance": 1777747.0,
                "total_count": 270,
                "filters_applied": {"overdue_only": True},
                "items": [
                    {"customer_name": "Pure 7", "balance": 357500.0},
                ],
            },
        }
    ]
    tampered_text = "Overdue balance is $9,999,999 across 270 invoices."
    errors = validate_numeric_claims(tampered_text, tool_results, "", "")
    assert len(errors) > 0  # 9,999,999 is unsupported

    # If build_voice_summary is given a tampered final_text without tool backing
    summary_tampered = build_voice_summary(
        tampered_text,
        [],  # no tools to support the numbers
        question="what is overdue",
    )
    # Requirement 4: Banned string is NEVER returned; falls back to first sentence of answer
    banned_string = "The full answer is shown in text."
    assert summary_tampered != banned_string
    assert summary_tampered == "Overdue balance is $9,999,999 across 270 invoices."
    assert len(summary_tampered) > 0


def test_voice_summary_banned_line_never_returned_and_template_guard_failure():
    """Requirement 4: For fixtures including a numeric-guard-failure case,

    build_voice_summary NEVER returns the banned string and ALWAYS returns non-empty speakable text.
    """
    banned_string = "The full answer is shown in text."

    # 1. Template numeric guard failure: tool result has numbers that fail validation
    # when tampered in the template
    tool_results = [
        {
            "tool": "get_invoices",
            "result": {
                "sum_balance": 1777747.0,
                "total_count": 270,
                "filters_applied": {"overdue_only": True},
                "items": [
                    # customer with amount not in allowed numbers
                    {"customer_name": "Pure 7", "balance": 99999999.0},
                ],
            },
        }
    ]
    answer_text = "Here is the summary of your account. Overdue invoices are ready for review."
    summary = build_voice_summary(answer_text, tool_results, question="invoices")
    assert banned_string not in summary
    assert len(summary.strip()) > 0

    # 2. Complete numeric guard failure on template path:
    # Tool result where sentence_1 total amount fails validation
    tampered_tool_results = [
        {
            "tool": "get_payments",
            "result": {
                "sum_amount": 88888888.0,
                "total_count": 5,
                "items": [],
            },
        }
    ]
    answer_text_2 = "We received 5 payments today. Details are in the table below."
    summary_2 = build_voice_summary(answer_text_2, tampered_tool_results, question="payments")
    assert banned_string not in summary_2
    assert len(summary_2.strip()) > 0
    # Must be non-empty speakable text
    assert summary_2 == "Total payments are $88,888,888 across 5 payments." or summary_2 == "We received 5 payments today."

    # 3. Non-numeric answer with no sentence boundary
    no_period_answer = "This is an answer without a period at the end that describes company records"
    summary_3 = build_voice_summary(no_period_answer, [], question="records")
    assert banned_string not in summary_3
    assert summary_3 == no_period_answer
    assert len(summary_3) > 0


def test_voice_summary_never_dumps_row_list():
    """Summary never dumps a row list even when 25 items are returned."""
    twenty_five_invoices = [
        {"customer_name": f"Customer {i}", "balance": round(10000.0 - i * 100, 2)}
        for i in range(25)
    ]
    tool_results = [
        {
            "tool": "get_invoices",
            "result": {
                "sum_balance": 220000.0,
                "total_count": 25,
                "filters_applied": {"overdue_only": True},
                "items": twenty_five_invoices,
            },
        }
    ]
    summary = build_voice_summary(
        "Here are 25 invoices with their balances.",
        tool_results,
        question="which invoices are overdue",
    )
    # Must never contain markdown table syntax or newline rows
    assert "|" not in summary
    assert "\n" not in summary
    assert len(summary) <= 280
    assert summary.count(".") <= 2
    # Never more than 3 named items
    named_matches = [f"Customer {i}" for i in range(25) if f"Customer {i}" in summary]
    assert len(named_matches) <= 3
    assert validate_numeric_claims(summary, tool_results, "", "") == []
