"""Mutation-batch lifecycle helpers.

A mutation batch is the audit handle every MCP write tool opens before
it performs writes. The id flows through:

1. ``POST /mutation-batches`` opens the batch (records actor + tool +
   origin) and returns ``batch_id``.
2. The MCP wrapper sets ``request_batch_id`` on the contextvar so every
   ``event_bus.publish`` call during the subsequent backend write
   stamps the same id onto its event row.
3. ``POST /mutation-batches/{id}/commit`` closes the batch with a
   per-row summary (status, error if any).

A dry-run batch above the per-call confirmation threshold (S3) is issued
a ``confirm_token`` here. The write that follows must present it when it
*opens* its own batch, before it writes anything. The token is bound to
the actor, the tool, the row count and a digest of the payload, expires 15
minutes after the dry run was opened, and can be redeemed once. A dry run
that reported failures voids its token.

The binding lives under the reserved ``confirmation`` key of the batch
``summary``, which the close call preserves.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mutation_batch import MutationBatch
from app.models.user import User

CONFIRM_TOKEN_TTL = timedelta(minutes=15)

# Reserved key of ``MutationBatch.summary`` holding what a confirm token is
# bound to (on the dry-run batch) or which dry-run authorised a write (on the
# write batch). Callers cannot overwrite it through the close call.
CONFIRMATION_KEY = "confirmation"

# ``summary.status`` values that mean the batch did not do what it set out to.
FAILED_STATUSES = frozenset({"failed", "errored"})


async def create_batch(
    db: AsyncSession,
    tool_name: str,
    actor: User | None,
    origin: str,
    dry_run: bool,
    confirm_token: str | None = None,
    confirmation: dict[str, Any] | None = None,
) -> MutationBatch:
    """Open a new batch row. The caller is responsible for setting
    ``request_batch_id`` on the contextvar so subsequent ``publish``
    calls in the same request stamp the id."""
    batch = MutationBatch(
        tool_name=tool_name,
        actor_user_id=actor.id if actor else None,
        origin=origin,
        dry_run=dry_run,
        confirm_token=confirm_token,
        summary={CONFIRMATION_KEY: confirmation} if confirmation else None,
    )
    db.add(batch)
    await db.flush()
    return batch


def issue_confirm_token() -> str:
    """Short, URL-safe token bound to a dry-run batch. The matching
    write must present it when it opens its batch. 15-minute TTL enforced
    in :func:`redeem_confirm_token`."""
    return secrets.token_urlsafe(24)


async def commit_batch(
    db: AsyncSession,
    batch: MutationBatch,
    summary: dict[str, Any] | None = None,
) -> MutationBatch:
    """Close a batch. ``committed_at`` is the time it was closed, for a
    preview as much as for a write; ``dry_run`` and ``summary.status`` say
    which it was and how it ended."""
    batch.committed_at = datetime.now(timezone.utc)
    confirmation = (batch.summary or {}).get(CONFIRMATION_KEY)
    merged: dict[str, Any] | None = dict(summary) if summary is not None else None
    if confirmation:
        if batch.dry_run and (summary or {}).get("status") in FAILED_STATUSES:
            # A preview that did not come back clean authorises nothing.
            confirmation = {**confirmation, "void": True}
        merged = {**(merged or {}), CONFIRMATION_KEY: confirmation}
    if merged is not None:
        batch.summary = merged
    await db.flush()
    return batch


async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> MutationBatch | None:
    res = await db.execute(select(MutationBatch).where(MutationBatch.id == batch_id))
    return res.scalar_one_or_none()


class ConfirmTokenError(Exception):
    """A write batch could not be opened because its confirm token is
    missing or does not authorise this operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_PREVIEW_AGAIN = (
    " Run the dry-run again and commit with the token it returns "
    "(tokens expire 15 minutes after the dry-run and work once)."
)


def confirm_token_expires_at(batch: MutationBatch) -> datetime | None:
    if not batch.confirm_token or batch.created_at is None:
        return None
    return batch.created_at + CONFIRM_TOKEN_TTL


async def redeem_confirm_token(
    db: AsyncSession,
    *,
    actor: User,
    tool_name: str,
    row_count: int,
    payload_hash: str | None,
    token: str,
) -> MutationBatch:
    """Check ``token`` against the dry-run batch that issued it and mark it
    spent. Returns that batch; raises :class:`ConfirmTokenError` otherwise.

    The row is locked for the check, so two commits racing on one token
    cannot both redeem it.
    """
    if not token:
        raise ConfirmTokenError(
            "confirm_token_required",
            f"A commit of {row_count} rows needs the confirm_token of a prior dry-run."
            + _PREVIEW_AGAIN,
        )
    res = await db.execute(
        select(MutationBatch)
        .where(MutationBatch.confirm_token == token, MutationBatch.dry_run.is_(True))
        .with_for_update()
    )
    preview = res.scalars().first()
    # Another user's token is reported exactly like an unknown one.
    if preview is None or preview.actor_user_id != actor.id:
        raise ConfirmTokenError(
            "confirm_token_invalid", "The confirm_token is not valid." + _PREVIEW_AGAIN
        )
    confirmation = dict((preview.summary or {}).get(CONFIRMATION_KEY) or {})
    if confirmation.get("redeemed_at"):
        raise ConfirmTokenError(
            "confirm_token_used", "The confirm_token has already been used." + _PREVIEW_AGAIN
        )
    if datetime.now(timezone.utc) - preview.created_at > CONFIRM_TOKEN_TTL:
        raise ConfirmTokenError(
            "confirm_token_expired", "The confirm_token has expired." + _PREVIEW_AGAIN
        )
    if preview.committed_at is None or confirmation.get("void"):
        raise ConfirmTokenError(
            "confirm_token_invalid",
            "The dry-run behind this confirm_token did not complete cleanly." + _PREVIEW_AGAIN,
        )
    previewed_hash = confirmation.get("payload_hash")
    if (
        preview.tool_name != tool_name
        or confirmation.get("row_count") != row_count
        or (previewed_hash and previewed_hash != payload_hash)
    ):
        raise ConfirmTokenError(
            "confirm_token_mismatch",
            "The confirm_token was issued for a different operation: the tool, the "
            "row count or the payload differs from the dry-run." + _PREVIEW_AGAIN,
        )
    confirmation["redeemed_at"] = datetime.now(timezone.utc).isoformat()
    preview.summary = {**(preview.summary or {}), CONFIRMATION_KEY: confirmation}
    await db.flush()
    return preview


def batch_to_dict(batch: MutationBatch, actor_display_name: str | None = None) -> dict[str, Any]:
    return {
        "id": batch.id,
        "tool_name": batch.tool_name,
        "actor_user_id": batch.actor_user_id,
        "actor_display_name": actor_display_name,
        "origin": batch.origin,
        "dry_run": batch.dry_run,
        # Only surface the token before commit; once committed it's spent
        # and noise. Same goes for read endpoints aimed at history (S6).
        "confirm_token": batch.confirm_token if batch.committed_at is None else None,
        "confirm_token_expires_at": (
            confirm_token_expires_at(batch) if batch.committed_at is None else None
        ),
        "summary": batch.summary,
        "created_at": batch.created_at,
        "committed_at": batch.committed_at,
    }
