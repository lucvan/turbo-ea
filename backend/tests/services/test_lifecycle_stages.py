"""The lifecycle stage vocabulary — pure rules, no database."""

from __future__ import annotations

from datetime import date

import pytest

from app.services import lifecycle_stages as ls

TODAY = date(2026, 6, 1)

RADAR = {
    "stages": [
        {
            "key": "evaluating",
            "label": "Evaluating",
            "color": "#9E9E9E",
            "semantic": "pre_operational",
        },
        {"key": "emerging", "label": "Emerging", "color": "#1976d2", "semantic": "operational"},
        {"key": "core", "label": " Core ", "color": "#2e7d32", "semantic": "operational"},
        {"key": "heritage", "label": "Heritage", "color": "#8d6e63", "semantic": "operational"},
        {"key": "sunset", "label": "Sunset", "color": "#ed6c02", "semantic": "retiring"},
        {"key": "discontinued", "label": "Discontinued", "color": "#c62828", "semantic": "retired"},
    ]
}


def _stage(**over):
    return {"key": "core", "label": "Core", "color": "#2e7d32", "semantic": "operational", **over}


class TestValidate:
    @pytest.mark.parametrize("empty", [None, {}, {"stages": []}, {"stages": None}])
    def test_empty_means_the_built_in_model(self, empty):
        assert ls.validate_lifecycle_config(empty) == {}

    def test_normalises_a_vocabulary_and_keeps_its_order(self):
        out = ls.validate_lifecycle_config(RADAR)
        assert [s["key"] for s in out["stages"]] == [
            "evaluating",
            "emerging",
            "core",
            "heritage",
            "sunset",
            "discontinued",
        ]
        assert out["stages"][0]["color"] == "#9e9e9e"
        assert out["stages"][2]["label"] == "Core"
        assert out["stages"][2]["translations"] == {}

    def test_keeps_translations(self):
        out = ls.validate_lifecycle_config({"stages": [_stage(translations={"de": "Kern"})]})
        assert out["stages"][0]["translations"] == {"de": "Kern"}

    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ([], "must be an object"),
            ({"stages": "core"}, "must be a list"),
            ({"stages": ["core"]}, "must be an object"),
            ({"stages": [_stage(key="1core")]}, "Invalid lifecycle stage key"),
            ({"stages": [_stage(key="has space")]}, "Invalid lifecycle stage key"),
            ({"stages": [_stage(key="k" * 51)]}, "Invalid lifecycle stage key"),
            ({"stages": [_stage(key=None)]}, "Invalid lifecycle stage key"),
            ({"stages": [_stage(), _stage()]}, "Duplicate lifecycle stage key 'core'"),
            ({"stages": [_stage(label="  ")]}, "needs a label"),
            ({"stages": [_stage(label=3)]}, "needs a label"),
            ({"stages": [_stage(color="green")]}, "needs a colour"),
            ({"stages": [_stage(color="#12345")]}, "needs a colour"),
            ({"stages": [_stage(semantic="live")]}, "needs a semantic"),
            ({"stages": [_stage(translations=["de"])]}, "translations must be an object"),
        ],
    )
    def test_rejects(self, payload, fragment):
        with pytest.raises(ls.LifecycleConfigError) as exc:
            ls.validate_lifecycle_config(payload)
        assert fragment in str(exc.value)

    def test_accepts_the_longest_key_and_the_most_stages(self):
        stages = [_stage(key=f"s{i}") for i in range(ls.MAX_STAGES - 1)] + [_stage(key="k" * 50)]
        assert len(ls.validate_lifecycle_config({"stages": stages})["stages"]) == ls.MAX_STAGES

    def test_rejects_one_stage_too_many(self):
        stages = [_stage(key=f"s{i}") for i in range(ls.MAX_STAGES + 1)]
        with pytest.raises(ls.LifecycleConfigError, match="at most 12"):
            ls.validate_lifecycle_config({"stages": stages})


class TestVocabulary:
    def test_default_model_is_the_five_stored_phases(self):
        stages = ls.stages_for({})
        assert ls.stage_keys(stages) == ["plan", "phaseIn", "active", "phaseOut", "endOfLife"]
        assert [s["semantic"] for s in stages] == [
            "pre_operational",
            "pre_operational",
            "operational",
            "retiring",
            "retired",
        ]

    def test_default_model_is_a_copy(self):
        ls.stages_for(None)[0]["key"] = "mutated"
        assert ls.stages_for(None)[0]["key"] == "plan"

    def test_custom_vocabulary_replaces_the_default(self):
        assert ls.stage_keys(ls.stages_for(RADAR))[0] == "evaluating"
        assert ls.is_custom(RADAR) is True
        assert ls.is_custom({}) is False
        assert ls.is_custom(None) is False

    def test_semantic_of(self):
        stages = ls.stages_for(RADAR)
        assert ls.semantic_of(stages, "heritage") == "operational"
        assert ls.semantic_of(stages, "sunset") == "retiring"
        assert ls.semantic_of(stages, "nope") is None
        assert ls.semantic_of(stages, None) is None

    def test_keys_with_semantic(self):
        stages = ls.stages_for(RADAR)
        assert ls.keys_with_semantic(stages, "operational") == ["emerging", "core", "heritage"]
        assert ls.keys_with_semantic(stages, "retiring", "retired") == ["sunset", "discontinued"]

    def test_removed_stage_keys(self):
        smaller = {"stages": [s for s in RADAR["stages"] if s["key"] != "heritage"]}
        assert ls.removed_stage_keys(RADAR, smaller) == {"heritage"}
        assert ls.removed_stage_keys(RADAR, RADAR) == set()
        # Going from the built-in model to a custom one drops all five phases.
        assert ls.removed_stage_keys({}, RADAR) == {
            "plan",
            "phaseIn",
            "active",
            "phaseOut",
            "endOfLife",
        }


class TestCurrentStage:
    stages = ls.stages_for(RADAR)
    default = ls.stages_for(None)

    def test_unknown_when_nothing_is_recorded(self):
        assert ls.current_stage({}, None, self.stages, TODAY) is None
        assert ls.current_stage(None, None, self.stages, TODAY) is None
        assert ls.current_stage({}, "", self.stages, TODAY) is None

    def test_explicit_stage_needs_no_date(self):
        assert ls.current_stage({}, "heritage", self.stages, TODAY) == "heritage"

    def test_explicit_stage_wins_over_dates(self):
        lifecycle = {"core": "2020-01-01", "sunset": "2025-01-01"}
        assert ls.current_stage(lifecycle, "core", self.stages, TODAY) == "core"

    def test_dates_give_the_most_advanced_stage_reached(self):
        lifecycle = {"core": "2020-01-01", "sunset": "2025-01-01", "discontinued": "2027-01-01"}
        assert ls.current_stage(lifecycle, None, self.stages, TODAY) == "sunset"

    def test_a_date_of_today_has_been_reached(self):
        assert ls.dated_stage({"core": "2026-06-01"}, self.stages, TODAY) == "core"

    def test_a_future_date_is_a_plan_not_the_current_stage(self):
        lifecycle = {"evaluating": "2020-01-01", "core": "2026-06-02"}
        assert ls.dated_stage(lifecycle, self.stages, TODAY) == "evaluating"

    def test_all_dates_ahead_falls_back_to_the_earliest_stage(self):
        lifecycle = {"sunset": "2030-01-01", "core": "2028-01-01"}
        assert ls.dated_stage(lifecycle, self.stages, TODAY) == "core"

    def test_reads_iso_timestamps(self):
        assert ls.dated_stage({"core": "2020-01-01T10:00:00"}, self.stages, TODAY) == "core"

    def test_ignores_unparseable_dates_but_still_sees_the_stage(self):
        assert ls.dated_stage({"core": "soon"}, self.stages, TODAY) == "core"

    def test_ignores_keys_outside_the_vocabulary(self):
        assert ls.dated_stage({"active": "2020-01-01"}, self.stages, TODAY) is None

    def test_default_model_matches_the_original_precedence(self):
        lifecycle = {"plan": "2019-01-01", "active": "2020-01-01", "endOfLife": "2030-01-01"}
        assert ls.current_stage(lifecycle, None, self.default, TODAY) == "active"

    def test_defaults_to_today(self):
        assert ls.dated_stage({"core": "2000-01-01", "sunset": "2999-01-01"}, self.stages) == "core"


class TestReportLifecycle:
    """Reports read the built-in phase names; a custom vocabulary is projected
    onto them by semantic."""

    def test_built_in_model_without_a_stage_passes_through_untouched(self):
        lifecycle = {"plan": "2019-01-01", "phaseIn": "2019-06-01", "active": "2020-01-01"}
        assert ls.report_lifecycle(lifecycle, None, {}) is lifecycle
        assert ls.report_lifecycle(None, None, None) is None
        assert ls.report_lifecycle(lifecycle, "", {}) is lifecycle

    def test_projects_each_semantic_onto_its_slot(self):
        lifecycle = {
            "evaluating": "2018-01-01",
            "core": "2020-01-01",
            "sunset": "2026-01-01",
            "discontinued": "2027-01-01",
        }
        assert ls.report_lifecycle(lifecycle, None, RADAR) == {
            "plan": "2018-01-01",
            "active": "2020-01-01",
            "phaseOut": "2026-01-01",
            "endOfLife": "2027-01-01",
        }

    def test_earliest_date_wins_when_stages_share_a_semantic(self):
        lifecycle = {"heritage": "2024-01-01", "emerging": "2019-01-01", "core": "2021-01-01"}
        assert ls.report_lifecycle(lifecycle, None, RADAR) == {"active": "2019-01-01"}
        lifecycle = {"emerging": "2022-01-01", "heritage": "2020-01-01"}
        assert ls.report_lifecycle(lifecycle, None, RADAR) == {"active": "2020-01-01"}

    def test_drops_empty_values_and_keys_outside_the_vocabulary(self):
        lifecycle = {"core": "", "sunset": None, "active": "2020-01-01"}
        assert ls.report_lifecycle(lifecycle, None, RADAR) == {}
        assert ls.report_lifecycle(None, None, RADAR) == {}

    def test_stated_retired_without_a_date_is_flagged_not_dated(self):
        assert ls.report_lifecycle({}, "discontinued", RADAR) == {"_semantic": "retired"}
        assert ls.report_lifecycle({"core": "2020-01-01"}, "discontinued", RADAR) == {
            "active": "2020-01-01",
            "_semantic": "retired",
        }

    def test_a_retirement_date_needs_no_flag(self):
        lifecycle = {"discontinued": "2027-01-01"}
        assert ls.report_lifecycle(lifecycle, "discontinued", RADAR) == {"endOfLife": "2027-01-01"}

    def test_stated_pre_operational_without_a_start_date_is_flagged(self):
        assert ls.report_lifecycle({}, "evaluating", RADAR) == {"_semantic": "pre_operational"}

    @pytest.mark.parametrize("dated", ["evaluating", "core"])
    def test_a_start_date_needs_no_flag(self, dated):
        out = ls.report_lifecycle({dated: "2020-01-01"}, "evaluating", RADAR)
        assert "_semantic" not in out

    @pytest.mark.parametrize("stage", ["emerging", "core", "heritage", "sunset", "legacy"])
    def test_other_stated_stages_add_nothing(self, stage):
        assert ls.report_lifecycle({}, stage, RADAR) == {}

    def test_built_in_model_with_a_stated_stage_copies_and_flags(self):
        lifecycle = {"active": "2020-01-01"}
        out = ls.report_lifecycle(lifecycle, "endOfLife", {})
        assert out == {"active": "2020-01-01", "_semantic": "retired"}
        assert lifecycle == {"active": "2020-01-01"}
        assert ls.report_lifecycle({}, "plan", None) == {"_semantic": "pre_operational"}
        assert ls.report_lifecycle({"phaseIn": "2020-01-01"}, "plan", None) == {
            "phaseIn": "2020-01-01"
        }


class TestReportPhase:
    def test_built_in_model_reports_its_own_phase(self):
        assert ls.report_phase({"phaseIn": "2020-01-01"}, None, {}, TODAY) == "phaseIn"
        assert ls.report_phase({}, "phaseOut", {}, TODAY) == "phaseOut"

    @pytest.mark.parametrize(
        ("stage", "slot"),
        [
            ("evaluating", "plan"),
            ("emerging", "active"),
            ("core", "active"),
            ("heritage", "active"),
            ("sunset", "phaseOut"),
            ("discontinued", "endOfLife"),
        ],
    )
    def test_custom_stage_counts_under_its_semantic(self, stage, slot):
        assert ls.report_phase({}, stage, RADAR, TODAY) == slot

    def test_dated_custom_stage(self):
        assert ls.report_phase({"sunset": "2020-01-01"}, None, RADAR, TODAY) == "phaseOut"

    def test_unknown_stays_unknown(self):
        assert ls.report_phase({}, None, RADAR, TODAY) is None
        assert ls.report_phase({}, None, {}, TODAY) is None
        # A stored stage the vocabulary no longer defines has no semantic.
        assert ls.report_phase({}, "legacy", RADAR, TODAY) is None


class TestSmallHelpers:
    def test_has_lifecycle_data(self):
        assert ls.has_lifecycle_data({}, "core") is True
        assert ls.has_lifecycle_data({"core": "2020-01-01"}, None) is True
        assert ls.has_lifecycle_data({"core": ""}, None) is False
        assert ls.has_lifecycle_data(None, None) is False
        assert ls.has_lifecycle_data({}, "") is False

    def test_retired_keys(self):
        assert ls.retired_keys({"Application": RADAR}, "Application") == ["discontinued"]
        assert ls.retired_keys({"Application": RADAR}, "ITComponent") == ["endOfLife"]

    def test_view_projects_by_the_cards_type(self):
        from types import SimpleNamespace

        view = ls.LifecycleView({"Application": RADAR})
        app = SimpleNamespace(
            id=1,
            type="Application",
            attributes={"a": 1},
            lifecycle={"core": "2020-01-01"},
            lifecycle_stage="discontinued",
        )
        other = SimpleNamespace(id=2, type="Interface", attributes={}, lifecycle={"active": "x"})
        assert view(app) == {"active": "2020-01-01", "_semantic": "retired"}
        assert view(other) is other.lifecycle
        assert view.phase(app) == "endOfLife"
        proxy = view.proxy(app)
        assert (proxy.id, proxy.type, proxy.attributes) == (1, "Application", {"a": 1})
        assert proxy.lifecycle == {"active": "2020-01-01", "_semantic": "retired"}
