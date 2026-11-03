"""Build/refresh the WhatsApp index (customer360_whatsapp).

Usage (from server/):
  .venv/bin/python run_whatsapp_index.py --estimate   # measure, change nothing
  .venv/bin/python run_whatsapp_index.py              # bounded pass: cache tier +
                                                      # leads active in the last
                                                      # WHATSAPP_RECENT_DAYS days
  .venv/bin/python run_whatsapp_index.py --days 30    # wider window
  .venv/bin/python run_whatsapp_index.py --cache-only # zero CRM calls
  .venv/bin/python run_whatsapp_index.py --full       # ALL people (warned first)

The CRM has no messages endpoint (probed live Nov 2: every message-like route
404s), so this index is the only honest way to answer "latest WhatsApp message
per person" and "who has the most WhatsApp messages" - and it is built from
detail payloads, one call per person. The measured numbers say what that costs:
--estimate prints the candidate count and the time it implies BEFORE anything
runs, and --full refuses to start unless --yes really means it.

A Redis lock (c360:whatsapp:lock) keeps two passes from overlapping; the
scheduler launches this on its own cadence (WHATSAPP_SYNC_MINUTES), separate
from the 5-minute signal cycle and the c360 mirror sync, so neither slows down.
The pass is resumable: a record already indexed after its last activity is
skipped, so an interrupted run continues where it stopped.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from app import db as app_db
from app.config import settings
from app.services.customer360_client import Customer360Client
from app.services.redis_client import get_redis
from app.services.whatsapp_index import SYNC_LOCK_KEY, estimate, run_whatsapp_sync


async def _claim_lock() -> tuple[bool, str | None]:
    """Same contract as the c360 sync lock: another holder means skip;
    Redis being down never blocks a manual run."""
    token = uuid.uuid4().hex
    try:
        redis = get_redis()
        if await redis.set(SYNC_LOCK_KEY, token, nx=True, ex=4 * 3600):
            return True, token
        return False, None
    except Exception:
        print("warning: Redis unreachable, running without the sync lock")
        return True, None


async def _release_lock(token: str | None) -> None:
    if not token:
        return
    try:
        redis = get_redis()
        current = await redis.get(SYNC_LOCK_KEY)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:
            await redis.delete(SYNC_LOCK_KEY)
    except Exception:
        pass


async def main(argv: list[str]) -> int:
    database = app_db.get_db()
    if "--estimate" in argv:
        days = None if "--full" in argv else int(settings.WHATSAPP_RECENT_DAYS)
        report = await estimate(database, days, int(settings.WHATSAPP_SYNC_CONCURRENCY))
        print(json.dumps(report, indent=2))
        if days is None and report["estimated_minutes"] > 240:
            print("A FULL backfill is hours of detail calls: prefer the bounded "
                  "window (default) or --cache-only. Nothing was fetched.")
        return 0

    cache_only = "--cache-only" in argv
    days: int | None = None if "--full" in argv else int(settings.WHATSAPP_RECENT_DAYS)
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    if "--full" in argv and "--yes" not in argv and not cache_only:
        report = await estimate(database, None, int(settings.WHATSAPP_SYNC_CONCURRENCY))
        print("refusing the FULL backfill without --yes; measured cost:",
              json.dumps(report))
        return 2

    may_proceed, token = await _claim_lock()
    if not may_proceed:
        print("another WhatsApp index pass is already running; skipping")
        return 0
    try:
        if cache_only or not settings.C360_EMAIL or not settings.C360_PASSWORD:
            if not cache_only:
                print("warning: C360 credentials missing, running cache-only")
            summary = await run_whatsapp_sync(
                database, None, cache_only=True,
                concurrency=int(settings.WHATSAPP_SYNC_CONCURRENCY),
            )
        else:
            async with Customer360Client() as client:
                summary = await run_whatsapp_sync(
                    database, client, days=days,
                    concurrency=int(settings.WHATSAPP_SYNC_CONCURRENCY),
                )
        print(json.dumps(summary, indent=2, default=str))
        return 0
    finally:
        await _release_lock(token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
