"""Confirmation, row outcomes and audit handling of the bulk write tools.

Regression tests for three defects in 2.158.0:

- a dry-run above the confirmation threshold failed while closing its audit
  batch, so the preview and its token never reached the caller;
- ``update_cards_bulk`` regrouped rows by patch, so ``row_index`` restarted in
  every group and a commit returned only the last group's cards;
- the confirm token was sent with the close call, after the write, where
  nothing checked it.

The backend half is covered in ``backend/tests/api/test_mutation_batches.py``
and ``test_cards_bulk_rows.py``; ``tests/live/bulk_update_check.py`` drives the
two together.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from turbo_ea_mcp import server
from turbo_ea_mcp.batches import (
    BatchCloseError,
    ConfirmationRejected,
    mutation_batch,
    operation_hash,
)

EXPIRES = "2026-10-09T12:15:00Z"


@pytest.fixture
def fake_token(monkeypatch):
    monkeypatch.setattr(server, "_stdio_token", "test-token")
    monkeypatch.setattr(server, "MCP_BATCH_CONFIRMATION_THRESHOLD", 20)
    monkeypatch.setattr(server, "MCP_REQUIRE_DRYRUN_FIRST", True)
    yield "test-token"


def _http_error(status: int, detail: object) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://backend/api/v1/x")
    response = httpx.Response(status, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(f"{status} error", request=request, response=response)


class Backend:
    """Stands in for the backend behind ``TurboEAClient``. Records every
    call; each hook can be replaced with a value or an exception."""

    def __init__(self, *, token: str | None = None, rows: object = None):
        self.token = token
        self.rows = rows if rows is not None else {}
        self.open_error: Exception | None = None
        self.close_error: Exception | None = None
        self.write_error: Exception | None = None
        self.events: object = {"events": []}
        self.opens: list[tuple[str, dict]] = []
        self.closes: list[dict] = []
        self.writes: list[tuple[str, str, dict | None]] = []

    async def post(self, path, json=None):
        if path.startswith("/mutation-batches/") and path.endswith("/commit"):
            self.closes.append(json)
            if self.close_error:
                raise self.close_error
            return {"id": "B-1"}
        if path.startswith("/mutation-batches"):
            self.opens.append((path, json))
            if self.open_error:
                raise self.open_error
            resp = {"id": "B-1", "dry_run": json["dry_run"]}
            if self.token and json["dry_run"]:
                resp |= {"confirm_token": self.token, "confirm_token_expires_at": EXPIRES}
            return resp
        self.writes.append(("POST", path, json))
        if self.write_error:
            raise self.write_error
        return self.rows

    async def patch(self, path, json=None):
        self.writes.append(("PATCH", path, json))
        if self.write_error:
            raise self.write_error
        return self.rows

    async def delete(self, path):
        self.writes.append(("DELETE", path, None))
        return {}

    async def get(self, path, params=None):
        if path.endswith("/events"):
            if isinstance(self.events, Exception):
                raise self.events
            return self.events
        if path.endswith("/archive-impact"):
            return {"child_count": 0}
        return {"items": []}

    def __enter__(self):
        self._patches = [
            patch.object(server.TurboEAClient, name, AsyncMock(side_effect=getattr(self, name)))
            for name in ("post", "patch", "delete", "get")
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _rows(count: int, stage: str = "core") -> list[dict]:
    return [{"card_id": f"c{i}", "lifecycle_stage": stage} for i in range(count)]


def _preview(rows: list[dict], *, failed: int = 0) -> dict:
    results = [
        {
            "row_index": i,
            "card_id": r["card_id"],
            "status": "error" if i < failed else "would_update",
            "before": {},
            "after": {},
            "error": "bad" if i < failed else None,
        }
        for i, r in enumerate(rows)
    ]
    return {
        "dry_run": True,
        "committed": False,
        "total": len(rows),
        "would_update": len(rows) - failed,
        "updated": 0,
        "unchanged": 0,
        "failed": failed,
        "not_applied": 0,
        "results": results,
    }


def _committed(rows: list[dict], *, unchanged: int = 0) -> dict:
    results = [
        {
            "row_index": i,
            "card_id": r["card_id"],
            "status": "unchanged" if i < unchanged else "updated",
            "before": {},
            "after": {},
            "error": None,
        }
        for i, r in enumerate(rows)
    ]
    return {
        "dry_run": False,
        "committed": True,
        "total": len(rows),
        "would_update": 0,
        "updated": len(rows) - unchanged,
        "unchanged": unchanged,
        "failed": 0,
        "not_applied": 0,
        "results": results,
    }


# ── operation_hash ──────────────────────────────────────────────────────────


class TestOperationHash:
    def test_same_operation_same_digest_whatever_the_key_order(self):
        a = operation_hash("t", [{"card_id": "c1", "name": "N"}])
        b = operation_hash("t", [{"name": "N", "card_id": "c1"}])
        assert a == b
        assert len(a) == 64

    def test_row_order_tool_and_values_all_change_the_digest(self):
        base = operation_hash("t", [{"card_id": "c1"}, {"card_id": "c2"}])
        assert operation_hash("t", [{"card_id": "c2"}, {"card_id": "c1"}]) != base
        assert operation_hash("u", [{"card_id": "c1"}, {"card_id": "c2"}]) != base
        assert operation_hash("t", [{"card_id": "c1"}, {"card_id": "c3"}]) != base


# ── mutation_batch ──────────────────────────────────────────────────────────


class TestMutationBatch:
    @pytest.mark.asyncio
    async def test_dry_run_open_carries_the_binding_and_close_carries_no_token(self):
        with Backend(token="TOK") as backend:
            async with mutation_batch(
                "jwt",
                tool_name="t",
                row_count=40,
                dry_run=True,
                confirm_token="IGNORED-ON-A-DRY-RUN",
                payload=[1, 2],
                require_confirmation=True,
            ) as batch:
                batch.summary = {"rows": 40}
                assert batch.confirmation_fields() == {
                    "confirm_token": "TOK",
                    "confirm_token_expires_at": EXPIRES,
                }
        path, body = backend.opens[0]
        assert path == "/mutation-batches?row_count=40"
        assert body == {
            "tool_name": "t",
            "dry_run": True,
            "payload_hash": operation_hash("t", [1, 2]),
            "require_confirmation": True,
        }
        assert backend.closes == [{"summary": {"rows": 40, "status": "previewed"}}]

    @pytest.mark.asyncio
    async def test_write_open_presents_the_token(self):
        with Backend() as backend:
            async with mutation_batch(
                "jwt", tool_name="t", row_count=40, dry_run=False, confirm_token="TOK"
            ) as batch:
                assert batch.confirmation_fields() == {}
        assert backend.opens[0][1] == {"tool_name": "t", "dry_run": False, "confirm_token": "TOK"}
        assert backend.closes == [{"summary": {"status": "committed"}}]

    @pytest.mark.asyncio
    async def test_a_status_set_by_the_tool_is_kept(self):
        with Backend() as backend:
            async with mutation_batch("jwt", tool_name="t", row_count=1, dry_run=False) as batch:
                batch.summary = {"status": "failed"}
        assert backend.closes == [{"summary": {"status": "failed"}}]

    @pytest.mark.asyncio
    async def test_refused_token_raises_before_the_body_runs(self):
        backend = Backend()
        backend.open_error = _http_error(
            400, {"code": "confirm_token_expired", "message": "The confirm_token has expired."}
        )
        ran = False
        with backend, pytest.raises(ConfirmationRejected) as caught:
            async with mutation_batch(
                "jwt", tool_name="t", row_count=40, dry_run=False, confirm_token="OLD"
            ):
                ran = True
        assert ran is False
        assert backend.closes == []
        assert caught.value.as_dict("t") == {
            "error": "confirm_token_expired",
            "message": "The confirm_token has expired.",
            "tool": "t",
            "committed": False,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            _http_error(400, "plain string detail"),
            _http_error(400, {"code": "something_else", "message": "x"}),
            _http_error(403, {"code": "confirm_token_invalid", "message": "x"}),
            httpx.HTTPStatusError(
                "400",
                request=httpx.Request("POST", "http://b"),
                response=httpx.Response(
                    400, text="not json", request=httpx.Request("POST", "http://b")
                ),
            ),
        ],
    )
    async def test_other_open_failures_are_not_dressed_up_as_token_refusals(self, error):
        backend = Backend()
        backend.open_error = error
        with backend, pytest.raises(httpx.HTTPStatusError):
            async with mutation_batch("jwt", tool_name="t", row_count=1, dry_run=False):
                pass

    @pytest.mark.asyncio
    async def test_an_exception_in_the_body_closes_the_batch_as_failed(self):
        with Backend() as backend, pytest.raises(RuntimeError, match="boom"):
            async with mutation_batch("jwt", tool_name="t", row_count=1, dry_run=False):
                raise RuntimeError("boom")
        assert backend.closes == [{"summary": {"status": "failed", "error": "boom"}}]

    @pytest.mark.asyncio
    async def test_the_original_exception_survives_a_failing_close(self):
        backend = Backend()
        backend.close_error = RuntimeError("audit down")
        with backend, pytest.raises(RuntimeError, match="boom"):
            async with mutation_batch("jwt", tool_name="t", row_count=1, dry_run=False):
                raise RuntimeError("boom")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("dry_run", "summary", "changed", "phrase"),
        [
            (True, None, False, "No data was changed."),
            (False, {"status": "failed"}, False, "No data was changed."),
            (False, {"updated": 3}, True, "The write itself completed"),
        ],
    )
    async def test_a_failing_close_says_whether_data_changed(
        self, dry_run, summary, changed, phrase
    ):
        backend = Backend()
        backend.close_error = RuntimeError("audit down")
        with backend, pytest.raises(BatchCloseError) as caught:
            async with mutation_batch(
                "jwt", tool_name="t", row_count=1, dry_run=dry_run
            ) as batch:
                batch.summary = summary
        assert caught.value.data_changed is changed
        assert caught.value.as_dict() == {
            "batch_id": "B-1",
            "data_changed": changed,
            "message": str(caught.value),
        }
        assert phrase in str(caught.value)
        assert "B-1" in str(caught.value) and "audit down" in str(caught.value)


# ── update_cards_bulk ───────────────────────────────────────────────────────


class TestUpdateCardsBulkPreview:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("count", "needs"), [(19, False), (20, False), (21, True), (40, True)])
    async def test_dry_run_returns_the_whole_preview_and_a_token_above_the_threshold(
        self, fake_token, count, needs
    ):
        rows = _rows(count)
        with Backend(token="TOK" if needs else None, rows=_preview(rows)) as backend:
            data = json.loads(
                await server.update_cards_bulk(updates=rows, strict_attributes=True)
            )
        assert backend.opens[0][0] == f"/mutation-batches?row_count={count}"
        assert backend.opens[0][1]["require_confirmation"] is needs
        assert backend.opens[0][1]["payload_hash"] == operation_hash(
            "update_cards_bulk", {"updates": rows, "strict_attributes": True}
        )
        assert [(r["row_index"], r["card_id"]) for r in data["results"]] == [
            (i, f"c{i}") for i in range(count)
        ]
        assert data["total"] == data["would_update"] == count
        assert data["dry_run"] is True and data["committed"] is False
        assert data.get("confirm_token") == ("TOK" if needs else None)
        assert data.get("confirm_token_expires_at") == (EXPIRES if needs else None)
        assert backend.closes == [
            {
                "summary": {
                    "rows": count,
                    "status": "previewed",
                    "would_update": count,
                    "updated": 0,
                    "unchanged": 0,
                    "failed": 0,
                }
            }
        ]

    @pytest.mark.asyncio
    async def test_a_preview_with_a_failed_row_hands_out_no_token(self, fake_token):
        rows = _rows(25)
        with Backend(token="TOK", rows=_preview(rows, failed=1)) as backend:
            data = json.loads(await server.update_cards_bulk(updates=rows))
        assert "confirm_token" not in data
        assert "confirm_token_expires_at" not in data
        assert "No confirm_token was issued" in data["message"]
        assert data["failed"] == 1
        # "failed" is what makes the backend void the token it issued.
        assert backend.closes[0]["summary"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_small_preview_with_a_failed_row_says_nothing_about_tokens(self, fake_token):
        rows = _rows(3)
        with Backend(rows=_preview(rows, failed=1)) as backend:
            data = json.loads(await server.update_cards_bulk(updates=rows))
        assert "message" not in data
        assert backend.closes[0]["summary"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_dry_run_that_cannot_reach_the_backend_raises(self, fake_token):
        backend = Backend()
        backend.write_error = _http_error(403, "Not enough permissions")
        with backend, pytest.raises(httpx.HTTPStatusError):
            await server.update_cards_bulk(updates=_rows(2))
        assert backend.closes[0]["summary"]["status"] == "failed"


class TestUpdateCardsBulkCommit:
    @pytest.mark.asyncio
    async def test_commit_reports_every_row_and_counts_that_add_up(self, fake_token):
        rows = _rows(10)
        rows[4]["lifecycle_stage"] = "emerging"
        with Backend(rows=_committed(rows, unchanged=2)) as backend:
            data = json.loads(await server.update_cards_bulk(updates=rows, dry_run=False))
        assert backend.writes == [
            (
                "PATCH",
                "/cards/bulk-rows",
                {
                    "rows": [
                        {
                            "card_id": r["card_id"],
                            "updates": {"lifecycle_stage": r["lifecycle_stage"]},
                        }
                        for r in rows
                    ],
                    "strict_attributes": False,
                    "dry_run": False,
                },
            )
        ]
        assert data["committed"] is True
        assert len(data["results"]) == data["total"] == 10
        assert data["updated"] == 8 and data["unchanged"] == 2
        assert (
            data["would_update"]
            + data["updated"]
            + data["unchanged"]
            + data["failed"]
            + data["not_applied"]
            == 10
        )
        assert "result" not in data  # the old "last group's cards" key
        assert "confirm_token" not in data
        assert "error" not in data and "audit_warning" not in data
        assert backend.closes[0]["summary"] == {
            "rows": 10,
            "status": "committed",
            "would_update": 0,
            "updated": 8,
            "unchanged": 2,
            "failed": 0,
        }

    @pytest.mark.asyncio
    async def test_commit_above_the_threshold_without_a_token_makes_no_call(self, fake_token):
        with Backend() as backend:
            data = json.loads(await server.update_cards_bulk(updates=_rows(21), dry_run=False))
        assert data["error"] == "confirm_token_required"
        assert data["received"] == 21 and data["threshold"] == 20
        assert backend.opens == [] and backend.writes == []

    @pytest.mark.asyncio
    async def test_commit_at_the_threshold_needs_no_token(self, fake_token):
        rows = _rows(20)
        with Backend(rows=_committed(rows)) as backend:
            data = json.loads(await server.update_cards_bulk(updates=rows, dry_run=False))
        assert data["committed"] is True
        assert backend.opens[0][1]["require_confirmation"] is False
        assert "confirm_token" not in backend.opens[0][1]

    @pytest.mark.asyncio
    async def test_commit_presents_the_token_when_opening_the_batch(self, fake_token):
        rows = _rows(40)
        with Backend(rows=_committed(rows)) as backend:
            await server.update_cards_bulk(updates=rows, dry_run=False, confirm_token="TOK")
        assert backend.opens[0][1]["confirm_token"] == "TOK"
        assert backend.opens[0][1]["require_confirmation"] is True
        assert backend.opens[0][1]["payload_hash"] == operation_hash(
            "update_cards_bulk", {"updates": rows, "strict_attributes": False}
        )
        assert "confirm_token" not in backend.closes[0]

    @pytest.mark.asyncio
    async def test_a_refused_token_writes_nothing(self, fake_token):
        backend = Backend()
        backend.open_error = _http_error(
            400, {"code": "confirm_token_mismatch", "message": "different operation"}
        )
        with backend:
            data = json.loads(
                await server.update_cards_bulk(
                    updates=_rows(40), dry_run=False, confirm_token="TOK"
                )
            )
        assert data == {
            "error": "confirm_token_mismatch",
            "message": "different operation",
            "tool": "update_cards_bulk",
            "committed": False,
        }
        assert backend.writes == []

    @pytest.mark.asyncio
    async def test_a_refused_commit_is_recorded_as_failed(self, fake_token):
        rows = _rows(3)
        refused = {
            "dry_run": False,
            "committed": False,
            "total": 3,
            "would_update": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 1,
            "not_applied": 2,
            "error": "1 row(s) were rejected, so nothing was written",
            "results": [
                {"row_index": 0, "card_id": "c0", "status": "not_applied"},
                {"row_index": 1, "card_id": "c1", "status": "not_applied"},
                {"row_index": 2, "card_id": "c2", "status": "error", "error": "bad stage"},
            ],
        }
        with Backend(rows=refused) as backend:
            data = json.loads(await server.update_cards_bulk(updates=rows, dry_run=False))
        assert data["committed"] is False
        assert data["error"] == "1 row(s) were rejected, so nothing was written"
        assert data["not_applied"] == 2 and data["failed"] == 1
        assert [r["status"] for r in data["results"]] == ["not_applied", "not_applied", "error"]
        assert backend.closes[0]["summary"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_4xx_on_commit_means_nothing_was_written(self, fake_token):
        backend = Backend()
        backend.write_error = _http_error(403, "Not enough permissions")
        backend.events = RuntimeError("must not be asked")
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=_rows(2), dry_run=False))
        assert data["committed"] is False
        assert data["message"] == "Nothing was written."
        assert data["batch_id"] == "B-1" and data["dry_run"] is False
        assert "403" in data["error"]
        assert backend.closes[0]["summary"] == {
            "rows": 2,
            "status": "failed",
            "outcome_verified": True,
            "error": data["error"],
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [_http_error(500, "boom"), httpx.ReadTimeout("timed out")]
    )
    async def test_a_lost_response_is_settled_from_the_audit_trail(self, fake_token, error):
        backend = Backend()
        backend.write_error = error
        backend.events = {"events": [{"id": "e1"}, {"id": "e2"}]}
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=_rows(2), dry_run=False))
        assert data["committed"] is True
        assert "recorded 2 change(s): the write took effect" in data["message"]
        assert backend.closes[0]["summary"]["status"] == "committed"
        assert backend.closes[0]["summary"]["outcome_verified"] is True

    @pytest.mark.asyncio
    async def test_a_lost_response_with_no_recorded_change_wrote_nothing(self, fake_token):
        backend = Backend()
        backend.write_error = _http_error(502, "bad gateway")
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=_rows(2), dry_run=False))
        assert data["committed"] is False
        assert data["message"] == "Nothing was written."

    @pytest.mark.asyncio
    @pytest.mark.parametrize("events", [RuntimeError("down"), ["not", "a", "dict"]])
    async def test_an_unverifiable_outcome_is_reported_as_unknown(self, fake_token, events):
        backend = Backend()
        backend.write_error = httpx.ReadTimeout("timed out")
        backend.events = events
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=_rows(2), dry_run=False))
        assert data["committed"] is None
        assert "Read the cards back before retrying" in data["message"]
        assert backend.closes[0]["summary"]["status"] == "failed"
        assert backend.closes[0]["summary"]["outcome_verified"] is False

    @pytest.mark.asyncio
    async def test_an_audit_failure_after_a_commit_keeps_the_result(self, fake_token):
        rows = _rows(3)
        backend = Backend(rows=_committed(rows))
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=rows, dry_run=False))
        assert data["committed"] is True and data["updated"] == 3
        assert len(data["results"]) == 3
        assert data["audit_warning"]["data_changed"] is True
        assert data["audit_warning"]["batch_id"] == "B-1"

    @pytest.mark.asyncio
    async def test_an_audit_failure_after_a_preview_says_nothing_changed(self, fake_token):
        rows = _rows(3)
        backend = Backend(rows=_preview(rows))
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(await server.update_cards_bulk(updates=rows))
        assert data["would_update"] == 3
        assert data["audit_warning"]["data_changed"] is False


class TestUpdateCardsBulkPreflight:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("row", [{"name": "x"}, {"card_id": "", "name": "x"}, "c1"])
    async def test_a_row_without_card_id_names_its_position(self, fake_token, row):
        with Backend() as backend:
            data = json.loads(
                await server.update_cards_bulk(updates=[{"card_id": "c0", "name": "a"}, row])
            )
        assert data["error"] == "missing_card_id"
        assert data["row_index"] == 1
        assert backend.opens == []

    @pytest.mark.asyncio
    async def test_unknown_fields_are_refused_with_the_rows_and_the_allowed_set(self, fake_token):
        with Backend() as backend:
            data = json.loads(
                await server.update_cards_bulk(
                    updates=[
                        {"card_id": "c0", "name": "ok"},
                        {"card_id": "c1", "lifecycle_stagee": "core", "zzz": 1},
                    ],
                    dry_run=False,
                )
            )
        assert data["error"] == "unknown_fields"
        assert data["rows"] == [
            {"row_index": 1, "card_id": "c1", "fields": ["lifecycle_stagee", "zzz"]}
        ]
        assert "lifecycle_stage" in data["allowed_fields"]
        assert "parent_label" in data["allowed_fields"]
        assert "card_id" not in data["allowed_fields"]
        assert data["allowed_fields"] == sorted(data["allowed_fields"])
        assert backend.opens == [] and backend.writes == []

    @pytest.mark.asyncio
    async def test_every_documented_field_is_accepted(self, fake_token):
        row = {"card_id": "c0", **{f: "x" for f in server._CARD_UPDATE_FIELDS}}
        with Backend(rows=_preview([row])) as backend:
            data = json.loads(await server.update_cards_bulk(updates=[row]))
        assert "error" not in data
        assert len(backend.writes) == 1

    @pytest.mark.asyncio
    async def test_a_card_listed_twice_is_refused_with_both_positions(self, fake_token):
        with Backend() as backend:
            data = json.loads(
                await server.update_cards_bulk(
                    updates=[
                        {"card_id": "AAA", "name": "one"},
                        {"card_id": "bbb", "name": "two"},
                        {"card_id": "aaa", "name": "three"},
                    ],
                    dry_run=False,
                )
            )
        assert data["error"] == "duplicate_card_id"
        assert data["duplicates"] == [{"card_id": "AAA", "row_indexes": [0, 2]}]
        assert backend.opens == [] and backend.writes == []


# ── The other tools that share the helper ───────────────────────────────────


class TestCreateCardsBulk:
    CARDS = [{"row_index": i, "type": "Application", "name": f"A{i}"} for i in range(25)]

    @pytest.mark.asyncio
    async def test_dry_run_returns_token_and_expiry(self, fake_token):
        rows = {"results": [], "created": 0, "failed": 0, "dry_run": True}
        with Backend(token="TOK", rows=rows) as backend:
            data = json.loads(await server.create_cards_bulk(cards=self.CARDS))
        assert data["confirm_token"] == "TOK"
        assert data["confirm_token_expires_at"] == EXPIRES
        assert data["batch_id"] == "B-1"
        assert backend.opens[0][1]["payload_hash"] == operation_hash(
            "create_cards_bulk", self.CARDS
        )
        assert backend.opens[0][1]["require_confirmation"] is True
        assert backend.closes[0]["summary"]["status"] == "previewed"

    @pytest.mark.asyncio
    async def test_commit_carries_no_token_fields_in_its_response(self, fake_token):
        rows = {"results": [], "created": 25, "failed": 0, "dry_run": False}
        with Backend(token="TOK", rows=rows) as backend:
            data = json.loads(
                await server.create_cards_bulk(
                    cards=self.CARDS, dry_run=False, confirm_token="TOK"
                )
            )
        assert "confirm_token" not in data
        assert backend.opens[0][1]["confirm_token"] == "TOK"

    @pytest.mark.asyncio
    async def test_refused_token(self, fake_token):
        backend = Backend()
        backend.open_error = _http_error(400, {"code": "confirm_token_used", "message": "used"})
        with backend:
            data = json.loads(
                await server.create_cards_bulk(
                    cards=self.CARDS, dry_run=False, confirm_token="TOK"
                )
            )
        assert data["error"] == "confirm_token_used"
        assert data["tool"] == "create_cards_bulk"
        assert backend.writes == []

    @pytest.mark.asyncio
    async def test_audit_failure_keeps_the_result(self, fake_token):
        backend = Backend(rows={"results": [], "created": 2, "failed": 0, "dry_run": False})
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(
                await server.create_cards_bulk(cards=self.CARDS[:2], dry_run=False)
            )
        assert data["created"] == 2
        assert data["audit_warning"]["data_changed"] is True

    @pytest.mark.asyncio
    async def test_audit_failure_with_a_list_response_still_returns_it(self, fake_token):
        backend = Backend(rows=["unexpected"])
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(
                await server.create_cards_bulk(cards=self.CARDS[:2], dry_run=False)
            )
        assert data == ["unexpected"]


class TestArchiveCards:
    IDS = [f"c{i}" for i in range(21)]

    @pytest.mark.asyncio
    async def test_dry_run_returns_token_bound_to_the_whole_request(self, fake_token):
        with Backend(token="TOK") as backend:
            data = json.loads(
                await server.archive_cards(card_ids=self.IDS, reason="tidy")
            )
        assert data["confirm_token"] == "TOK"
        assert data["confirm_token_expires_at"] == EXPIRES
        assert data["would_archive"] == 21
        assert backend.opens[0][1]["payload_hash"] == operation_hash(
            "archive_cards",
            {"card_ids": self.IDS, "cascade_all_related": False, "reason": "tidy"},
        )
        assert backend.opens[0][1]["require_confirmation"] is True

    @pytest.mark.asyncio
    async def test_refused_token(self, fake_token):
        backend = Backend()
        backend.open_error = _http_error(400, {"code": "confirm_token_invalid", "message": "no"})
        with backend:
            data = json.loads(
                await server.archive_cards(
                    card_ids=self.IDS, dry_run=False, confirm_token="X"
                )
            )
        assert data["error"] == "confirm_token_invalid"
        assert data["tool"] == "archive_cards"
        assert backend.writes == []

    @pytest.mark.asyncio
    async def test_commit_presents_the_token_and_summarises(self, fake_token):
        rows = {"archived_card_ids": self.IDS, "cascaded_card_ids": ["k"]}
        with Backend(rows=rows) as backend:
            data = json.loads(
                await server.archive_cards(
                    card_ids=self.IDS, dry_run=False, confirm_token="TOK"
                )
            )
        assert data["batch_id"] == "B-1"
        assert backend.opens[0][1]["confirm_token"] == "TOK"
        assert backend.closes[0]["summary"] == {
            "rows": 21,
            "archived": 21,
            "cascaded": 1,
            "status": "committed",
        }

    @pytest.mark.asyncio
    async def test_audit_failure_keeps_the_result(self, fake_token):
        backend = Backend(rows={"archived_card_ids": ["c0"], "cascaded_card_ids": []})
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(await server.archive_cards(card_ids=["c0"], dry_run=False))
        assert data["archived_card_ids"] == ["c0"]
        assert data["audit_warning"]["data_changed"] is True

    @pytest.mark.asyncio
    async def test_audit_failure_with_a_list_response_still_returns_it(self, fake_token):
        backend = Backend(rows=["unexpected"])
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(await server.archive_cards(card_ids=["c0"], dry_run=False))
        assert data == ["unexpected"]


class TestLogoTools:
    """Their previews make no backend write call, so nothing used to issue
    the token their commits demand above the threshold."""

    IDS = [f"c{i}" for i in range(21)]

    @pytest.mark.asyncio
    async def test_clear_dry_run_above_the_threshold_returns_a_token(self, fake_token):
        with Backend(token="TOK") as backend:
            data = json.loads(await server.clear_card_logos(card_ids=self.IDS))
        assert data["confirm_token"] == "TOK"
        assert data["batch_id"] == "B-1"
        assert data["dry_run"] is True
        path, body = backend.opens[0]
        assert path == "/mutation-batches?row_count=21"
        assert body == {
            "tool_name": "clear_card_logos",
            "dry_run": True,
            "payload_hash": operation_hash("clear_card_logos", self.IDS),
            "require_confirmation": True,
        }
        assert backend.closes == [{"summary": {"rows": 21, "status": "previewed"}}]

    @pytest.mark.asyncio
    async def test_clear_dry_run_at_the_threshold_opens_no_batch(self, fake_token):
        with Backend(token="TOK") as backend:
            data = json.loads(await server.clear_card_logos(card_ids=self.IDS[:20]))
        assert "confirm_token" not in data and "batch_id" not in data
        assert backend.opens == []

    @pytest.mark.asyncio
    async def test_clear_dry_run_opens_no_batch_when_previews_are_not_required(
        self, fake_token, monkeypatch
    ):
        monkeypatch.setattr(server, "MCP_REQUIRE_DRYRUN_FIRST", False)
        with Backend(token="TOK") as backend:
            await server.clear_card_logos(card_ids=self.IDS)
        assert backend.opens == []

    @pytest.mark.asyncio
    async def test_clear_commit_presents_the_token(self, fake_token):
        with Backend() as backend:
            data = json.loads(
                await server.clear_card_logos(
                    card_ids=self.IDS, dry_run=False, confirm_token="TOK"
                )
            )
        assert data["cleared"] == 21
        assert backend.opens[0][1]["confirm_token"] == "TOK"
        assert backend.opens[0][1]["payload_hash"] == operation_hash(
            "clear_card_logos", self.IDS
        )
        assert backend.opens[0][1]["require_confirmation"] is True

    @pytest.mark.asyncio
    async def test_clear_refused_token_deletes_nothing(self, fake_token):
        backend = Backend()
        backend.open_error = _http_error(400, {"code": "confirm_token_invalid", "message": "no"})
        with backend:
            data = json.loads(
                await server.clear_card_logos(
                    card_ids=self.IDS, dry_run=False, confirm_token="X"
                )
            )
        assert data["error"] == "confirm_token_invalid"
        assert data["tool"] == "clear_card_logos"
        assert backend.writes == []

    @pytest.mark.asyncio
    async def test_clear_audit_failure_keeps_the_result(self, fake_token):
        backend = Backend()
        backend.close_error = RuntimeError("audit down")
        with backend:
            data = json.loads(await server.clear_card_logos(card_ids=["c0"], dry_run=False))
        assert data["cleared"] == 1
        assert data["audit_warning"]["data_changed"] is True

    @pytest.mark.asyncio
    async def test_set_dry_run_above_the_threshold_returns_a_token(self, fake_token):
        items = [{"card_id": cid, "icon_slug": "postgresql"} for cid in self.IDS]
        with Backend(token="TOK") as backend:
            data = json.loads(await server.set_card_logos(items=items))
        assert data["confirm_token"] == "TOK"
        assert data["dry_run"] is True
        opened = [body for _, body in backend.opens if body["tool_name"] == "set_card_logos"]
        assert opened[0]["payload_hash"] == operation_hash("set_card_logos", items)
        assert opened[0]["dry_run"] is True

    @pytest.mark.asyncio
    async def test_set_refused_token_uploads_nothing(self, fake_token):
        items = [{"card_id": cid, "icon_slug": "postgresql"} for cid in self.IDS]
        backend = Backend()
        backend.open_error = _http_error(400, {"code": "confirm_token_expired", "message": "old"})
        with backend, patch.object(
            server.TurboEAClient, "post_multipart", AsyncMock()
        ) as upload:
            data = json.loads(
                await server.set_card_logos(items=items, dry_run=False, confirm_token="X")
            )
        assert data["error"] == "confirm_token_expired"
        assert data["tool"] == "set_card_logos"
        upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_commit_binds_the_same_payload_as_its_preview(self, fake_token):
        items = [{"card_id": cid, "icon_slug": "postgresql"} for cid in self.IDS]
        backend = Backend()
        with backend, patch.object(
            server.TurboEAClient, "post_multipart", AsyncMock(return_value={})
        ):
            data = json.loads(
                await server.set_card_logos(items=items, dry_run=False, confirm_token="TOK")
            )
        assert data["set"] == 21
        path, body = backend.opens[0]
        assert path == "/mutation-batches?row_count=21"
        assert body["payload_hash"] == operation_hash("set_card_logos", items)
        assert body["confirm_token"] == "TOK"
        assert body["require_confirmation"] is True

    @pytest.mark.asyncio
    async def test_set_audit_failure_keeps_the_result(self, fake_token):
        backend = Backend()
        backend.close_error = RuntimeError("audit down")
        with backend, patch.object(
            server.TurboEAClient, "post_multipart", AsyncMock(return_value={})
        ):
            data = json.loads(
                await server.set_card_logos(
                    items=[{"card_id": "c0", "icon_slug": "postgresql"}], dry_run=False
                )
            )
        assert data["set"] == 1
        assert data["audit_warning"]["data_changed"] is True
