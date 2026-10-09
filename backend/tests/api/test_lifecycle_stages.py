"""Configurable lifecycle stages, per card type.

Pinned here, in order of how much damage a regression would do:

* a type that never configures stages behaves exactly as before;
* a card's explicit stage is recorded without any date, and a card with neither
  stays *unknown* — nothing defaults it to an operational stage;
* a stage that cards use cannot be dropped from the vocabulary without saying
  where those cards go, and a reassignment never discards a date.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.permissions import MEMBER_PERMISSIONS
from app.models.card import Card
from app.services.card_write_service import _check_lifecycle_stage
from tests.conftest import auth_headers, create_card, create_card_type, create_role, create_user


def _stage(key, semantic, label=None):
    return {"key": key, "label": label or key.title(), "color": "#2e7d32", "semantic": semantic}


RADAR = {
    "stages": [
        _stage("evaluating", "pre_operational"),
        _stage("emerging", "operational"),
        _stage("core", "operational"),
        _stage("heritage", "operational"),
        _stage("sunset", "retiring"),
        _stage("discontinued", "retired"),
    ]
}
RADAR_KEYS = ["evaluating", "emerging", "core", "heritage", "sunset", "discontinued"]
DEFAULT_KEYS = ["plan", "phaseIn", "active", "phaseOut", "endOfLife"]


def _without(*keys):
    return {"stages": [s for s in RADAR["stages"] if s["key"] not in keys]}


class TestCheckLifecycleStage:
    def test_accepts_a_defined_stage(self):
        _check_lifecycle_stage("Application", RADAR, "core", None)

    def test_rejects_an_undefined_stage(self):
        with pytest.raises(HTTPException) as exc:
            _check_lifecycle_stage("Application", RADAR, "active", None)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "invalid_lifecycle_stage"
        assert exc.value.detail["valid_stages"] == RADAR_KEYS
        assert exc.value.detail["card_type"] == "Application"
        assert "'active'" in exc.value.detail["message"]

    @pytest.mark.parametrize("config", [None, {}])
    def test_unconfigured_type_uses_the_built_in_phases(self, config):
        _check_lifecycle_stage("Application", config, "phaseOut", None)
        with pytest.raises(HTTPException) as exc:
            _check_lifecycle_stage("Application", config, "core", None)
        assert exc.value.detail["valid_stages"] == DEFAULT_KEYS

    @pytest.mark.parametrize("empty", [None, ""])
    def test_clearing_always_passes(self, empty):
        _check_lifecycle_stage("Application", RADAR, empty, "core")

    def test_unchanged_legacy_value_is_grandfathered(self):
        _check_lifecycle_stage("Application", RADAR, "active", "active")

    def test_changing_a_legacy_value_to_another_bad_one_still_fails(self):
        with pytest.raises(HTTPException):
            _check_lifecycle_stage("Application", RADAR, "worse", "active")


@pytest.fixture
async def env(db):
    await create_role(db, key="admin", label="Admin", permissions={"*": True})
    await create_role(db, key="member", label="Member", permissions=MEMBER_PERMISSIONS)
    await create_card_type(db, key="Application", label="Application", lifecycle_config=RADAR)
    await create_card_type(db, key="Interface", label="Interface")
    admin = await create_user(db, email="admin@test.com", role="admin")
    member = await create_user(db, email="member@test.com", role="member")
    return {"admin": admin, "member": member}


async def _patch_type(client, user, key, body):
    return await client.patch(
        f"/api/v1/metamodel/types/{key}", json=body, headers=auth_headers(user)
    )


async def _get_type(client, user, key):
    response = await client.get(f"/api/v1/metamodel/types/{key}", headers=auth_headers(user))
    assert response.status_code == 200
    return response.json()


class TestVocabularyApi:
    async def test_unconfigured_type_reports_the_built_in_stages(self, client, db, env):
        body = await _get_type(client, env["admin"], "Interface")
        assert body["lifecycle_config"] == {}
        assert [s["key"] for s in body["lifecycle_stages"]] == DEFAULT_KEYS

    async def test_configured_type_reports_its_own_stages(self, client, db, env):
        body = await _get_type(client, env["admin"], "Application")
        assert [s["key"] for s in body["lifecycle_stages"]] == RADAR_KEYS
        assert body["lifecycle_stages"][4]["semantic"] == "retiring"

    async def test_admin_sets_a_vocabulary(self, client, db, env):
        response = await _patch_type(client, env["admin"], "Interface", {"lifecycle_config": RADAR})
        assert response.status_code == 200
        assert [s["key"] for s in response.json()["lifecycle_config"]["stages"]] == RADAR_KEYS
        assert [s["key"] for s in response.json()["lifecycle_stages"]] == RADAR_KEYS

    async def test_create_type_with_a_vocabulary(self, client, db, env):
        response = await client.post(
            "/api/v1/metamodel/types",
            json={"key": "Robot", "label": "Robot", "lifecycle_config": RADAR},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code in (200, 201)
        assert [s["key"] for s in response.json()["lifecycle_stages"]] == RADAR_KEYS

    async def test_create_type_rejects_a_bad_vocabulary(self, client, db, env):
        response = await client.post(
            "/api/v1/metamodel/types",
            json={
                "key": "Robot",
                "label": "Robot",
                "lifecycle_config": {"stages": [_stage("core", "live")]},
            },
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 400
        assert "semantic" in response.json()["detail"]

    async def test_rejects_a_bad_vocabulary(self, client, db, env):
        response = await _patch_type(
            client,
            env["admin"],
            "Interface",
            {
                "lifecycle_config": {
                    "stages": [_stage("core", "operational"), _stage("core", "retired")]
                }
            },
        )
        assert response.status_code == 400
        assert "Duplicate" in response.json()["detail"]
        assert (await _get_type(client, env["admin"], "Interface"))["lifecycle_config"] == {}

    async def test_member_cannot_change_the_vocabulary(self, client, db, env):
        response = await _patch_type(
            client, env["member"], "Interface", {"lifecycle_config": RADAR}
        )
        assert response.status_code == 403

    async def test_a_save_without_the_key_leaves_the_vocabulary_alone(self, client, db, env):
        response = await _patch_type(client, env["admin"], "Application", {"label": "App"})
        assert response.status_code == 200
        assert [s["key"] for s in response.json()["lifecycle_stages"]] == RADAR_KEYS

    async def test_relabel_recolour_and_reorder_are_free(self, client, db, env):
        await create_card(db, card_type="Application", name="A", lifecycle_stage="core")
        stages = [dict(s) for s in reversed(RADAR["stages"])]
        stages[0]["label"] = "Gone"
        stages[0]["color"] = "#000000"
        response = await _patch_type(
            client, env["admin"], "Application", {"lifecycle_config": {"stages": stages}}
        )
        assert response.status_code == 200
        assert response.json()["lifecycle_stages"][0] == {
            "key": "discontinued",
            "label": "Gone",
            "color": "#000000",
            "semantic": "retired",
            "translations": {},
        }


class TestRemovingStages:
    async def test_an_unused_stage_can_be_removed(self, client, db, env):
        await create_card(db, card_type="Application", name="A", lifecycle_stage="core")
        # Another type using the same key must not block this one.
        await create_card(db, card_type="Interface", name="I", lifecycle={"heritage": "2020-01-01"})
        response = await _patch_type(
            client, env["admin"], "Application", {"lifecycle_config": _without("heritage")}
        )
        assert response.status_code == 200

    async def test_a_stage_in_explicit_use_cannot_be_removed(self, client, db, env):
        await create_card(db, card_type="Application", name="A", lifecycle_stage="heritage")
        await create_card(
            db, card_type="Application", name="B", lifecycle_stage="heritage", status="ARCHIVED"
        )
        response = await _patch_type(
            client, env["admin"], "Application", {"lifecycle_config": _without("heritage")}
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "lifecycle_stage_in_use"
        assert detail["in_use"] == {"heritage": 2}
        assert "heritage" not in detail["valid_stages"]
        assert "'heritage' (2 card(s))" in detail["message"]
        stages = (await _get_type(client, env["admin"], "Application"))["lifecycle_stages"]
        assert [s["key"] for s in stages] == RADAR_KEYS

    async def test_a_dated_stage_cannot_be_removed(self, client, db, env):
        await create_card(
            db, card_type="Application", name="A", lifecycle={"sunset": "2027-01-01", "core": ""}
        )
        response = await _patch_type(
            client, env["admin"], "Application", {"lifecycle_config": _without("sunset")}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["in_use"] == {"sunset": 1}
        # An empty value is not a date: `core` is not in use.
        response = await _patch_type(
            client, env["admin"], "Application", {"lifecycle_config": _without("core")}
        )
        assert response.status_code == 200

    async def test_resetting_to_the_built_in_model_is_guarded_too(self, client, db, env):
        await create_card(db, card_type="Application", name="A", lifecycle_stage="core")
        response = await _patch_type(client, env["admin"], "Application", {"lifecycle_config": {}})
        assert response.status_code == 400
        assert response.json()["detail"]["valid_stages"] == DEFAULT_KEYS

    @pytest.mark.parametrize("target", ["heritage", "nowhere", None])
    async def test_reassignment_must_name_a_remaining_stage(self, client, db, env, target):
        await create_card(db, card_type="Application", name="A", lifecycle_stage="heritage")
        response = await _patch_type(
            client,
            env["admin"],
            "Application",
            {"lifecycle_config": _without("heritage"), "lifecycle_reassign": {"heritage": target}},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "lifecycle_stage_in_use"

    async def test_reassignment_moves_the_stage_and_keeps_the_date(self, client, db, env):
        explicit = await create_card(
            db, card_type="Application", name="A", lifecycle_stage="heritage"
        )
        dated = await create_card(
            db,
            card_type="Application",
            name="B",
            lifecycle={"heritage": "2021-03-04", "core": "2019-01-01", "sunset": ""},
        )
        untouched = await create_card(
            db,
            card_type="Application",
            name="C",
            lifecycle_stage="core",
            lifecycle={"heritage": ""},
        )
        other_type = await create_card(
            db, card_type="Interface", name="I", lifecycle={"heritage": "2020-01-01"}
        )
        response = await _patch_type(
            client,
            env["admin"],
            "Application",
            {
                "lifecycle_config": _without("heritage"),
                "lifecycle_reassign": {"heritage": "sunset"},
            },
        )
        assert response.status_code == 200
        for card in (explicit, dated, untouched, other_type):
            await db.refresh(card)
        assert explicit.lifecycle_stage == "sunset"
        assert dated.lifecycle == {"core": "2019-01-01", "sunset": "2021-03-04"}
        assert dated.lifecycle_stage is None
        assert untouched.lifecycle_stage == "core"
        assert untouched.lifecycle == {}
        assert other_type.lifecycle == {"heritage": "2020-01-01"}

    async def test_reassignment_never_discards_a_date(self, client, db, env):
        card = await create_card(
            db,
            card_type="Application",
            name="A",
            lifecycle={"heritage": "2021-03-04", "sunset": "2025-01-01"},
        )
        response = await _patch_type(
            client,
            env["admin"],
            "Application",
            {
                "lifecycle_config": _without("heritage"),
                "lifecycle_reassign": {"heritage": "sunset"},
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "lifecycle_reassign_conflict"
        assert (detail["from"], detail["to"], detail["cards"]) == ("heritage", "sunset", 1)
        await db.refresh(card)
        assert card.lifecycle == {"heritage": "2021-03-04", "sunset": "2025-01-01"}


class TestCardStage:
    async def test_create_records_a_stage_without_any_date(self, client, db, env):
        response = await client.post(
            "/api/v1/cards",
            json={"type": "Application", "name": "Ledger", "lifecycle_stage": "heritage"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 201
        assert response.json()["lifecycle_stage"] == "heritage"
        assert response.json()["lifecycle"] == {}

    async def test_create_without_a_stage_stays_unknown(self, client, db, env):
        response = await client.post(
            "/api/v1/cards",
            json={"type": "Application", "name": "Mystery"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 201
        assert response.json()["lifecycle_stage"] is None
        assert response.json()["lifecycle"] == {}

    async def test_create_rejects_an_undefined_stage(self, client, db, env):
        response = await client.post(
            "/api/v1/cards",
            json={"type": "Application", "name": "Ledger", "lifecycle_stage": "active"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_lifecycle_stage"

    async def test_unconfigured_type_takes_a_built_in_stage(self, client, db, env):
        response = await client.post(
            "/api/v1/cards",
            json={"type": "Interface", "name": "Feed", "lifecycle_stage": "phaseOut"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 201
        assert response.json()["lifecycle_stage"] == "phaseOut"

    async def test_update_sets_changes_and_clears_the_stage(self, client, db, env):
        card = await create_card(
            db, card_type="Application", name="A", lifecycle={"core": "2020-01-01"}
        )
        url = f"/api/v1/cards/{card.id}"
        headers = auth_headers(env["admin"])
        response = await client.patch(url, json={"lifecycle_stage": "sunset"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["lifecycle_stage"] == "sunset"
        # The dates are a separate fact and are not rewritten.
        assert response.json()["lifecycle"] == {"core": "2020-01-01"}
        for empty in (None, ""):
            await client.patch(url, json={"lifecycle_stage": "sunset"}, headers=headers)
            response = await client.patch(url, json={"lifecycle_stage": empty}, headers=headers)
            assert response.status_code == 200
            assert response.json()["lifecycle_stage"] is None

    async def test_update_rejects_an_undefined_stage(self, client, db, env):
        card = await create_card(db, card_type="Application", name="A", lifecycle_stage="core")
        response = await client.patch(
            f"/api/v1/cards/{card.id}",
            json={"lifecycle_stage": "active"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 422
        await db.refresh(card)
        assert card.lifecycle_stage == "core"

    async def test_a_card_with_a_legacy_stage_stays_editable(self, client, db, env):
        card = await create_card(db, card_type="Application", name="A", lifecycle_stage="active")
        response = await client.patch(
            f"/api/v1/cards/{card.id}",
            json={"name": "Renamed", "lifecycle_stage": "active"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        assert response.json()["lifecycle_stage"] == "active"

    async def test_changing_the_stage_breaks_an_approval(self, client, db, env):
        card = await create_card(
            db,
            card_type="Application",
            name="A",
            lifecycle_stage="core",
            approval_status="APPROVED",
        )
        response = await client.patch(
            f"/api/v1/cards/{card.id}",
            json={"lifecycle_stage": "sunset"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        assert response.json()["approval_status"] == "BROKEN"

    async def test_the_stage_does_not_touch_the_card_status(self, client, db, env):
        card = await create_card(db, card_type="Application", name="A")
        response = await client.patch(
            f"/api/v1/cards/{card.id}",
            json={"lifecycle_stage": "discontinued"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ACTIVE"
        assert response.json()["archived_at"] is None


class TestBulk:
    async def test_bulk_update_sets_the_stage(self, client, db, env):
        a = await create_card(db, card_type="Application", name="A")
        b = await create_card(db, card_type="Application", name="B", lifecycle_stage="core")
        response = await client.patch(
            "/api/v1/cards/bulk",
            json={"ids": [str(a.id), str(b.id)], "updates": {"lifecycle_stage": "sunset"}},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        for card in (a, b):
            await db.refresh(card)
            assert card.lifecycle_stage == "sunset"

    async def test_bulk_update_clears_the_stage(self, client, db, env):
        a = await create_card(db, card_type="Application", name="A", lifecycle_stage="core")
        response = await client.patch(
            "/api/v1/cards/bulk",
            json={"ids": [str(a.id)], "updates": {"lifecycle_stage": ""}},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        await db.refresh(a)
        assert a.lifecycle_stage is None

    async def test_bulk_update_rejects_a_stage_one_of_the_types_lacks(self, client, db, env):
        a = await create_card(db, card_type="Application", name="A")
        i = await create_card(db, card_type="Interface", name="I")
        response = await client.patch(
            "/api/v1/cards/bulk",
            json={"ids": [str(a.id), str(i.id)], "updates": {"lifecycle_stage": "core"}},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["card_type"] == "Interface"
        await db.refresh(a)
        assert a.lifecycle_stage is None

    async def test_import_keeps_stages_and_leaves_blanks_unknown(self, client, db, env):
        """The migration acceptance rule: source values land unchanged, no date
        is fabricated, and a blank source value does not become a stage."""
        response = await client.post(
            "/api/v1/cards/bulk-create",
            json={
                "cards": [
                    {
                        "row_index": 0,
                        "type": "Application",
                        "name": "Ledger",
                        "lifecycle_stage": "heritage",
                    },
                    {"row_index": 1, "type": "Application", "name": "Mystery"},
                    {"row_index": 2, "type": "Application", "name": "Blank", "lifecycle_stage": ""},
                ]
            },
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 3
        rows = (await db.execute(Card.__table__.select().where(Card.type == "Application"))).all()
        by_name = {r.name: r for r in rows}
        assert by_name["Ledger"].lifecycle_stage == "heritage"
        assert by_name["Mystery"].lifecycle_stage is None
        assert by_name["Blank"].lifecycle_stage is None
        assert all(r.lifecycle == {} for r in rows)

    async def test_import_reports_an_undefined_stage(self, client, db, env):
        response = await client.post(
            "/api/v1/cards/bulk-create",
            json={
                "cards": [
                    {
                        "row_index": 0,
                        "type": "Application",
                        "name": "Bad",
                        "lifecycle_stage": "active",
                    }
                ]
            },
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200
        body = response.json()
        assert (body["created"], body["failed"]) == (0, 1)
        assert "does not define lifecycle stage 'active'" in body["results"][0]["error"]
        rows = (await db.execute(Card.__table__.select().where(Card.name == "Bad"))).all()
        assert rows == []


class TestCardStatusIsNotALifecycle:
    @pytest.mark.parametrize("status", ["PHASING_OUT", "END_OF_LIFE", "archived", ""])
    async def test_status_takes_only_its_two_values(self, client, db, env, status):
        card = await create_card(db, card_type="Application", name="A")
        response = await client.patch(
            f"/api/v1/cards/{card.id}", json={"status": status}, headers=auth_headers(env["admin"])
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_card_status"
        assert detail["valid_statuses"] == ["ACTIVE", "ARCHIVED"]
        assert "lifecycle_stage" in detail["message"]
        await db.refresh(card)
        assert card.status == "ACTIVE"

    async def test_a_real_status_still_passes(self, client, db, env):
        card = await create_card(db, card_type="Application", name="A")
        response = await client.patch(
            f"/api/v1/cards/{card.id}",
            json={"status": "ACTIVE"},
            headers=auth_headers(env["admin"]),
        )
        assert response.status_code == 200


class TestReports:
    async def test_dashboard_counts_stages_by_what_they_mean(self, client, db, env):
        for name, stage in [
            ("a", "evaluating"),
            ("b", "emerging"),
            ("c", "core"),
            ("d", "heritage"),
            ("e", "sunset"),
            ("f", "discontinued"),
        ]:
            await create_card(db, card_type="Application", name=name, lifecycle_stage=stage)
        # Blank stays unknown — it is not counted as active.
        await create_card(db, card_type="Application", name="blank")
        await create_card(
            db, card_type="Application", name="dated", lifecycle={"sunset": "2000-01-01"}
        )
        await create_card(db, card_type="Interface", name="i", lifecycle={"phaseIn": "2000-01-01"})
        await create_card(db, card_type="Interface", name="j", lifecycle_stage="active")
        response = await client.get("/api/v1/reports/dashboard", headers=auth_headers(env["admin"]))
        assert response.status_code == 200
        assert response.json()["lifecycle_distribution"] == {
            "plan": 1,
            "phaseIn": 1,
            "active": 4,
            "phaseOut": 2,
            "endOfLife": 1,
            "none": 1,
        }

    async def test_roadmap_reads_custom_stage_dates_as_built_in_phases(self, client, db, env):
        await create_card(
            db,
            card_type="Application",
            name="Ledger",
            lifecycle={"core": "2020-01-01", "discontinued": "2030-01-01"},
        )
        response = await client.get("/api/v1/reports/roadmap", headers=auth_headers(env["admin"]))
        assert response.status_code == 200
        items = response.json()["items"]
        ledger = next(i for i in items if i["name"] == "Ledger")
        assert ledger["lifecycle"] == {"active": "2020-01-01", "endOfLife": "2030-01-01"}

    async def test_roadmap_has_no_row_for_a_card_with_only_a_stated_stage(self, client, db, env):
        await create_card(db, card_type="Application", name="Gone", lifecycle_stage="discontinued")
        response = await client.get("/api/v1/reports/roadmap", headers=auth_headers(env["admin"]))
        assert [i for i in response.json()["items"] if i["name"] == "Gone"] == []


class TestExports:
    async def test_json_export_carries_the_stage(self, client, db, env):
        await create_card(db, card_type="Application", name="Ledger", lifecycle_stage="heritage")
        response = await client.get(
            "/api/v1/cards/export/json?types=Application", headers=auth_headers(env["admin"])
        )
        assert response.status_code == 200, response.text
        body = response.json()
        rows = body["items"] if isinstance(body, dict) else body
        assert rows[0]["lifecycle_stage"] == "heritage"

    async def test_csv_export_carries_the_stage(self, client, db, env):
        await create_card(db, card_type="Application", name="Ledger", lifecycle_stage="heritage")
        await create_card(db, card_type="Application", name="Mystery")
        response = await client.get(
            "/api/v1/cards/export/csv?type=Application", headers=auth_headers(env["admin"])
        )
        assert response.status_code == 200, response.text
        import csv
        import io

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert {r["name"]: r["lifecycle_stage"] for r in rows} == {
            "Ledger": "heritage",
            "Mystery": "",
        }
