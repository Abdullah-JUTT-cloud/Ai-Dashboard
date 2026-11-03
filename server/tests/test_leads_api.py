"""Tests for GET /api/leads and lead categorization / partitioning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.config import settings
from app.db import get_db
from app.main import app
from app.services import processors


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _make_lead(
    lead_id: str,
    status: str = "New",
    created_at: str | None = None,
    last_activity_at: str | None = None,
    description: str | None = None,
    stage: str | None = "open",
    flag_reason: str | None = "",
    converted_at: datetime | None = None,
    company: str | None = "Test Co",
    first_name: str | None = "Jane",
    last_name: str | None = "Doe",
    email: str | None = "jane@example.com",
    phone: str | None = "+15551234567",
    source: str | None = "Web",
) -> dict:
    created = created_at or (NOW - timedelta(days=2)).isoformat()
    activity = last_activity_at or created
    return {
        "zohoLeadId": lead_id,
        "status": status,
        "createdAt": created,
        "lastActivityAt": activity,
        "description": description,
        "company": company,
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phone": phone,
        "source": source,
        "leadDesk": {
            "stage": stage,
            "flagReason": flag_reason,
            "convertedAt": converted_at,
        },
    }


def test_category_mapping_on_fixtures():
    """Verify each lead category maps accurately from real fields."""
    leads = [
        # 1. Stuck: status New, recent, description, stage open, not flagged
        _make_lead(
            "L-STUCK",
            status="New",
            created_at=(NOW - timedelta(days=3)).isoformat(),
            last_activity_at=(NOW - timedelta(days=2)).isoformat(),
            description='{"product":"mylar bag","quantity":"1000"}',
        ),
        # 2. New: status New, created <= 14d, NOT stuck (no description)
        _make_lead(
            "L-NEW",
            status="New",
            created_at=(NOW - timedelta(days=3)).isoformat(),
            last_activity_at=(NOW - timedelta(days=2)).isoformat(),
            description=None,
        ),
        # 3. Dormant: status New, activity > 14d ago
        _make_lead(
            "L-DORMANT",
            status="New",
            created_at=(NOW - timedelta(days=30)).isoformat(),
            last_activity_at=(NOW - timedelta(days=20)).isoformat(),
            description='{"product":"box"}',
        ),
        # 4. Working: status Contacted
        _make_lead(
            "L-WORKING-1",
            status="Contacted",
            created_at=(NOW - timedelta(days=10)).isoformat(),
        ),
        # 5. Working: status Contact in Future
        _make_lead(
            "L-WORKING-2",
            status="Contact in Future",
            created_at=(NOW - timedelta(days=10)).isoformat(),
        ),
        # 6. Converted: status Converted
        _make_lead(
            "L-CONVERTED-1",
            status="Converted",
            stage="converted",
            converted_at=NOW - timedelta(days=1),
        ),
        # 7. Converted: desk stage converted even if status New
        _make_lead(
            "L-CONVERTED-2",
            status="New",
            stage="converted",
            converted_at=NOW - timedelta(days=1),
        ),
        # 8. Lost: status Lost Lead
        _make_lead(
            "L-LOST-1",
            status="Lost Lead",
        ),
        # 9. Lost: disqualifying flagReason
        _make_lead(
            "L-LOST-2",
            status="New",
            flag_reason="Pakistan phone (+92)",
        ),
    ]

    counts, items = processors.partition_leads(leads, now=NOW, days=14)
    item_map = {item["lead_id"]: item for item in items}

    assert item_map["L-STUCK"]["category"] == "Stuck"
    assert item_map["L-NEW"]["category"] == "New"
    assert item_map["L-DORMANT"]["category"] == "Dormant"
    assert item_map["L-WORKING-1"]["category"] == "Working"
    assert item_map["L-WORKING-2"]["category"] == "Working"
    assert item_map["L-CONVERTED-1"]["category"] == "Converted"
    assert item_map["L-CONVERTED-2"]["category"] == "Converted"
    assert item_map["L-LOST-1"]["category"] == "Lost"
    assert item_map["L-LOST-2"]["category"] == "Lost"

    assert counts["Stuck"] == 1
    assert counts["New"] == 1
    assert counts["Dormant"] == 1
    assert counts["Working"] == 2
    assert counts["Converted"] == 2
    assert counts["Lost"] == 2
    assert counts["Other"] == 0
    assert sum(counts.values()) == len(leads)


def test_stuck_category_matches_stuck_leads_exactly():
    """Stuck category items must equal processors.stuck_leads output exactly."""
    leads = [
        _make_lead(
            f"L-{i}",
            status="New",
            created_at=(NOW - timedelta(days=i)).isoformat(),
            last_activity_at=(NOW - timedelta(days=i)).isoformat(),
            description=f'{{"product":"bag {i}","quantity":"{i * 100}"}}',
        )
        for i in range(1, 6)
    ]
    # Add non-stuck leads
    leads.append(_make_lead("L-OLD", status="New", created_at=(NOW - timedelta(days=40)).isoformat()))
    leads.append(_make_lead("L-CONV", status="Converted"))

    direct_stuck = processors.stuck_leads(leads, now=NOW, days=14)
    counts, all_items = processors.partition_leads(leads, now=NOW, days=14)
    stuck_items = [item for item in all_items if item["category"] == "Stuck"]

    assert len(stuck_items) == len(direct_stuck) == 5
    direct_ids = [row["lead_id"] for row in direct_stuck]
    partition_ids = [row["lead_id"] for row in stuck_items]
    assert partition_ids == direct_ids

    # Spot check fields match stuck_leads output
    for p_item, d_item in zip(stuck_items, direct_stuck):
        assert p_item["lead_id"] == d_item["lead_id"]
        assert p_item["contact_name"] == d_item["contact_name"]
        assert p_item["age_days"] == d_item["age_days"]
        assert p_item["product"] == d_item["product"]
        assert p_item["missing"] == d_item["missing"]


def _make_crm_lead(
    lead_id: str,
    status: str = "New",
    created_at: str | None = None,
    last_activity_at: str | None = None,
    description: str | None = None,
    company: str | None = "CRM Co",
    first_name: str | None = "CRM",
    last_name: str | None = "User",
    email: str | None = "crm@example.com",
    phone: str | None = "+15559876543",
    source: str | None = "Colorwing Newsletter",
    lead_desk: dict | None = None,
) -> dict:
    """A native CRM lead document shaped like customer360_leads (no leadDesk by default)."""
    created = created_at or (NOW - timedelta(days=1)).isoformat()
    activity = last_activity_at or created
    doc = {
        "zohoLeadId": lead_id,
        "status": status,
        "createdAt": created,
        "lastActivityAt": activity,
        "description": description or "",
        "company": company,
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phone": phone,
        "source": source,
        "synced_at": NOW.isoformat(),
    }
    if lead_desk is not None:
        doc["leadDesk"] = lead_desk
    return doc


def test_crm_shaped_fixture_partition_and_mapping():
    """Verify partition and category mapping on native CRM-shaped records (no leadDesk)."""
    crm_leads = [
        # 1. Stuck: New CRM lead with status New, recent activity, description, no leadDesk
        _make_crm_lead(
            "CRM-STUCK-1",
            status="New",
            created_at=(NOW - timedelta(hours=2)).isoformat(),
            description="Newsletter subscription received from website for m3967@hotmail.com.",
        ),
        # 2. Stuck: Attempted to Contact with quote description
        _make_crm_lead(
            "CRM-STUCK-2",
            status="Attempted to Contact",
            created_at=(NOW - timedelta(days=2)).isoformat(),
            description='{"product":"custom boxes","quantity":"500"}',
        ),
        # 3. New: status New, recent, no description
        _make_crm_lead(
            "CRM-NEW-1",
            status="New",
            created_at=(NOW - timedelta(hours=4)).isoformat(),
            description="",
        ),
        # 4. Dormant: status New, activity > 14 days ago
        _make_crm_lead(
            "CRM-DORMANT-1",
            status="New",
            created_at=(NOW - timedelta(days=40)).isoformat(),
            last_activity_at=(NOW - timedelta(days=25)).isoformat(),
            description="Old inquiry",
        ),
        # 5. Working: status Contacted
        _make_crm_lead(
            "CRM-WORKING-1",
            status="Contacted",
            created_at=(NOW - timedelta(days=5)).isoformat(),
        ),
        # 6. Converted: CRM status Converted
        _make_crm_lead(
            "CRM-CONVERTED-1",
            status="Converted",
            created_at=(NOW - timedelta(days=12)).isoformat(),
        ),
        # 7. Lost: CRM status Junk Lead
        _make_crm_lead(
            "CRM-LOST-1",
            status="Junk Lead",
            created_at=(NOW - timedelta(days=3)).isoformat(),
        ),
        # 8. Historical record with leadDesk flagReason -> Lost
        _make_crm_lead(
            "CRM-HIST-FLAG",
            status="New",
            created_at=(NOW - timedelta(days=2)).isoformat(),
            lead_desk={"flagReason": "Internal team account", "stage": "open"},
        ),
    ]

    counts, items = processors.partition_leads(crm_leads, now=NOW, days=14)
    item_map = {item["lead_id"]: item for item in items}

    # Verify no overlap and all categorized
    assert sum(counts.values()) == len(crm_leads)
    assert counts["Other"] == 0

    assert item_map["CRM-STUCK-1"]["category"] == "Stuck"
    assert item_map["CRM-STUCK-2"]["category"] == "Stuck"
    assert item_map["CRM-NEW-1"]["category"] == "New"
    assert item_map["CRM-DORMANT-1"]["category"] == "Dormant"
    assert item_map["CRM-WORKING-1"]["category"] == "Working"
    assert item_map["CRM-CONVERTED-1"]["category"] == "Converted"
    assert item_map["CRM-LOST-1"]["category"] == "Lost"
    assert item_map["CRM-HIST-FLAG"]["category"] == "Lost"

    assert counts["Stuck"] == 2
    assert counts["New"] == 1
    assert counts["Dormant"] == 1
    assert counts["Working"] == 1
    assert counts["Converted"] == 1
    assert counts["Lost"] == 2


@pytest.mark.asyncio
async def test_partition_on_live_crm_mirror():
    """On the live CRM mirror (customer360_leads), counts reconcile with CRM frontend (~19,226)."""
    try:
        db = get_db()
        rows = await db.customer360_leads.find({}, processors.LEAD_FIELDS).to_list(50_000)
        now = datetime.now(timezone.utc)

        counts, all_items = processors.partition_leads(rows, now)

        # 1. Total must equal live CRM mirror row count (~19,226)
        assert len(all_items) == len(rows) >= 19_220
        # 2. Category counts must partition the universe exactly
        assert sum(counts.values()) == len(rows)
        # 3. No uncategorized rows outside reported Other
        assert counts["Other"] == 0
        # 4. Stuck and New are positive on live CRM data
        assert counts["Stuck"] > 0
        assert counts["New"] > 0
        # 5. Every row carries a recognized category
        valid_categories = set(processors.LEAD_CATEGORIES)
        for item in all_items:
            assert item["category"] in valid_categories
    finally:
        from app import db as app_db
        await app_db.close()


def test_api_leads_endpoint_pagination_and_filtering(monkeypatch):
    """GET /api/leads supports filtering by category and standard pagination on customer360_leads."""
    from app import db as app_db
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)

    cur_now = datetime.now(timezone.utc)
    leads = [
        _make_crm_lead(f"L-{i:02d}", status="Contacted", created_at=(cur_now - timedelta(days=i)).isoformat())
        for i in range(1, 26)
    ]
    # Add a converted lead and a stuck lead
    leads.append(_make_crm_lead("L-CONV", status="Converted", created_at=(cur_now - timedelta(days=1)).isoformat()))
    leads.append(_make_crm_lead("L-STUCK-CRM", status="New", created_at=(cur_now - timedelta(days=1)).isoformat(), description="Need 1000 bags"))

    class FakeCursor:
        def __init__(self, data):
            self.data = data

        def sort(self, *_args, **_kwargs):
            return self

        def limit(self, limit_count):
            return FakeCursor(self.data[:limit_count])

        async def to_list(self, _limit):
            return list(self.data)

    class FakeDB:
        def __init__(self, data):
            self.customer360_leads = self
            self.leads = self
            self.data = data

        def find(self, *_args, **_kwargs):
            return FakeCursor(self.data)

    async def close_db():
        return None

    monkeypatch.setattr(app_db, "get_client", lambda: object())
    monkeypatch.setattr(app_db, "close", close_db)
    monkeypatch.setattr(routes, "get_db", lambda: FakeDB(leads))

    with TestClient(app) as client:
        # Page 1: default limit 10, offset 0
        resp1 = client.get("/api/leads")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["universe"] == "customer360_leads"
        assert data1["total"] == 27
        assert data1["matched_total"] == 27
        assert len(data1["items"]) == 10
        assert data1["limit"] == 10
        assert data1["offset"] == 0
        assert data1["next_offset"] == 10
        assert data1["has_more"] is True
        assert sum(data1["category_counts"].values()) == 27

        # Page 2: limit 10, offset 10
        resp2 = client.get("/api/leads?limit=10&offset=10")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["items"]) == 10
        assert data2["next_offset"] == 20
        assert data2["has_more"] is True

        # Ensure no overlap between page 1 and page 2
        ids_p1 = {item["lead_id"] for item in data1["items"]}
        ids_p2 = {item["lead_id"] for item in data2["items"]}
        assert ids_p1.isdisjoint(ids_p2)

        # Filter by category: Converted (should return exactly 1)
        resp_conv = client.get("/api/leads?category=Converted")
        assert resp_conv.status_code == 200
        data_conv = resp_conv.json()
        assert data_conv["total"] == 27  # universe total
        assert data_conv["matched_total"] == 1
        assert len(data_conv["items"]) == 1
        assert data_conv["items"][0]["lead_id"] == "L-CONV"
        assert data_conv["next_offset"] is None
        assert data_conv["has_more"] is False

        # Filter by category: Stuck (tile == tab agreement verification)
        resp_stuck = client.get("/api/leads?category=Stuck")
        assert resp_stuck.status_code == 200
        data_stuck = resp_stuck.json()
        assert data_stuck["matched_total"] == data_stuck["category_counts"]["Stuck"] == 1

        # Filter by category: case-insensitive
        resp_case = client.get("/api/leads?category=working")
        assert resp_case.status_code == 200
        data_case = resp_case.json()
        assert data_case["matched_total"] == 25

        # Invalid category returns 400
        resp_invalid = client.get("/api/leads?category=NonExistent")
        assert resp_invalid.status_code == 400
        assert "Unknown category" in resp_invalid.json()["detail"]

        # Limit > 50 returns 422
        resp_overflow = client.get("/api/leads?limit=51")
        assert resp_overflow.status_code == 422
