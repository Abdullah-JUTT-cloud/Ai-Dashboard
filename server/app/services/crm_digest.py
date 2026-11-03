"""The CRM morning digest: yesterday at a glance, computed in Python.

Three blocks, all from data already inside this system - no CRM calls:

* payments that landed in the Zoho CRM yesterday (the business date,
  America/Chicago - the calendar the CRM's receive dates use), read
  from the local mirror with a Python total;
* who is waiting for a reply, from the latest FINISHED agent run's CRM
  Engagement section (WhatsApp counted separately, longest waits
  first within the channel order the queue tool uses);
* the biggest stalled deals, from the same run's CRM Deal Watch
  section.

A run only counts as finished when it was saved with its sections:
the "running" and "failed" documents agent_runs also carries have a
started_at but no sections, and reading them as the latest run made
every run-derived block look like a genuine zero (live Nov 2: the
briefing question answered "couldn't verify" because the newest doc
was a running stub). Missing run data is therefore reported per block
as `unavailable` - distinct from a finished run whose section really
holds zero items - so the model can write an honest partial briefing
instead of a figure it cannot support.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services import processors

BUSINESS_TZ = ZoneInfo("America/Chicago")

def format_human_timestamp(iso_str: str | None) -> str:
    """Format an ISO timestamp to human-readable 'Nov 2, 2026, 9:54 PM UTC'."""
    if not iso_str:
        return ""
    try:
        clean = str(iso_str).strip()
        dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        month = dt.strftime("%b")
        day = str(dt.day)
        year = str(dt.year)
        hour = dt.strftime("%I").lstrip("0") or "12"
        minute = dt.strftime("%M")
        ampm = dt.strftime("%p")
        tz_name = "UTC" if dt.tzinfo in (timezone.utc, None) or dt.utcoffset().total_seconds() == 0 else (dt.tzname() or "UTC")
        return f"{month} {day}, {year}, {hour}:{minute} {ampm} {tz_name}"
    except Exception:
        return str(iso_str)


# Sections only a saved (finished) run document ever carries.
_RUN_SECTION_KEYS = (
    "crm_engagement",
    "crm_deal_watch",
    "negotiation",
    "collections",
    "lead_followup",
    "master",
)


def is_finished_run(row: Any) -> bool:
    """Whether one agent_runs document is a saved run, not a stub.

    The pipeline's save node stamps finished_at/finished_epoch, writes
    the sections and carries NO status field; every stub state does:
    the run guard's "running" document, the API's "failed" document, and
    the "abandoned" document mark_stale_runs_abandoned leaves behind at
    startup - which also stamps finished_at, so a finished stamp alone
    does not prove a saved run. A section-bearing doc without a finished
    stamp (older fixtures, hand-saved runs) still counts - its sections
    are the data the blocks read.
    """
    if not isinstance(row, dict):
        return False
    if row.get("status") is not None:
        return False
    if row.get("finished_epoch") is not None or row.get("finished_at"):
        return True
    return any(key in row for key in _RUN_SECTION_KEYS)


async def latest_finished_run(database: Any) -> tuple[dict | None, dict | None]:
    """The newest finished run document, plus the newest document if it
    was a running/failed stub (None when the newest doc IS the finished
    run). Returns (None, stub-or-None) when no finished run exists."""
    latest: dict | None = None
    try:
        latest = await database.agent_runs.find_one({}, sort=[("started_at", -1)])
    except Exception:
        latest = None
    if is_finished_run(latest):
        return latest, None
    try:
        rows = await database.agent_runs.find({}, {"_id": 0}).sort("started_at", -1).to_list(10)
    except Exception:
        rows = []
    for row in rows or []:
        if is_finished_run(row):
            return row, latest
    return None, latest


def _business_yesterday(now: datetime | None = None) -> date:
    if now is not None:
        return now.astimezone(BUSINESS_TZ).date() - timedelta(days=1)
    from app.services import periods
    return periods.business_today() - timedelta(days=1)


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def _payments_block(database: Any, day: date) -> dict:
    rows: list[dict] = []
    try:
        cursor = database.customer360_payments.find(
            {
                "amountReceiveDate": {
                    "$gte": f"{day.isoformat()}T00:00:00",
                    "$lt": f"{(day + timedelta(days=1)).isoformat()}T00:00:00",
                }
            }
        )
        rows = [row for row in await cursor.to_list(20_000) if isinstance(row, dict)]
    except Exception:
        rows = []
    # the range query narrows; Python confirms the calendar date itself
    matched = [
        row
        for row in rows
        if str(row.get("amountReceiveDate") or "")[:10] == day.isoformat()
    ]
    amounts = [_money(row.get("amountReceived")) for row in matched]
    total = (
        round(sum(amount for amount in amounts if amount is not None), 2)
        if matched and all(amount is not None for amount in amounts)
        else None
    )
    top = sorted(
        matched,
        key=lambda row: -(_money(row.get("amountReceived")) or 0.0),
    )[:3]
    return {
        "date": day.isoformat(),
        "label": f"CRM payments for {day.isoformat()}",
        "status": "present" if matched else "empty",
        "count": len(matched),
        "total_received": total,
        "top": [
            {
                "customer": row.get("customerName"),
                "amount": _money(row.get("amountReceived")),
            }
            for row in top
        ],
    }


def _waiting_block(run: dict | None, previous_run: dict | None = None) -> dict:
    items = [
        item
        for item in ((run or {}).get("crm_engagement") or [])
        if isinstance(item, dict) and not item.get("error")
    ]
    ordered = sorted(
        items,
        key=lambda item: (
            item.get("channel") != "WhatsApp",
            -(item.get("waiting_days") or 0),
        ),
    )
    res = {
        "status": ("present" if items else "empty") if run is not None else "unavailable",
        "total": len(items),
        "whatsapp_total": sum(
            1 for item in items if item.get("channel") == "WhatsApp"
        ),
        "top": [
            {
                "name": item.get("contact_name"),
                "company": item.get("company"),
                "channel": item.get("channel"),
                "waiting_days": item.get("waiting_days"),
                # stored runs from before the Nov 2 strip-at-source fix
                # hold truncated HTML: never pass raw tags to the model
                "last_customer_message": processors.readable_snippet(
                    item.get("last_customer_message")
                ),
            }
            for item in ordered[:3]
        ],
    }
    if run is not None and len(items) == 0 and previous_run:
        prev_items = [
            it for it in (previous_run.get("crm_engagement") or [])
            if isinstance(it, dict) and not it.get("error")
        ]
        if prev_items:
            res["previous_run_waiting"] = len(prev_items)
            res["previous_run_as_of"] = format_human_timestamp(previous_run.get("started_at"))
            res["note"] = f"No one waiting in the latest run (previous run had {len(prev_items)} waiting)."
    return res


def _deals_block(run: dict | None) -> dict:
    items = [
        item
        for item in ((run or {}).get("crm_deal_watch") or [])
        if isinstance(item, dict) and not item.get("error")
    ]
    ordered = sorted(
        items, key=lambda item: -(_money(item.get("amount")) or 0.0)
    )
    return {
        "status": ("present" if items else "empty") if run is not None else "unavailable",
        "total": len(items),
        "top": [
            {
                "name": item.get("deal_name"),
                "account": item.get("account_name"),
                "amount": _money(item.get("amount")),
                "days_idle": item.get("days_idle"),
            }
            for item in ordered[:3]
        ],
    }


NO_FINISHED_RUN_NOTE = (
    "No finished agent run yet - the waiting queue and stalled deals are "
    "computed by an agent run. Start one from the Run agents button (or "
    "wait for the 6 AM daily run). Say this plainly; state no waiting or "
    "deal figures for an unavailable block."
)


async def build_crm_digest(
    database: Any, *, now: datetime | None = None
) -> dict:
    """The full digest for yesterday (business date).

    The run-derived blocks read the latest FINISHED run - a running or
    failed document is skipped, never served as a zero (live Nov 2). Each
    block carries status present / empty / unavailable so a partial
    briefing can name exactly what is missing.
    """
    day = _business_yesterday(now)
    run, pending = await latest_finished_run(database)
    prev_run = None
    if run is not None and database is not None and hasattr(database, "agent_runs"):
        try:
            candidates = await database.agent_runs.find(
                {"_id": {"$ne": run.get("_id")}, "status": None},
            ).sort("started_at", -1).to_list(10)
            for cand in candidates or []:
                if is_finished_run(cand):
                    prev_run = cand
                    break
        except Exception:
            prev_run = None
    run_date = str((run or {}).get("started_at") or "")[:10] or None
    raw_as_of = (run or {}).get("started_at")
    human_as_of = format_human_timestamp(raw_as_of) if raw_as_of else None
    digest = {
        "source": "crm_morning_briefing",
        "date": day.isoformat(),
        "run_date": run_date,
        "payments": await _payments_block(database, day),
        "waiting": _waiting_block(run, prev_run),
        "stalled_deals": _deals_block(run),
        "run_status": "finished" if run is not None else "no_finished_run",
        "as_of_run": raw_as_of,
        "as_of_run_human": human_as_of,
        "as_of": raw_as_of,
        "as_of_human": human_as_of,
    }
    if run is None:
        digest["note"] = NO_FINISHED_RUN_NOTE
    if pending is not None:
        digest["newer_run_status"] = str(pending.get("status") or "running")
        if run is not None:
            digest["note"] = (
                "A newer agent run is still " + digest["newer_run_status"] +
                "; this briefing is from the latest finished run."
            )
    return digest
