"""Tests for ``PATCH /cards/bulk-rows`` — one patch per card, one transaction.

The endpoint exists because the MCP ``update_cards_bulk`` tool used to send one
``PATCH /cards/bulk`` per distinct patch: each committed on its own, results
were indexed per request, and the commit response kept only the last group.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.permissions import VIEWER_PERMISSIONS
from app.models.card import Card
from app.models.event import Event
from tests.conftest import (
    auth_headers,
    create_card,
    create_card_type,
    create_role,
    create_user,
)

RADAR = {
    "stages": [
        {
            "key": "evaluating",
            "label": "Evaluating",
            "color": "#9e9e9e",
            "semantic": "pre_operational",
        },
        {"key": "emerging", "label": "Emerging", "color": "#1976d2", "semantic": "pre_operational"},
        {"key": "core", "label": "Core", "color": "#2e7d32", "semantic": "operational"},
        {"key": "sunset", "label": "Sunset", "color": "#ed6c02", "semantic": "retiring"},
    ]
}

STATUS_COUNT = {
    "would_update": "would_update",
    "updated": "updated",
    "unchanged": "unchanged",
    "failed": "error",
    "not_applied": "not_applied",
}


@pytest.fixture
async def env(db):
    await create_role(db, key="admin", label="Admin", permissions={"*": True})
    await create_role(db, key="viewer", label="Viewer", permissions=VIEWER_PERMISSIONS)
    await create_card_type(
        db,
        key="Application",
        label="Application",
        lifecycle_config=RADAR,
        has_hierarchy=True,
        fields_schema=[
            {"section": "General", "fields": [{"key": "owner", "label": "Owner", "type": "text"}]}
        ],
    )
    # No stages of its own: "core" is not a stage here.
    await create_card_type(db, key="Interface", label="Interface")
    admin = await create_user(db, email="admin@test.com", role="admin")
    viewer = await create_user(db, email="viewer@test.com", role="viewer")
    return {"admin": admin, "viewer": viewer}


async def _apps(db, count, **kwargs):
    return [
        await create_card(db, card_type="Application", name=f"App {i}", **kwargs)
        for i in range(count)
    ]


async def _send(client, user, rows, **body):
    return await client.patch(
        "/api/v1/cards/bulk-rows",
        json={"rows": rows, **body},
        headers=auth_headers(user),
    )


def _stage_rows(cards, stage):
    return [{"card_id": str(c.id), "updates": {"lifecycle_stage": stage}} for c in cards]


async def _stages(db, cards):
    out = {}
    for card in cards:
        await db.refresh(card)
        out[str(card.id)] = card.lifecycle_stage
    return out


async def _event_count(db, cards):
    result = await db.execute(
        select(func.count()).select_from(Event).where(Event.card_id.in_([c.id for c in cards]))
    )
    return result.scalar_one()


def _assert_reconciles(data, rows):
    """One result per input row, by position and identity, and counts that
    add up to the results they summarise."""
    results = data["results"]
    assert [(r["row_index"], r["card_id"]) for r in results] == [
        (i, row["card_id"]) for i, row in enumerate(rows)
    ]
    for key, status in STATUS_COUNT.items():
        assert data[key] == sum(1 for r in results if r["status"] == status), key
    assert data["total"] == len(rows) == sum(data[key] for key in STATUS_COUNT)


class TestMixedPatches:
    async def test_dry_run_reports_each_row_at_its_input_index(self, client, db, env):
        cards = await _apps(db, 10)
        rows = _stage_rows(cards, "core")
        rows[4]["updates"]["lifecycle_stage"] = "emerging"

        resp = await _send(client, env["admin"], rows, dry_run=True)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        _assert_reconciles(data, rows)
        assert data["dry_run"] is True
        assert data["committed"] is False
        assert data["would_update"] == 10
        assert [r["after"]["lifecycle_stage"] for r in data["results"]] == [
            "emerging" if i == 4 else "core" for i in range(10)
        ]
        assert all(r["before"] == {"lifecycle_stage": None} for r in data["results"])
        assert set((await _stages(db, cards)).values()) == {None}
        assert await _event_count(db, cards) == 0

    async def test_commit_writes_every_group_and_reports_every_row(self, client, db, env):
        cards = await _apps(db, 10)
        rows = _stage_rows(cards, "core")
        rows[4]["updates"]["lifecycle_stage"] = "emerging"

        resp = await _send(client, env["admin"], rows)

        data = resp.json()
        _assert_reconciles(data, rows)
        assert data["committed"] is True
        assert data["updated"] == 10
        assert {r["status"] for r in data["results"]} == {"updated"}
        assert data["error"] is None
        assert await _stages(db, cards) == {
            str(c.id): ("emerging" if i == 4 else "core") for i, c in enumerate(cards)
        }
        # One history entry per changed card, as the single-patch endpoint writes.
        assert await _event_count(db, cards) == 10

    async def test_commit_records_who_changed_only_the_changed_cards(self, client, db, env):
        cards = await _apps(db, 3, lifecycle_stage="core")
        cards[1].lifecycle_stage = "sunset"
        await db.flush()
        rows = _stage_rows(cards, "core")

        preview = (await _send(client, env["admin"], rows, dry_run=True)).json()
        assert [r["status"] for r in preview["results"]] == [
            "unchanged",
            "would_update",
            "unchanged",
        ]
        _assert_reconciles(preview, rows)

        data = (await _send(client, env["admin"], rows)).json()

        _assert_reconciles(data, rows)
        assert [r["status"] for r in data["results"]] == ["unchanged", "updated", "unchanged"]
        assert (data["updated"], data["unchanged"]) == (1, 2)
        assert data["results"][0]["before"] == {} and data["results"][0]["after"] == {}
        for card in cards:
            await db.refresh(card)
        assert [c.updated_by for c in cards] == [None, env["admin"].id, None]
        assert await _event_count(db, cards) == 1

    async def test_commit_breaks_the_approval_of_a_changed_card(self, client, db, env):
        (card,) = await _apps(db, 1, approval_status="APPROVED")

        data = (await _send(client, env["admin"], _stage_rows([card], "core"))).json()

        assert data["committed"] is True
        await db.refresh(card)
        assert card.approval_status == "BROKEN"


class TestAllOrNothing:
    async def test_an_invalid_row_in_a_later_group_is_named_on_dry_run(self, client, db, env):
        cards = await _apps(db, 5)
        rows = _stage_rows(cards[:4], "sunset") + _stage_rows(cards[4:], "no-such-stage")

        data = (await _send(client, env["admin"], rows, dry_run=True)).json()

        _assert_reconciles(data, rows)
        assert [r["status"] for r in data["results"]] == ["would_update"] * 4 + ["error"]
        assert data["failed"] == 1
        assert "no-such-stage" in data["results"][4]["error"]
        assert data["results"][4]["after"] == {}

    async def test_an_invalid_row_in_a_later_group_writes_nothing(self, client, db, env):
        cards = await _apps(db, 5)
        rows = _stage_rows(cards[:4], "sunset") + _stage_rows(cards[4:], "no-such-stage")

        resp = await _send(client, env["admin"], rows)

        assert resp.status_code == 200
        data = resp.json()
        _assert_reconciles(data, rows)
        assert data["committed"] is False
        assert data["updated"] == 0
        assert [r["status"] for r in data["results"]] == ["not_applied"] * 4 + ["error"]
        assert "1 row(s) were rejected" in data["error"]
        assert set((await _stages(db, cards)).values()) == {None}
        assert await _event_count(db, cards) == 0

    async def test_a_rejection_is_pinned_to_the_row_that_caused_it(self, client, db, env):
        """Three rows share one patch; only the Interface has no "core" stage."""
        apps = await _apps(db, 2)
        interface = await create_card(db, card_type="Interface", name="Feed")
        cards = [apps[0], interface, apps[1]]
        rows = _stage_rows(cards, "core")

        data = (await _send(client, env["admin"], rows, dry_run=True)).json()

        _assert_reconciles(data, rows)
        assert [r["status"] for r in data["results"]] == ["would_update", "error", "would_update"]
        assert "Interface" in data["results"][1]["error"]
        assert data["results"][2]["after"] == {"lifecycle_stage": "core"}

    async def test_a_rejection_only_the_group_provokes_is_reported_on_all_its_rows(
        self, client, db, env
    ):
        """Two same-named cards moved under one parent clash with each other;
        neither is wrong on its own."""
        parent = await create_card(db, card_type="Application", name="Parent")
        other_parent = await create_card(db, card_type="Application", name="Elsewhere")
        first = await create_card(db, card_type="Application", name="Twin")
        second = await create_card(
            db, card_type="Application", name="Twin", parent_id=other_parent.id
        )
        rows = [
            {"card_id": str(c.id), "updates": {"parent_id": str(parent.id)}}
            for c in (first, second)
        ]

        data = (await _send(client, env["admin"], rows, dry_run=True)).json()

        _assert_reconciles(data, rows)
        assert [r["status"] for r in data["results"]] == ["error", "error"]
        assert data["results"][0]["error"] == data["results"][1]["error"]
        assert "Twin" in data["results"][0]["error"]

    async def test_a_clash_found_while_writing_rolls_back_the_earlier_group(self, client, db, env):
        """The two moves are in different groups, so each validates alone; the
        second only fails once the first has been written."""
        parent = await create_card(db, card_type="Application", name="Parent")
        other_parent = await create_card(db, card_type="Application", name="Elsewhere")
        first = await create_card(db, card_type="Application", name="Twin")
        second = await create_card(
            db, card_type="Application", name="Twin", parent_id=other_parent.id
        )
        # The handler rolls back, which expires these objects; keep the
        # fixtures on the far side of that and the ids in hand.
        await db.commit()
        first_id, second_id = first.id, second.id
        rows = [
            {
                "card_id": str(first_id),
                "updates": {"parent_id": str(parent.id), "description": "first"},
            },
            {
                "card_id": str(second_id),
                "updates": {"parent_id": str(parent.id), "description": "second"},
            },
        ]

        preview = (await _send(client, env["admin"], rows, dry_run=True)).json()
        assert preview["would_update"] == 2

        data = (await _send(client, env["admin"], rows)).json()

        _assert_reconciles(data, rows)
        assert data["committed"] is False
        assert [r["status"] for r in data["results"]] == ["not_applied", "not_applied"]
        assert data["error"].startswith("Nothing was written:")
        assert data["results"][0]["after"] == {}
        fresh = (
            await db.execute(
                select(Card).where(Card.id == first_id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh.parent_id is None
        assert fresh.description is None
        events = await db.execute(
            select(func.count()).select_from(Event).where(Event.card_id.in_([first_id, second_id]))
        )
        assert events.scalar_one() == 0


class TestRowChecks:
    async def test_duplicate_card_ids_are_rejected_on_every_row_involved(self, client, db, env):
        cards = await _apps(db, 2)
        rows = _stage_rows([cards[0], cards[1], cards[0]], "core")

        data = (await _send(client, env["admin"], rows)).json()

        assert data["committed"] is False
        assert [r["status"] for r in data["results"]] == ["error", "not_applied", "error"]
        assert "rows [0, 2]" in data["results"][0]["error"]
        assert data["results"][0]["error"] == data["results"][2]["error"]
        assert set((await _stages(db, cards)).values()) == {None}

    async def test_unknown_and_malformed_card_ids_fail_their_own_rows(self, client, db, env):
        (card,) = await _apps(db, 1)
        rows = [
            {"card_id": str(card.id), "updates": {"lifecycle_stage": "core"}},
            {"card_id": str(uuid.uuid4()), "updates": {"lifecycle_stage": "core"}},
            {"card_id": "not-a-uuid", "updates": {"lifecycle_stage": "core"}},
        ]

        data = (await _send(client, env["admin"], rows, dry_run=True)).json()

        assert [r["card_id"] for r in data["results"]] == [r["card_id"] for r in rows]
        assert [r["status"] for r in data["results"]] == ["would_update", "error", "error"]
        assert data["results"][1]["error"] == "Card not found"
        assert data["results"][2]["error"] == "card_id is not a valid UUID"
        assert (data["would_update"], data["failed"], data["total"]) == (1, 2, 3)

    async def test_a_row_with_nothing_to_update_is_an_error(self, client, db, env):
        (card,) = await _apps(db, 1)
        rows = [{"card_id": str(card.id), "updates": {"strict_attributes": True}}]

        data = (await _send(client, env["admin"], rows, dry_run=True)).json()

        assert data["results"][0]["status"] == "error"
        assert data["results"][0]["error"] == "No updatable fields in this row"

    @pytest.mark.parametrize("body, row_flag", [({"strict_attributes": True}, False), ({}, True)])
    async def test_strict_attributes_rejects_undeclared_keys(self, client, db, env, body, row_flag):
        (card,) = await _apps(db, 1)
        updates = {"attributes": {"owner": "Ann", "ownr": "typo"}}
        if row_flag:
            updates["strict_attributes"] = True
        rows = [{"card_id": str(card.id), "updates": updates}]

        data = (await _send(client, env["admin"], rows, **body)).json()

        assert data["committed"] is False
        assert data["results"][0]["status"] == "error"
        assert "ownr" in data["results"][0]["error"]
        await db.refresh(card)
        assert card.attributes == {}

    async def test_without_strict_attributes_undeclared_keys_are_stored(self, client, db, env):
        (card,) = await _apps(db, 1)
        rows = [{"card_id": str(card.id), "updates": {"attributes": {"ownr": "typo"}}}]

        data = (await _send(client, env["admin"], rows)).json()

        assert data["committed"] is True
        await db.refresh(card)
        assert card.attributes["ownr"] == "typo"

    async def test_an_empty_request_is_refused(self, client, db, env):
        resp = await _send(client, env["admin"], [])
        assert resp.status_code == 422


class TestPermissions:
    async def test_bulk_edit_permission_is_required(self, client, db, env):
        cards = await _apps(db, 1)

        resp = await _send(client, env["viewer"], _stage_rows(cards, "core"))

        assert resp.status_code == 403
        assert set((await _stages(db, cards)).values()) == {None}

    async def test_anonymous_is_refused(self, client, db, env):
        cards = await _apps(db, 1)
        resp = await client.patch(
            "/api/v1/cards/bulk-rows", json={"rows": _stage_rows(cards, "core")}
        )
        assert resp.status_code == 401


class TestSinglePatchEndpoint:
    """``PATCH /cards/bulk`` shares the staging code and must behave as before."""

    async def test_dry_run_row_index_is_the_position_in_ids(self, client, db, env):
        cards = await _apps(db, 3)
        cards[0].lifecycle_stage = "core"
        await db.flush()

        resp = await client.patch(
            "/api/v1/cards/bulk",
            json={
                "ids": [str(c.id) for c in cards],
                "updates": {"lifecycle_stage": "core"},
                "dry_run": True,
            },
            headers=auth_headers(env["admin"]),
        )

        data = resp.json()
        # The first card is already at "core", so only rows 1 and 2 change.
        assert sorted((r["row_index"], r["card_id"]) for r in data["results"]) == [
            (1, str(cards[1].id)),
            (2, str(cards[2].id)),
        ]
        assert data["would_update"] == 2
        assert (await _stages(db, cards))[str(cards[1].id)] is None

    async def test_commit_still_returns_the_cards(self, client, db, env):
        cards = await _apps(db, 2, approval_status="APPROVED")

        resp = await client.patch(
            "/api/v1/cards/bulk",
            json={"ids": [str(c.id) for c in cards], "updates": {"lifecycle_stage": "core"}},
            headers=auth_headers(env["admin"]),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert sorted(c["id"] for c in body) == sorted(str(c.id) for c in cards)
        assert {c["lifecycle_stage"] for c in body} == {"core"}
        assert {c["approval_status"] for c in body} == {"BROKEN"}
