"""Helpers for opening / closing mutation batches around MCP write tools.

Every MCP write tool flows through these helpers so the audit log gets a
batch row and every event the underlying backend handler publishes is
stamped with the same ``batch_id``.

Usage from a tool handler::

    async with mutation_batch(
        token, tool_name="create_cards_bulk",
        row_count=len(cards), dry_run=dry_run,
        confirm_token=confirm_token,
        payload=cards, require_confirmation=needs_confirmation,
    ) as batch:
        client = batch.client()
        data = await client.post("/cards/bulk-create",
                                 json={"cards": cards, "dry_run": dry_run})
        batch.summary = {"rows": len(cards), "created": data.get("created")}
        # On block exit, the batch is closed with the summary.

Confirmation (S3) happens when the batch is **opened**:

- A dry-run that needs confirmation gets a ``confirm_token`` from the open
  call, bound to the user, the tool, the row count and a digest of
  ``payload``. Closing the dry-run batch needs no token.
- A write that needs confirmation sends that token with its own open call.
  The backend checks and spends it there, so a refused commit raises
  :class:`ConfirmationRejected` before the tool has written anything.

The context manager guarantees:

- A batch row exists before any write — so events emitted by the
  underlying handler land with the right ``batch_id`` even if the
  request crashes mid-flight.
- On clean exit, the batch is closed with ``summary.status`` set to
  ``previewed`` (dry-run) or ``committed`` unless the tool set its own
  (``failed`` when it wrote nothing).
- On exception, the batch is closed with ``status: failed`` and the error,
  so the half-done state is still queryable via ``get_change_history``.
- If the close call itself fails, :class:`BatchCloseError` says whether the
  tool's work was a preview, a refused write or a completed write, so the
  caller is never left guessing whether data changed.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from turbo_ea_mcp.api_client import TurboEAClient


def operation_hash(tool_name: str, payload: Any) -> str:
    """Digest of one tool call's operation. Row order is part of it: the
    rows of a preview are reported by position."""
    canonical = json.dumps(
        {"tool": tool_name, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationRejected(Exception):
    """The backend refused to open a write batch over its confirm token.
    Nothing was written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self, tool: str) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "tool": tool,
            "committed": False,
        }


class BatchCloseError(Exception):
    """The tool's work finished but its audit batch could not be closed.

    ``data_changed`` is ``False`` for a preview or a write the tool marked
    as failed, ``True`` otherwise.
    """

    def __init__(self, batch_id: str | None, data_changed: bool, cause: Exception) -> None:
        self.batch_id = batch_id
        self.data_changed = data_changed
        outcome = (
            "The write itself completed and its changes are saved; do not repeat the call."
            if data_changed
            else "No data was changed."
        )
        super().__init__(
            f"{outcome} Closing audit batch {batch_id} failed, so the audit log "
            f"shows it as still open: {cause}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "data_changed": self.data_changed,
            "message": str(self),
        }


def _confirmation_rejection(exc: httpx.HTTPStatusError) -> ConfirmationRejected | None:
    if exc.response.status_code != 400:
        return None
    try:
        detail = exc.response.json().get("detail")
    except Exception:  # noqa: BLE001 — not a confirmation refusal, then
        return None
    if not isinstance(detail, dict):
        return None
    code = str(detail.get("code") or "")
    if not code.startswith("confirm_token"):
        return None
    return ConfirmationRejected(code, str(detail.get("message") or ""))


@dataclass
class BatchContext:
    """Mutable handle returned by :func:`mutation_batch`. The tool
    handler sets :attr:`summary` before the context manager exits so the
    commit call persists per-row outcomes."""

    token: str
    batch_id: str | None
    open_response: dict[str, Any]
    summary: dict[str, Any] | None = field(default=None)

    @property
    def confirm_token_issued(self) -> str | None:
        """Token surfaced by the open call — only present on dry-runs
        above the confirmation threshold. The agent must echo it back
        when calling the commit path."""
        return self.open_response.get("confirm_token")

    def confirmation_fields(self) -> dict[str, Any]:
        """``confirm_token`` and its expiry for a dry-run response; empty
        when the open call issued none."""
        if not self.confirm_token_issued:
            return {}
        return {
            "confirm_token": self.confirm_token_issued,
            "confirm_token_expires_at": self.open_response.get("confirm_token_expires_at"),
        }

    def client(self) -> TurboEAClient:
        """``TurboEAClient`` with the batch id pre-set so every backend
        write inside the context is stamped with the same id."""
        return TurboEAClient(self.token, batch_id=self.batch_id)


@asynccontextmanager
async def mutation_batch(
    token: str,
    *,
    tool_name: str,
    row_count: int,
    dry_run: bool,
    confirm_token: str | None = None,
    payload: Any = None,
    require_confirmation: bool | None = None,
):
    """Open a mutation batch, yield a :class:`BatchContext`, close on exit.

    Args:
        token: Turbo EA JWT for backend calls.
        tool_name: The MCP tool that opened the batch (lands in the
            audit log so admins can filter MCP-driven writes by tool).
        row_count: Number of rows the wrapper intends to write.
        dry_run: Whether this batch is a preview. The audit log keeps the
            flag, so a preview is never mistaken for a write.
        confirm_token: On a write that needs confirmation, the token the
            matching dry-run returned. Sent with the open call.
        payload: The operation (the tool's arguments minus ``dry_run``
            and ``confirm_token``). Its digest binds the confirm token, so
            the dry-run and the commit must pass the same value.
        require_confirmation: Whether this row count needs a confirm
            token under the MCP server's own settings. ``None`` leaves
            the decision to the backend's default threshold.

    Raises:
        ConfirmationRejected: the write batch was refused over its token.
        BatchCloseError: the body finished but the close call failed.
    """
    # Use a fresh client for the open/close calls — the batch id is
    # not yet known so no ``X-Turbo-EA-Batch`` header.
    bootstrap = TurboEAClient(token)
    open_payload: dict[str, Any] = {"tool_name": tool_name, "dry_run": dry_run}
    if payload is not None:
        open_payload["payload_hash"] = operation_hash(tool_name, payload)
    if require_confirmation is not None:
        open_payload["require_confirmation"] = require_confirmation
    if confirm_token and not dry_run:
        open_payload["confirm_token"] = confirm_token
    try:
        open_resp = await bootstrap.post(
            f"/mutation-batches?row_count={row_count}", json=open_payload
        )
    except httpx.HTTPStatusError as exc:
        rejection = _confirmation_rejection(exc)
        if rejection is None:
            raise
        raise rejection from None
    batch_id = open_resp.get("id") if isinstance(open_resp, dict) else None
    ctx = BatchContext(token=token, batch_id=batch_id, open_response=open_resp)
    try:
        yield ctx
    except Exception as exc:  # noqa: BLE001 — surface the cause in audit
        # Best-effort close so a failed batch is still queryable. We
        # don't re-raise from the close call; the original exception
        # is what the caller needs to see.
        try:
            await bootstrap.post(
                f"/mutation-batches/{batch_id}/commit",
                json={"summary": {"status": "failed", "error": str(exc)}},
            )
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        summary = dict(ctx.summary or {})
        summary.setdefault("status", "previewed" if dry_run else "committed")
        try:
            await bootstrap.post(
                f"/mutation-batches/{batch_id}/commit", json={"summary": summary}
            )
        except Exception as exc:
            data_changed = not dry_run and summary["status"] != "failed"
            raise BatchCloseError(batch_id, data_changed, exc) from exc
