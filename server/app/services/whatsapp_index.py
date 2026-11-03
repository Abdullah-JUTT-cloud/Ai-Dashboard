"""The WhatsApp index: a local, queryable mirror of per-person WhatsApp messages.

Why this exists (Phase 0 probe, Nov 2, live): the CRM backend has NO messages
endpoint. GET /messages, /conversations, /threads, /whatsapp, /activities,
/timeline, /customer-360/messages, /customer-360/conversations and
/customer-360/timeline all answer HTTP 404, and the leads/contacts/customers
list rows carry identity and activity timestamps only - no message metadata.
Message bodies live inside ONE record's detail payload, in its ``timeline``
entries of type "WhatsApp". So "latest WhatsApp message per person" can only
be a built index, never a per-turn CRM walk: hundreds of ~8s cold detail calls
per chat turn is a dead path.

What is indexed (``customer360_whatsapp``, one doc per person record):

  _id                 "<Type>:<record_id>"
  record_id           the CRM record id
  record_type         "Lead" | "Contact" | "Customer" (the CRM's real types -
                      the user says "patients"; there is no patients collection)
  name, company       joined from the module mirror rows
  whatsapp_count      WhatsApp timeline entries in the detail payload
  latest_text         newest WhatsApp message, readable text (HTML stripped)
  latest_at           its timestamp (ISO)
  latest_direction    "Inbound" / "Outbound" as the CRM recorded it
  first_message_at    oldest WhatsApp entry time
  last_message_at     newest WhatsApp entry time
  indexed_at          when this doc was written
  source              "cache" (detail already mirrored) | "detail" (fetched)

A record with NO WhatsApp entries is indexed with whatsapp_count 0: that is
the evidence it was inspected, not skipped. The index is deliberately bounded
(cache re-index every run - free - plus detail for leads with recent
activity), so EVERY read must carry its coverage ("indexed N of M person
records, as_of T"); a partial index is never presented as "all".
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.services import processors

log = logging.getLogger("whatsapp_index")

WHATSAPP_COLLECTION = "customer360_whatsapp"
RUNS_COLLECTION = "customer360_whatsapp_runs"
CACHE_COLLECTION = "customer360_cache"
SYNC_LOCK_KEY = "c360:whatsapp:lock"

# module mirror -> (record_type, activity field, windowed?). Only leads carry
# a trustworthy MESSAGE-activity timestamp (lastActivityAt): contacts/customers
# updatedAt is re-stamped by any CRM touch, so filtering them by a recent
# window would re-fetch ~11k records every hour (a hammer, not a refresh) and
# still miss the older ones. The bounded pass therefore walks the leads
# window plus the free cache tier (which covers contacts and customers as
# chat opens them); --full opts into every record, once, after the estimate.
PEOPLE_MIRRORS = (
    ("customer360_leads", "Lead", "lastActivityAt", True),
    ("customer360_contacts", "Contact", "updatedAt", False),
    ("customer360_customers", "Customer", "updatedAt", False),
)

# a warm detail call measured ~8s live (cold: far worse); the estimate uses it
DETAIL_SECONDS_PER_CALL = 8.0


def _collection(database: Any, name: str) -> Any:
    try:
        return database[name]
    except (TypeError, KeyError, AttributeError):
        return getattr(database, name, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def doc_id(record_id: Any, record_type: str) -> str:
    return f"{record_type}:{record_id}"


def _timeline_rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("timeline")
    if not isinstance(rows, list) and isinstance(payload.get("data"), dict):
        rows = payload["data"].get("timeline")
    return [row for row in (rows or []) if isinstance(row, dict)]


def extract_whatsapp_stats(payload: Any) -> dict:
    """The WhatsApp block of one detail payload: count, latest message and the
    first/last message times. An empty timeline is a zero, never an error."""
    rows = [
        row for row in _timeline_rows(payload)
        if str(row.get("type") or "").casefold() == "whatsapp"
    ]
    if not rows:
        return {
            "whatsapp_count": 0,
            "latest_text": None,
            "latest_at": None,
            "latest_direction": None,
            "first_message_at": None,
            "last_message_at": None,
        }
    entries: list[tuple[Optional[datetime], Optional[str], Optional[str]]] = []
    for row in rows:
        moment = processors.parse_date(row.get("timestamp") or row.get("createdAt") or row.get("at"))
        direction = str(row.get("direction") or "").strip() or None
        text = processors.readable_text(row.get("content"), 200) or None
        entries.append((moment, direction, text))
    dated = [entry for entry in entries if entry[0] is not None]
    ordered = sorted(dated, key=lambda entry: entry[0])
    latest = ordered[-1] if ordered else entries[-1]
    return {
        "whatsapp_count": len(rows),
        "latest_text": latest[2],
        "latest_at": latest[0].isoformat() if latest[0] else None,
        "latest_direction": latest[1],
        "first_message_at": ordered[0][0].isoformat() if ordered else None,
        "last_message_at": latest[0].isoformat() if latest[0] else None,
    }


def _person_identity(record_type: str, row: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """Name and company from the module mirror row (shapes verified live by
    customer360_sync._person_fields: leads firstName/lastName/company,
    contacts firstName/lastName/accountName, customers customerName)."""
    if not isinstance(row, dict):
        return None, None
    if record_type == "Customer":
        return (row.get("customerName") or None), None
    name = " ".join(part for part in (row.get("firstName"), row.get("lastName")) if part).strip() or None
    company = row.get("company") if record_type == "Lead" else row.get("accountName")
    return name, (company or None)


async def _mirror_identity(database: Any, record_type: str, record_id: Any) -> tuple[Optional[str], Optional[str]]:
    for name, rtype, _field, _windowed in PEOPLE_MIRRORS:
        if rtype != record_type:
            continue
        collection = _collection(database, name)
        if collection is None:
            return None, None
        try:
            row = await collection.find_one({"_id": record_id})
        except Exception:
            row = None
        return _person_identity(record_type, row)
    return None, None


async def index_detail_payload(
    database: Any, record_id: Any, record_type: str, payload: Any, *, source: str
) -> dict:
    """Upsert the one-doc WhatsApp view of a detail payload."""
    stats = extract_whatsapp_stats(payload)
    name, company = await _mirror_identity(database, record_type, record_id)
    doc = {
        "_id": doc_id(record_id, record_type),
        "record_id": str(record_id),
        "record_type": record_type,
        "name": name,
        "company": company,
        "indexed_at": _now_iso(),
        "source": source,
        **stats,
    }
    collection = _collection(database, WHATSAPP_COLLECTION)
    if collection is None:
        return {"indexed": 0, "error": "collection-unavailable"}
    await collection.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
    return {"indexed": 1}


async def reindex_from_cache(database: Any) -> int:
    """Index every detail payload already mirrored in customer360_cache.
    Costs no CRM call: this is the free tier that grows with normal chat use."""
    cache = _collection(database, CACHE_COLLECTION)
    if cache is None:
        return 0
    count = 0
    try:
        rows = await cache.find({}).to_list(20_000)
    except Exception:
        log.warning("whatsapp index: cache read failed", exc_info=True)
        return 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            continue
        record_id = row.get("record_id")
        record_type = row.get("record_type")
        if not record_id or not record_type:
            continue
        result = await index_detail_payload(
            database, record_id, record_type, row["payload"], source="cache"
        )
        count += result.get("indexed", 0)
    return count


async def recent_people_candidates(database: Any, days: Optional[int]) -> list[dict]:
    """The bounded backfill universe.

    With a window (days = N): every LEAD whose lastActivityAt is inside it -
    re-checked each pass, because lead activity is a real message signal -
    plus nothing else: contacts/customers arrive through the free cache tier
    or the warned --full pass. With days = None (--full): EVERY person row
    of every type, fetched once (recheck False keeps churn from re-fetching).
    """
    cutoff = None
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    candidates: list[dict] = []
    for name, record_type, field, windowed in PEOPLE_MIRRORS:
        if cutoff is not None and not windowed:
            continue
        collection = _collection(database, name)
        if collection is None:
            continue
        query = {field: {"$gte": cutoff}} if cutoff and windowed else {}
        try:
            rows = await collection.find(query).to_list(50_000)
        except Exception:
            log.warning("whatsapp index: mirror read failed for %s", name, exc_info=True)
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("_id") is None:
                continue
            activity = str(row.get(field) or "")
            # the Mongo filter narrows the read; this confirms the window in
            # Python too, so a driver or double that ignores the query can
            # never widen the backfill past the bounded window
            if cutoff and windowed and activity < cutoff:
                continue
            candidates.append({
                "record_id": row["_id"],
                "record_type": record_type,
                "activity_at": activity,
                "recheck": bool(windowed and cutoff is not None),
            })
    return candidates


async def coverage(database: Any) -> dict:
    """How far the index reaches - the honesty block every answer must carry."""
    collection = _collection(database, WHATSAPP_COLLECTION)
    indexed = 0
    as_of: Optional[str] = None
    if collection is not None:
        try:
            indexed = await collection.count_documents({})
        except Exception:
            indexed = 0
        try:
            rows = await collection.find({}, {"_id": 0, "indexed_at": 1}).to_list(100_000)
            stamps = [str(row.get("indexed_at") or "") for row in rows if isinstance(row, dict)]
            as_of = max(stamps) if stamps else None
        except Exception:
            as_of = None
    universe = 0
    for name, _rtype, _field, _windowed in PEOPLE_MIRRORS:
        collection = _collection(database, name)
        if collection is None:
            continue
        try:
            universe += int(await collection.count_documents({}))
        except Exception:
            continue
    return {
        "indexed_records": indexed,
        "people_records": universe,
        "as_of": as_of,
    }


def coverage_label(cover: dict) -> str:
    """The one honest sentence about how far the index reaches - every read
    carries it, so a bounded index is never mistaken for the whole CRM."""
    as_of_human = cover.get("as_of_human")
    if not as_of_human and cover.get("as_of"):
        from app.services.crm_digest import format_human_timestamp
        as_of_human = format_human_timestamp(cover.get("as_of"))
    stamp = as_of_human or cover.get("as_of")
    return (
        f"Indexed {cover.get('indexed_records', 0)} of {cover.get('people_records', 0)} "
        "person records (Leads, Contacts, Customers)"
        + (f", as of {stamp}" if stamp else " (no index pass recorded yet)")
        + ". This is a local mirror, not live CRM data."
    )


async def estimate(database: Any, days: Optional[int], concurrency: int) -> dict:
    """The backfill honesty gate: measure first, report before anything runs."""
    candidates = await recent_people_candidates(database, days)
    collection = _collection(database, WHATSAPP_COLLECTION)
    already = 0
    if collection is not None:
        try:
            already = await collection.count_documents({})
        except Exception:
            already = 0
    minutes = len(candidates) / max(1, concurrency) * DETAIL_SECONDS_PER_CALL / 60.0
    return {
        "candidates": len(candidates),
        "already_indexed": already,
        "concurrency": concurrency,
        "estimated_minutes": round(minutes, 1),
        "seconds_per_detail_call_assumed": DETAIL_SECONDS_PER_CALL,
    }


async def run_whatsapp_sync(
    database: Any,
    client: Any,
    *,
    days: Optional[int] = 14,
    concurrency: int = 4,
    max_records: Optional[int] = None,
    cache_only: bool = False,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict:
    """One resumable sync pass: cache re-index (free), then detail fetches for
    the bounded recent-activity window.

    * Resumable: a record already indexed AFTER its last recorded activity is
      skipped, so an interrupted pass simply continues where it stopped.
    * Rate-limit aware: the client already backs off twice on 5xx; a failing
      record counts against a consecutive-failure budget and the pass aborts
      early (never hammers) after 8 in a row.
    * Bounded parallelism: one asyncio.Semaphore, default 4.
    """
    started_at = _now_iso()
    runs = _collection(database, RUNS_COLLECTION)
    run_doc = {"started_at": started_at, "scope": "recent" if days is not None else "full", "days": days}
    if runs is not None:
        try:
            await runs.insert_one(dict(run_doc))
        except Exception:
            log.warning("whatsapp index: run log insert failed", exc_info=True)

    cache_indexed = await reindex_from_cache(database)
    summary = {
        "started_at": started_at,
        "cache_indexed": cache_indexed,
        "detail_fetched": 0,
        "detail_failed": 0,
        "skipped_fresh": 0,
        "aborted_early": False,
    }
    if not cache_only:
        collection = _collection(database, WHATSAPP_COLLECTION)
        candidates = await recent_people_candidates(database, days)
        already: dict[str, str] = {}
        if collection is not None:
            try:
                rows = await collection.find({}, {"_id": 0, "record_id": 1, "record_type": 1, "indexed_at": 1}).to_list(100_000)
                for row in rows:
                    if isinstance(row, dict):
                        already[doc_id(row.get("record_id"), row.get("record_type") or "")] = str(row.get("indexed_at") or "")
            except Exception:
                log.warning("whatsapp index: resume scan failed", exc_info=True)
        def stale(candidate: dict) -> bool:
            indexed_at = already.get(doc_id(candidate["record_id"], candidate["record_type"]))
            if indexed_at is None:
                return True
            # a lead is re-read only when its real activity postdates the index;
            # a one-shot (full-pass) record is never re-read on CRM churn
            return bool(candidate["recheck"]) and indexed_at < candidate["activity_at"]

        todo = [candidate for candidate in candidates if stale(candidate)]
        if max_records is not None:
            todo = todo[:max_records]
        summary["candidates"] = len(candidates)
        summary["to_fetch"] = len(todo)

        semaphore = asyncio.Semaphore(max(1, concurrency))
        state = {"consecutive_failures": 0}

        async def one(candidate: dict) -> None:
            if state["consecutive_failures"] >= 8:
                return
            async with semaphore:
                try:
                    payload = await client.detail(str(candidate["record_id"]), candidate["record_type"])
                except Exception as exc:
                    state["consecutive_failures"] += 1
                    summary["detail_failed"] += 1
                    log.warning("whatsapp index: detail %s failed: %s", candidate["record_id"], exc)
                    # back off on the shared failure streak: 429/5xx storms
                    # slow the pass down instead of being hammered again
                    await sleep(min(60.0, 2.0 ** min(state["consecutive_failures"], 4)))
                    return
                state["consecutive_failures"] = 0
                await index_detail_payload(
                    database, candidate["record_id"], candidate["record_type"], payload, source="detail"
                )
                summary["detail_fetched"] += 1

        await asyncio.gather(*(one(candidate) for candidate in todo))
        if state["consecutive_failures"] >= 8:
            summary["aborted_early"] = True

    summary["finished_at"] = _now_iso()
    summary["coverage"] = await coverage(database)
    if runs is not None:
        try:
            await runs.update_one({"started_at": started_at}, {"$set": summary}, upsert=True)
        except Exception:
            log.warning("whatsapp index: run log update failed", exc_info=True)
    return summary
