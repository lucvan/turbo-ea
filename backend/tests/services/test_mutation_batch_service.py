"""Unit tests for mutation-batch helpers that need no database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.mutation_batch_service import (
    CONFIRM_TOKEN_TTL,
    ConfirmTokenError,
    confirm_token_expires_at,
    issue_confirm_token,
    redeem_confirm_token,
)


def test_issue_confirm_token_is_random_and_long_enough():
    a = issue_confirm_token()
    b = issue_confirm_token()
    assert a != b
    assert len(a) >= 16


def test_a_token_expires_one_ttl_after_its_batch_was_opened():
    opened = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    batch = SimpleNamespace(confirm_token="tok", created_at=opened)
    assert CONFIRM_TOKEN_TTL == timedelta(minutes=15)
    assert confirm_token_expires_at(batch) == opened + timedelta(minutes=15)


def test_a_batch_without_a_token_has_no_expiry():
    opened = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    assert confirm_token_expires_at(SimpleNamespace(confirm_token=None, created_at=opened)) is None
    assert confirm_token_expires_at(SimpleNamespace(confirm_token="tok", created_at=None)) is None


async def test_redeeming_without_a_token_is_refused_before_any_query():
    """``db=None`` would raise on the first query: the refusal comes first."""
    with pytest.raises(ConfirmTokenError) as caught:
        await redeem_confirm_token(
            None,
            actor=SimpleNamespace(id="u"),
            tool_name="update_cards_bulk",
            row_count=40,
            payload_hash=None,
            token="",
        )
    assert caught.value.code == "confirm_token_required"
    assert "40 rows" in caught.value.message
    assert "Run the dry-run again" in caught.value.message
    assert str(caught.value) == caught.value.message


# ── Auto-batch skiplist ──────────────────────────────────────────────────


def test_auto_batch_skiplist_contains_notifications():
    """Notifications publish on every write that mentions a stake-held
    card — high volume, low signal. The underlying card / relation /
    ADR write is already audited under its own event, so the
    notification's own batch would be a noisy duplicate."""
    from app.services.event_bus import _NO_AUTO_BATCH_PREFIXES

    assert "notification." in _NO_AUTO_BATCH_PREFIXES


def test_auto_batch_skiplist_keeps_audit_relevant_writes():
    """High-signal write event types must NOT be in the skiplist so
    they still create auto-batches when published from web/api."""
    from app.services.event_bus import _NO_AUTO_BATCH_PREFIXES

    high_signal_types = [
        "card.created",
        "card.updated",
        "card.archived",
        "card.restored",
        "relation.created",
        "relation.upserted",
        "relation.deleted",
        "adr.signed",
        "adr.rejected",
        "soaw.signed",
        "risk.added",
        "risk.updated",
        "adr.created",
        "comment.created",
        "document.added",
        "stakeholder.added",
        "stakeholder.removed",
        "stakeholder.role_changed",
        "tag.added",
        "tag.removed",
        "process_diagram.saved",
        "process_flow.approved",
        # Rollback events must stay captured — they're the inverse-op
        # records admins specifically want to see.
        "rollback.delete_card",
    ]
    for et in high_signal_types:
        assert not any(et.startswith(p) for p in _NO_AUTO_BATCH_PREFIXES), (
            f"{et} should NOT be skipped — it's audit-relevant"
        )
