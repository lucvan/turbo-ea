"""Live check of the MCP bulk-write path against a running backend.

Not collected by pytest. It starts the real MCP server over stdio, calls its
tools through the MCP protocol, and reads the database back directly, so it
proves what the mocked unit tests cannot: that a preview changes nothing, that
a commit changes exactly the intended cards, and that the confirmation token
is enforced end to end.

Run it only against a throwaway database — it registers users, edits the
Application card type and creates cards::

    python tests/live/bulk_update_check.py \\
        --backend-url http://127.0.0.1:8931 \\
        --db-dsn postgresql://turboea:turboea@127.0.0.1:5433/turboea_e2e

``--mcp-src`` points at another checkout's ``mcp-server`` directory, to run the
same checks against a different version. Exit status is 0 only if every check
passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PASSWORD = "LiveCheck-Passw0rd"
STAGES = ["evaluating", "emerging", "core", "heritage", "sunset", "discontinued"]
SEMANTIC = {
    "evaluating": "pre_operational",
    "emerging": "pre_operational",
    "core": "operational",
    "heritage": "operational",
    "sunset": "retiring",
    "discontinued": "retired",
}

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    RESULTS.append((name, bool(ok), "" if ok else str(detail)[:400]))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n        {str(detail)[:400]}"))
    return bool(ok)


class Db:
    """Independent read-back (and clock manipulation) through psql."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def query(self, sql: str) -> list[dict]:
        wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t"
        out = subprocess.run(
            ["psql", self.dsn, "-Atqc", wrapped], capture_output=True, text=True, check=True
        )
        return json.loads(out.stdout)

    def execute(self, sql: str) -> None:
        subprocess.run(["psql", self.dsn, "-qc", sql], capture_output=True, text=True, check=True)

    def cards(self, ids: list[str]) -> dict[str, dict]:
        id_list = ",".join(f"'{i}'" for i in ids)
        rows = self.query(
            "select id::text, lifecycle_stage, description, parent_id::text, "
            f"updated_at::text from cards where id in ({id_list})"
        )
        return {r["id"]: r for r in rows}

    def snapshot(self) -> str:
        """Digest of every card's editable state, to prove nothing moved."""
        return self.query(
            "select md5(string_agg(id::text || coalesce(lifecycle_stage,'-') || "
            "coalesce(description,'-') || coalesce(parent_id::text,'-') || name || "
            "updated_at::text, '|' order by id)) as digest from cards"
        )[0]["digest"]

    def batch(self, batch_id: str) -> dict:
        rows = self.query(
            "select b.dry_run, b.summary, b.committed_at is not null as closed, b.tool_name, "
            "(select count(*) from events e where e.batch_id = b.id) as events "
            f"from mutation_batches b where b.id = '{batch_id}'"
        )
        return rows[0] if rows else {}


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"
        self.http = httpx.Client(timeout=60.0)

    def sign_in(self, email: str, display_name: str) -> str:
        body = {"email": email, "password": PASSWORD}
        resp = self.http.post(f"{self.base}/auth/login", json=body)
        if resp.status_code != 200:
            resp = self.http.post(
                f"{self.base}/auth/register", json={**body, "display_name": display_name}
            )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def call(self, method: str, path: str, token: str, **kwargs) -> httpx.Response:
        return self.http.request(
            method, f"{self.base}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
        )


class Mcp:
    """One MCP stdio session, signed in as one user."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def tool(self, name: str, **arguments) -> dict:
        result = await self.session.call_tool(name, arguments)
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.isError:
            return {"_tool_error": text}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {"_tool_error": text}
        return parsed if isinstance(parsed, dict) else {"_list": parsed}


@asynccontextmanager
async def mcp_session(args, email: str):
    env = {
        **os.environ,
        "TURBO_EA_URL": args.backend_url,
        "TURBO_EA_EMAIL": email,
        "TURBO_EA_PASSWORD": PASSWORD,
        "PYTHONPATH": str(args.mcp_src),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "turbo_ea_mcp", "--stdio"],
        env=env,
        # ``-m`` puts the working directory first on the import path.
        cwd=str(args.mcp_src),
    )
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield Mcp(session)


def stage_rows(ids: list[str], stage: str) -> list[dict]:
    return [{"card_id": i, "lifecycle_stage": stage} for i in ids]


def reconciles(data: dict, expected_rows: int) -> tuple[bool, str]:
    results = data.get("results")
    if not isinstance(results, list):
        return False, f"no results[] in {list(data)}"
    counted = sum(
        int(data.get(k, 0) or 0)
        for k in ("would_update", "updated", "unchanged", "failed", "not_applied")
    )
    ok = len(results) == expected_rows == data.get("total") == counted
    return ok, f"rows={expected_rows} results={len(results)} total={data.get('total')} sum={counted}"


def rows_in_order(data: dict, rows: list[dict]) -> tuple[bool, str]:
    results = data.get("results") or []
    got = [(r.get("row_index"), r.get("card_id")) for r in results]
    want = [(i, r["card_id"]) for i, r in enumerate(rows)]
    return got == want, f"got {got[:12]} want {want[:12]}"


async def run(args) -> None:
    api, db = Api(args.backend_url), Db(args.db_dsn)
    run_id = uuid.uuid4().hex[:8]
    admin_email, other_email = "live-admin@example.com", "live-other@example.com"
    admin = api.sign_in(admin_email, "Live Admin")
    made = api.call(
        "POST",
        "/users",
        admin,
        json={"email": other_email, "display_name": "Live Other", "password": PASSWORD,
              "role": "admin"},
    )
    assert made.status_code in (201, 400, 409), made.text

    resp = api.call(
        "PATCH",
        "/metamodel/types/Application",
        admin,
        json={
            "lifecycle_config": {
                "stages": [
                    {"key": s, "label": s.title(), "color": "#1976d2", "semantic": SEMANTIC[s]}
                    for s in STAGES
                ]
            }
        },
    )
    assert resp.status_code == 200, resp.text

    def make_cards(count: int, label: str, **extra) -> list[str]:
        body = {
            "cards": [
                {"row_index": i, "type": "Application", "name": f"{label} {run_id} {i:03d}",
                 **extra}
                for i in range(count)
            ]
        }
        made = api.call("POST", "/cards/bulk-create", admin, json=body)
        assert made.status_code == 200, made.text
        ids = [r["id"] for r in sorted(made.json()["results"], key=lambda r: r["row_index"])]
        assert all(ids), made.text
        return ids

    pool = make_cards(330, "App")
    take = iter(pool)

    def fresh(count: int) -> list[str]:
        return [next(take) for _ in range(count)]

    async with mcp_session(args, admin_email) as mcp, mcp_session(args, other_email) as other:
        # ── 1. Dry runs below, at and above the threshold ────────────────
        tokens: dict[int, tuple[list[dict], str | None]] = {}
        for size in (19, 20, 21, 40):
            rows = stage_rows(fresh(size), "core")
            before = db.snapshot()
            data = await mcp.tool(
                "update_cards_bulk", updates=rows, strict_attributes=True, dry_run=True
            )
            label = f"dry run, {size} rows"
            check(f"{label}: returns a preview", data.get("dry_run") is True, data)
            check(f"{label}: one result per row, in input order", *rows_in_order(data, rows))
            check(f"{label}: counts reconcile", *reconciles(data, size))
            check(f"{label}: would_update == {size}", data.get("would_update") == size, data)
            has_token = bool(data.get("confirm_token"))
            check(
                f"{label}: confirm_token {'issued' if size > 20 else 'not issued'}",
                has_token == (size > 20),
                {k: data.get(k) for k in ("confirm_token", "_tool_error", "error")},
            )
            check(f"{label}: no card changed", db.snapshot() == before)
            if data.get("batch_id"):
                batch = db.batch(data["batch_id"])
                check(
                    f"{label}: audit batch is a closed preview",
                    batch.get("dry_run") is True
                    and batch.get("closed") is True
                    and (batch.get("summary") or {}).get("status") == "previewed"
                    and batch.get("events") == 0,
                    batch,
                )
            tokens[size] = (rows, data.get("confirm_token"))

        # ── 2. Commit at the threshold needs no token ────────────────────
        rows, _ = tokens[20]
        ids = [r["card_id"] for r in rows]
        untouched = [i for i in pool if i not in ids]
        before_other = db.cards(untouched)
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=False)
        check("commit, 20 rows, no token: committed", data.get("committed") is True, data)
        check("commit, 20 rows: one result per row, in order", *rows_in_order(data, rows))
        check("commit, 20 rows: counts reconcile", *reconciles(data, 20))
        check("commit, 20 rows: updated == 20", data.get("updated") == 20, data)
        after = db.cards(ids)
        check(
            "commit, 20 rows: database has exactly those 20 at core",
            all(after[i]["lifecycle_stage"] == "core" for i in ids)
            and db.cards(untouched) == before_other,
        )
        if data.get("batch_id"):
            batch = db.batch(data["batch_id"])
            check(
                "commit, 20 rows: audit batch is a committed write with 20 events",
                batch.get("dry_run") is False
                and (batch.get("summary") or {}).get("status") == "committed"
                and batch.get("events") == 20,
                batch,
            )

        # ── 3. Token rules above the threshold ───────────────────────────
        rows21, token21 = tokens[21]
        rows40, token40 = tokens[40]

        async def refused(label: str, expected: str, **call) -> None:
            before = db.snapshot()
            data = await mcp.tool("update_cards_bulk", strict_attributes=True, dry_run=False,
                                  **call)
            check(f"{label}: refused with {expected}", data.get("error") == expected, data)
            check(f"{label}: no card changed", db.snapshot() == before)

        await refused("commit, 21 rows, missing token", "confirm_token_required", updates=rows21)
        await refused("commit, 21 rows, invalid token", "confirm_token_invalid",
                      updates=rows21, confirm_token="not-a-real-token")
        await refused("commit, 21 rows, token of the 40-row preview", "confirm_token_mismatch",
                      updates=rows21, confirm_token=token40 or "missing")
        changed = [dict(r) for r in rows21]
        changed[-1]["lifecycle_stage"] = "sunset"
        await refused("commit, 21 rows, payload edited after the preview",
                      "confirm_token_mismatch", updates=changed,
                      confirm_token=token21 or "missing")
        theirs = await other.tool("update_cards_bulk", updates=rows21, strict_attributes=True,
                                  dry_run=True)
        await refused("commit, 21 rows, another user's token", "confirm_token_invalid",
                      updates=rows21, confirm_token=theirs.get("confirm_token") or "missing")

        expiring = stage_rows(fresh(21), "heritage")
        data = await mcp.tool("update_cards_bulk", updates=expiring, strict_attributes=True,
                              dry_run=True)
        if data.get("batch_id"):
            db.execute(
                "update mutation_batches set created_at = created_at - interval '16 minutes' "
                f"where id = '{data['batch_id']}'"
            )
        await refused("commit, 21 rows, token older than 15 minutes", "confirm_token_expired",
                      updates=expiring, confirm_token=data.get("confirm_token") or "missing")

        ids = [r["card_id"] for r in rows21]
        untouched = [i for i in pool if i not in ids]
        before_other = db.cards(untouched)
        data = await mcp.tool("update_cards_bulk", updates=rows21, strict_attributes=True,
                              dry_run=False, confirm_token=token21 or "missing")
        check("commit, 21 rows, valid token: committed", data.get("committed") is True, data)
        check("commit, 21 rows: counts reconcile", *reconciles(data, 21))
        after = db.cards(ids)
        check(
            "commit, 21 rows: database has exactly those 21 at core",
            all(after[i]["lifecycle_stage"] == "core" for i in ids)
            and db.cards(untouched) == before_other,
        )
        await refused("commit, 21 rows, token replayed", "confirm_token_used",
                      updates=rows21, confirm_token=token21 or "missing")

        # ── 4. Mixed patch groups: nine Core and one Emerging ────────────
        ids = fresh(10)
        rows = stage_rows(ids, "core")
        rows[4]["lifecycle_stage"] = "emerging"  # mid-list, so regrouping would reorder it
        before = db.snapshot()
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=True)
        check("mixed 9+1 dry run: one result per row, in input order", *rows_in_order(data, rows))
        check("mixed 9+1 dry run: counts reconcile", *reconciles(data, 10))
        check(
            "mixed 9+1 dry run: each row previews its own stage",
            [(r.get("after") or {}).get("lifecycle_stage") for r in data.get("results") or []]
            == [r["lifecycle_stage"] for r in rows],
            data.get("results"),
        )
        check("mixed 9+1 dry run: no card changed", db.snapshot() == before)
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=False)
        check("mixed 9+1 commit: one result per row, in input order", *rows_in_order(data, rows))
        check("mixed 9+1 commit: counts reconcile", *reconciles(data, 10))
        check(
            "mixed 9+1 commit: updated == 10 and every row says updated",
            data.get("updated") == 10
            and [r.get("status") for r in data.get("results") or []] == ["updated"] * 10,
            data,
        )
        after = db.cards(ids)
        check(
            "mixed 9+1 commit: database matches every row",
            [after[r["card_id"]]["lifecycle_stage"] for r in rows]
            == [r["lifecycle_stage"] for r in rows],
        )

        # ── 5. The reported case: 40 rows, mixed, strict, preview → commit ─
        ids = fresh(40)
        rows = [
            {"card_id": cid, "lifecycle_stage": STAGES[i % len(STAGES)]}
            for i, cid in enumerate(ids)
        ]
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=True)
        check("40 mixed rows dry run: preview and token returned",
              data.get("dry_run") is True and bool(data.get("confirm_token")), data)
        check("40 mixed rows dry run: one result per row, in order", *rows_in_order(data, rows))
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=False, confirm_token=data.get("confirm_token") or "missing")
        check("40 mixed rows commit: committed, updated == 40",
              data.get("committed") is True and data.get("updated") == 40, data)
        check("40 mixed rows commit: counts reconcile", *reconciles(data, 40))
        after = db.cards(ids)
        check(
            "40 mixed rows commit: database matches every row",
            [after[r["card_id"]]["lifecycle_stage"] for r in rows]
            == [r["lifecycle_stage"] for r in rows],
        )

        # ── 6. An invalid row in a later group rolls everything back ─────
        ids = fresh(5)
        rows = stage_rows(ids, "heritage")
        rows[4]["lifecycle_stage"] = "no-such-stage"
        before = db.snapshot()
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=True)
        statuses = [r.get("status") for r in data.get("results") or []]
        check("invalid later row, dry run: that row is an error, the rest preview",
              statuses == ["would_update"] * 4 + ["error"] and data.get("failed") == 1, data)
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=False)
        statuses = [r.get("status") for r in data.get("results") or []]
        check("invalid later row, commit: refused, nothing committed",
              data.get("committed") is False
              and statuses == ["not_applied"] * 4 + ["error"], data)
        check("invalid later row, commit: counts reconcile", *reconciles(data, 5))
        check("invalid later row, commit: database unchanged (earlier group rolled back)",
              db.snapshot() == before, db.cards(ids))
        if data.get("batch_id"):
            batch = db.batch(data["batch_id"])
            check("invalid later row, commit: audit batch is a failed write with no events",
                  batch.get("dry_run") is False
                  and (batch.get("summary") or {}).get("status") == "failed"
                  and batch.get("events") == 0, batch)

        big = stage_rows(fresh(25), "heritage")
        big[24]["lifecycle_stage"] = "no-such-stage"
        data = await mcp.tool("update_cards_bulk", updates=big, dry_run=True)
        check("invalid row above the threshold: the preview issues no token",
              data.get("failed") == 1 and not data.get("confirm_token"), data)

        # A clash only the write can see: two same-named cards moved under one
        # parent by different patches. The first group is already applied in
        # the transaction when the second is refused.
        parent_a, parent_b = make_cards(2, "Parent")
        clash_name = f"Clash {run_id}"
        first = api.call("POST", "/cards", admin, json={"type": "Application", "name": clash_name})
        second = api.call("POST", "/cards", admin,
                          json={"type": "Application", "name": clash_name, "parent_id": parent_a})
        assert first.status_code in (200, 201) and second.status_code in (200, 201), (
            first.text, second.text)
        x, y = first.json()["id"], second.json()["id"]
        rows = [
            {"card_id": x, "parent_id": parent_b, "description": "moved first"},
            {"card_id": y, "parent_id": parent_b, "description": "moved second"},
        ]
        before = db.snapshot()
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=False)
        check("clash in the second group at write time: refused",
              data.get("committed") is False, data)
        check("clash in the second group: the first group's card is rolled back",
              db.snapshot() == before, db.cards([x, y]))

        # ── 7. Unchanged rows ────────────────────────────────────────────
        ids = fresh(3)
        await mcp.tool("update_cards_bulk", updates=stage_rows(ids[:2], "sunset"), dry_run=False)
        rows = stage_rows(ids, "sunset")
        stamps = db.cards(ids)
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=True)
        statuses = [r.get("status") for r in data.get("results") or []]
        check("unchanged rows, dry run: reported as unchanged",
              statuses == ["unchanged", "unchanged", "would_update"], data)
        check("unchanged rows, dry run: counts reconcile", *reconciles(data, 3))
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=False)
        statuses = [r.get("status") for r in data.get("results") or []]
        check("unchanged rows, commit: updated == 1, unchanged == 2",
              statuses == ["unchanged", "unchanged", "updated"]
              and data.get("updated") == 1 and data.get("unchanged") == 2, data)
        check("unchanged rows, commit: counts reconcile", *reconciles(data, 3))
        after = db.cards(ids)
        check("unchanged rows, commit: their updated_at did not move",
              all(after[i]["updated_at"] == stamps[i]["updated_at"] for i in ids[:2])
              and after[ids[2]]["lifecycle_stage"] == "sunset")

        # ── 8. Malformed calls ───────────────────────────────────────────
        ids = fresh(2)
        before = db.snapshot()
        data = await mcp.tool(
            "update_cards_bulk", dry_run=False,
            updates=[{"card_id": ids[0], "lifecycle_stage": "core"},
                     {"card_id": ids[1], "lifecycle_stage": "core"},
                     {"card_id": ids[0], "lifecycle_stage": "sunset"}])
        check("duplicate card_id: refused, naming rows 0 and 2",
              data.get("error") == "duplicate_card_id"
              and (data.get("duplicates") or [{}])[0].get("row_indexes") == [0, 2], data)
        data = await mcp.tool(
            "update_cards_bulk", dry_run=False,
            updates=[{"card_id": ids[0], "lifecycle_stagee": "core"}])
        check("misspelt field: refused as unknown_fields",
              data.get("error") == "unknown_fields", data)
        rows = [{"card_id": ids[0], "lifecycle_stage": "core"},
                {"card_id": str(uuid.uuid4()), "lifecycle_stage": "core"}]
        data = await mcp.tool("update_cards_bulk", updates=rows, dry_run=False)
        statuses = [r.get("status") for r in data.get("results") or []]
        check("unknown card: refused, row 1 is the error",
              data.get("committed") is False and statuses == ["not_applied", "error"], data)
        check("malformed calls: no card changed", db.snapshot() == before)

        # ── 9. Audit trail through the MCP tool ──────────────────────────
        ids = fresh(2)
        data = await mcp.tool("update_cards_bulk", updates=stage_rows(ids, "core"), dry_run=False)
        history = await mcp.tool("get_change_history", batch_id=data.get("batch_id") or "")
        events = history.get("events") or []
        check("get_change_history: the commit's batch lists one event per updated card",
              sorted(e.get("card_id") for e in events) == sorted(ids)
              and (history.get("batch") or {}).get("dry_run") is False, history)

        # ── 10. The same batch helper in the other bulk tools ────────────
        new_cards = [{"row_index": i, "type": "Application", "name": f"New {run_id} {i:03d}"}
                     for i in range(25)]
        before = db.snapshot()
        data = await mcp.tool("create_cards_bulk", cards=new_cards, dry_run=True)
        check("create_cards_bulk, 25 rows dry run: preview and token returned",
              data.get("dry_run") is True and bool(data.get("confirm_token")), data)
        check("create_cards_bulk dry run: no card created", db.snapshot() == before)
        bad = await mcp.tool("create_cards_bulk", cards=new_cards, dry_run=False,
                             confirm_token="not-a-real-token")
        check("create_cards_bulk commit, invalid token: refused, nothing created",
              bad.get("error") == "confirm_token_invalid" and db.snapshot() == before, bad)
        data = await mcp.tool("create_cards_bulk", cards=new_cards, dry_run=False,
                              confirm_token=data.get("confirm_token") or "missing")
        check("create_cards_bulk commit, valid token: 25 created",
              data.get("created") == 25, data)

        ids = fresh(21)
        before = db.snapshot()
        data = await mcp.tool("archive_cards", card_ids=ids, dry_run=True)
        check("archive_cards, 21 rows dry run: preview and token returned",
              data.get("dry_run") is True and bool(data.get("confirm_token")), data)
        bad = await mcp.tool("archive_cards", card_ids=ids, dry_run=False,
                             confirm_token="not-a-real-token")
        check("archive_cards commit, invalid token: refused, nothing archived",
              bad.get("error") == "confirm_token_invalid" and db.snapshot() == before, bad)
        data = await mcp.tool("clear_card_logos", card_ids=ids, dry_run=True)
        check("clear_card_logos, 21 rows dry run: token returned",
              bool(data.get("confirm_token")), data)
        bad = await mcp.tool("clear_card_logos", card_ids=ids, dry_run=False,
                             confirm_token="not-a-real-token")
        check("clear_card_logos commit, invalid token: refused",
              bad.get("error") == "confirm_token_invalid", bad)

        # ── 11. The per-call cap, inside the client's 30 s timeout ───────
        ids = fresh(100)
        rows = [{"card_id": cid, "lifecycle_stage": STAGES[i % len(STAGES)]}
                for i, cid in enumerate(ids)]
        started = time.monotonic()
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=True)
        preview_seconds = time.monotonic() - started
        started = time.monotonic()
        data = await mcp.tool("update_cards_bulk", updates=rows, strict_attributes=True,
                              dry_run=False, confirm_token=data.get("confirm_token") or "missing")
        commit_seconds = time.monotonic() - started
        check(f"100 mixed rows: committed (preview {preview_seconds:.1f}s, "
              f"commit {commit_seconds:.1f}s)",
              data.get("committed") is True and data.get("updated") == 100
              and max(preview_seconds, commit_seconds) < 30, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--db-dsn", required=True)
    parser.add_argument("--mcp-src", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    asyncio.run(run(args))
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
