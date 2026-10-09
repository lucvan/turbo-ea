"""Tests for the mutation-batch CRUD + change-history endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.mutation_batch import MutationBatch
from tests.conftest import auth_headers, create_user


@pytest.mark.asyncio
async def test_open_dry_run_batch_below_threshold_no_token(client, admin_user):
    resp = await client.post(
        "/api/v1/mutation-batches?row_count=5",
        json={"tool_name": "create_cards_bulk", "dry_run": True},
        headers=auth_headers(admin_user),
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["tool_name"] == "create_cards_bulk"
    assert payload["dry_run"] is True
    assert payload["committed_at"] is None
    # 5 rows < default threshold (20) → no token issued
    assert payload["confirm_token"] is None


HASH = "a" * 64
OTHER_HASH = "b" * 64


async def _open(client, user, *, row_count, dry_run, tool="update_cards_bulk", **body):
    return await client.post(
        f"/api/v1/mutation-batches?row_count={row_count}",
        json={"tool_name": tool, "dry_run": dry_run, **body},
        headers=auth_headers(user),
    )


async def _close(client, user, batch_id, summary=None):
    return await client.post(
        f"/api/v1/mutation-batches/{batch_id}/commit",
        json={"summary": summary} if summary is not None else {},
        headers=auth_headers(user),
    )


async def _previewed(client, user, *, row_count=30, tool="update_cards_bulk", **body):
    """A closed dry-run batch above the threshold: ``(batch_id, token)``."""
    body.setdefault("payload_hash", HASH)
    opened = await _open(client, user, row_count=row_count, dry_run=True, tool=tool, **body)
    assert opened.status_code == 201, opened.text
    payload = opened.json()
    closed = await _close(client, user, payload["id"], {"status": "previewed"})
    assert closed.status_code == 200, closed.text
    return payload["id"], payload["confirm_token"]


async def _batch_count(db):
    result = await db.execute(select(func.count()).select_from(MutationBatch))
    return result.scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count, issued", [(19, False), (20, False), (21, True), (40, True)])
async def test_dry_run_is_issued_a_token_only_above_the_threshold(
    client, admin_user, row_count, issued
):
    resp = await _open(client, admin_user, row_count=row_count, dry_run=True, payload_hash=HASH)
    payload = resp.json()
    assert (payload["confirm_token"] is not None) is issued
    assert (payload["confirm_token_expires_at"] is not None) is issued
    if issued:
        assert len(payload["confirm_token"]) >= 16
        assert payload["summary"] == {
            "confirmation": {"row_count": row_count, "payload_hash": HASH}
        }
    else:
        assert payload["summary"] is None


@pytest.mark.asyncio
async def test_token_expiry_is_fifteen_minutes_after_the_dry_run(client, admin_user):
    payload = (await _open(client, admin_user, row_count=30, dry_run=True)).json()
    created = datetime.fromisoformat(payload["created_at"])
    expires = datetime.fromisoformat(payload["confirm_token_expires_at"])
    assert expires - created == timedelta(minutes=15)


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [21, 40])
async def test_closing_a_dry_run_above_the_threshold_needs_no_token(client, admin_user, row_count):
    """The reported failure: the preview's own close call was held to the
    rule meant for a write, so every dry-run above the threshold ended in a
    400 after the preview had been computed."""
    opened = (await _open(client, admin_user, row_count=row_count, dry_run=True)).json()
    assert opened["confirm_token"]

    resp = await _close(
        client, admin_user, opened["id"], {"status": "previewed", "would_update": row_count}
    )

    assert resp.status_code == 200, resp.text
    closed = resp.json()
    assert closed["dry_run"] is True
    assert closed["committed_at"] is not None
    assert closed["summary"]["status"] == "previewed"
    assert closed["summary"]["would_update"] == row_count
    # A closed batch no longer shows the token: only the dry-run's caller has it.
    assert closed["confirm_token"] is None
    assert closed["confirm_token_expires_at"] is None


@pytest.mark.asyncio
async def test_the_caller_can_ask_for_a_token_below_the_default_threshold(client, admin_user):
    resp = await _open(client, admin_user, row_count=5, dry_run=True, require_confirmation=True)
    assert resp.json()["confirm_token"] is not None


@pytest.mark.asyncio
async def test_the_caller_can_waive_confirmation_and_the_waiver_is_recorded(client, admin_user):
    preview = await _open(
        client, admin_user, row_count=30, dry_run=True, require_confirmation=False
    )
    assert preview.json()["confirm_token"] is None

    write = await _open(client, admin_user, row_count=30, dry_run=False, require_confirmation=False)

    assert write.status_code == 201
    assert write.json()["summary"] == {"confirmation": {"waived": True, "row_count": 30}}


@pytest.mark.asyncio
async def test_a_small_write_records_no_confirmation(client, admin_user):
    write = await _open(client, admin_user, row_count=20, dry_run=False)
    assert write.status_code == 201
    assert write.json()["summary"] is None


@pytest.mark.asyncio
async def test_write_above_the_threshold_without_a_token_is_refused(client, admin_user, db):
    before = await _batch_count(db)

    resp = await _open(client, admin_user, row_count=21, dry_run=False, payload_hash=HASH)

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_required"
    assert "21 rows" in resp.json()["detail"]["message"]
    # Refused before a batch exists: there is nothing for a write to hang off.
    assert await _batch_count(db) == before


@pytest.mark.asyncio
async def test_write_with_the_dry_runs_token_opens_and_spends_it(client, admin_user):
    preview_id, token = await _previewed(client, admin_user)

    write = await _open(
        client, admin_user, row_count=30, dry_run=False, payload_hash=HASH, confirm_token=token
    )

    assert write.status_code == 201, write.text
    body = write.json()
    assert body["dry_run"] is False
    assert body["confirm_token"] is None
    assert body["summary"]["confirmation"] == {
        "preview_batch_id": preview_id,
        "row_count": 30,
        "payload_hash": HASH,
    }
    preview = await client.get(
        f"/api/v1/mutation-batches/{preview_id}", headers=auth_headers(admin_user)
    )
    spent = preview.json()["summary"]["confirmation"]
    assert spent["redeemed_by_batch_id"] == body["id"]
    assert spent["redeemed_at"]
    assert preview.json()["summary"]["status"] == "previewed"


@pytest.mark.asyncio
async def test_a_token_works_once(client, admin_user, db):
    _, token = await _previewed(client, admin_user)
    args = {"row_count": 30, "dry_run": False, "payload_hash": HASH, "confirm_token": token}
    assert (await _open(client, admin_user, **args)).status_code == 201
    before = await _batch_count(db)

    replay = await _open(client, admin_user, **args)

    assert replay.status_code == 400
    assert replay.json()["detail"]["code"] == "confirm_token_used"
    assert await _batch_count(db) == before


@pytest.mark.asyncio
async def test_an_unknown_token_is_refused(client, admin_user):
    await _previewed(client, admin_user)
    resp = await _open(
        client,
        admin_user,
        row_count=30,
        dry_run=False,
        payload_hash=HASH,
        confirm_token="not-the-right-token",
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"


@pytest.mark.asyncio
async def test_a_presented_token_is_checked_even_below_the_threshold(client, admin_user):
    resp = await _open(client, admin_user, row_count=3, dry_run=False, confirm_token="made-up")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"


@pytest.mark.asyncio
async def test_another_users_token_is_refused_like_an_unknown_one(client, admin_user, db):
    other = await create_user(db, email="other-previewer@test.com", role="admin")
    _, token = await _previewed(client, other)

    resp = await _open(
        client, admin_user, row_count=30, dry_run=False, payload_hash=HASH, confirm_token=token
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"
    # Still good for the user it was issued to.
    theirs = await _open(
        client, other, row_count=30, dry_run=False, payload_hash=HASH, confirm_token=token
    )
    assert theirs.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"tool": "archive_cards"},
        {"row_count": 31},
        {"payload_hash": OTHER_HASH},
        {"payload_hash": None},
    ],
    ids=["tool", "row_count", "payload", "payload_missing"],
)
async def test_a_token_only_commits_the_operation_that_was_previewed(client, admin_user, change):
    _, token = await _previewed(client, admin_user)
    args = {
        "row_count": 30,
        "dry_run": False,
        "tool": "update_cards_bulk",
        "payload_hash": HASH,
        "confirm_token": token,
        **change,
    }
    if args["payload_hash"] is None:
        del args["payload_hash"]

    resp = await _open(client, admin_user, **args)

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_mismatch"
    # A mismatch does not spend the token: the previewed operation still commits.
    ok = await _open(
        client, admin_user, row_count=30, dry_run=False, payload_hash=HASH, confirm_token=token
    )
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_a_preview_without_a_payload_hash_binds_tool_and_row_count_only(client, admin_user):
    opened = (await _open(client, admin_user, row_count=30, dry_run=True)).json()
    await _close(client, admin_user, opened["id"], {"status": "previewed"})

    resp = await _open(
        client,
        admin_user,
        row_count=30,
        dry_run=False,
        payload_hash=OTHER_HASH,
        confirm_token=opened["confirm_token"],
    )

    assert resp.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("age_minutes, accepted", [(14, True), (16, False)])
async def test_a_token_expires_fifteen_minutes_after_the_dry_run(
    client, admin_user, db, age_minutes, accepted
):
    preview_id, token = await _previewed(client, admin_user)
    batch = (
        await db.execute(select(MutationBatch).where(MutationBatch.id == uuid.UUID(preview_id)))
    ).scalar_one()
    batch.created_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    await db.flush()

    resp = await _open(
        client, admin_user, row_count=30, dry_run=False, payload_hash=HASH, confirm_token=token
    )

    if accepted:
        assert resp.status_code == 201
    else:
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "confirm_token_expired"


@pytest.mark.asyncio
async def test_a_dry_run_that_was_never_closed_authorises_nothing(client, admin_user):
    opened = (await _open(client, admin_user, row_count=30, dry_run=True, payload_hash=HASH)).json()

    resp = await _open(
        client,
        admin_user,
        row_count=30,
        dry_run=False,
        payload_hash=HASH,
        confirm_token=opened["confirm_token"],
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"
    assert "did not complete" in resp.json()["detail"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "errored"])
async def test_a_dry_run_that_reported_failures_voids_its_token(client, admin_user, status):
    opened = (await _open(client, admin_user, row_count=30, dry_run=True, payload_hash=HASH)).json()
    closed = await _close(client, admin_user, opened["id"], {"status": status, "failed": 1})
    assert closed.json()["summary"]["confirmation"]["void"] is True

    resp = await _open(
        client,
        admin_user,
        row_count=30,
        dry_run=False,
        payload_hash=HASH,
        confirm_token=opened["confirm_token"],
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"


@pytest.mark.asyncio
async def test_a_write_batch_token_cannot_be_used_as_a_confirm_token(client, admin_user, db):
    """Only a dry-run batch's token confirms anything."""
    batch = MutationBatch(
        tool_name="update_cards_bulk",
        actor_user_id=admin_user.id,
        origin="mcp",
        dry_run=False,
        confirm_token="token-on-a-write-batch",
        summary={"confirmation": {"row_count": 30, "payload_hash": HASH}},
        committed_at=datetime.now(timezone.utc),
    )
    db.add(batch)
    await db.flush()

    resp = await _open(
        client,
        admin_user,
        row_count=30,
        dry_run=False,
        payload_hash=HASH,
        confirm_token="token-on-a-write-batch",
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "confirm_token_invalid"


@pytest.mark.asyncio
async def test_closing_cannot_overwrite_what_the_token_is_bound_to(client, admin_user):
    opened = (await _open(client, admin_user, row_count=30, dry_run=True, payload_hash=HASH)).json()

    closed = await _close(
        client,
        admin_user,
        opened["id"],
        {"status": "previewed", "confirmation": {"row_count": 1, "payload_hash": OTHER_HASH}},
    )

    assert closed.json()["summary"]["confirmation"] == {"row_count": 30, "payload_hash": HASH}


@pytest.mark.asyncio
async def test_closing_without_a_summary_keeps_the_binding(client, admin_user):
    opened = (await _open(client, admin_user, row_count=30, dry_run=True, payload_hash=HASH)).json()
    closed = await _close(client, admin_user, opened["id"])
    assert closed.json()["summary"] == {"confirmation": {"row_count": 30, "payload_hash": HASH}}


@pytest.mark.asyncio
async def test_a_failed_write_keeps_its_summary_and_is_not_voided(client, admin_user):
    opened = (await _open(client, admin_user, row_count=3, dry_run=False)).json()
    closed = await _close(client, admin_user, opened["id"], {"status": "failed", "error": "boom"})
    assert closed.json()["summary"] == {"status": "failed", "error": "boom"}
    assert closed.json()["dry_run"] is False


@pytest.mark.asyncio
async def test_commit_idempotent_rejection(client, admin_user):
    open_resp = await client.post(
        "/api/v1/mutation-batches?row_count=1",
        json={"tool_name": "update_cards_bulk", "dry_run": False},
        headers=auth_headers(admin_user),
    )
    batch_id = open_resp.json()["id"]
    first = await client.post(
        f"/api/v1/mutation-batches/{batch_id}/commit",
        json={},
        headers=auth_headers(admin_user),
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/mutation-batches/{batch_id}/commit",
        json={},
        headers=auth_headers(admin_user),
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_cross_actor_commit_forbidden(client, admin_user, db, member_role):
    open_resp = await client.post(
        "/api/v1/mutation-batches?row_count=1",
        json={"tool_name": "update_cards_bulk", "dry_run": False},
        headers=auth_headers(admin_user),
    )
    batch_id = open_resp.json()["id"]
    other = await create_user(db, email="other-actor@test.com", role="member")
    resp = await client.post(
        f"/api/v1/mutation-batches/{batch_id}/commit",
        json={},
        headers=auth_headers(other),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_batches_requires_admin_events(client, admin_user, db, member_role):
    # Open one batch as admin so the list is non-empty
    await client.post(
        "/api/v1/mutation-batches?row_count=1",
        json={"tool_name": "create_cards_bulk", "dry_run": False},
        headers=auth_headers(admin_user),
    )

    # Member without admin.events → 403
    member = await create_user(db, email="non-auditor@test.com", role="member")
    resp = await client.get("/api/v1/mutation-batches", headers=auth_headers(member))
    assert resp.status_code == 403

    # Admin → 200 with at least the batch we opened. The list endpoint
    # returns a `{items, total, page, page_size}` envelope so the audit-
    # log UI can paginate; assert against `items`.
    resp = await client.get("/api/v1/mutation-batches", headers=auth_headers(admin_user))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 50
    assert payload["total"] >= 1
    assert len(payload["items"]) >= 1


@pytest.mark.asyncio
async def test_events_stamped_with_batch_id_via_header(client, admin_user, app_card_type):
    """End-to-end: open a batch, perform a write while echoing the
    ``X-Turbo-EA-Batch`` header, and confirm the resulting event row
    carries the batch id."""
    open_resp = await client.post(
        "/api/v1/mutation-batches?row_count=1",
        json={"tool_name": "create_cards_bulk", "dry_run": False},
        headers={**auth_headers(admin_user), "X-Turbo-EA-Origin": "mcp"},
    )
    batch_id = open_resp.json()["id"]

    create_resp = await client.post(
        "/api/v1/cards",
        json={
            "type": "Application",
            "name": "Batched App",
        },
        headers={
            **auth_headers(admin_user),
            "X-Turbo-EA-Origin": "mcp",
            "X-Turbo-EA-Batch": batch_id,
        },
    )
    assert create_resp.status_code in (200, 201), create_resp.text

    history = await client.get(
        f"/api/v1/mutation-batches/{batch_id}/events",
        headers=auth_headers(admin_user),
    )
    assert history.status_code == 200, history.text
    events = history.json()["events"]
    assert any(e["event_type"].startswith("card.") for e in events), events
