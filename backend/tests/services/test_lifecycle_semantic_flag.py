"""A stated stage with no date still decides whether a card is in the landscape."""

from __future__ import annotations

import pytest

from app.services.lifecycle import is_live_in_fiscal_year


@pytest.mark.parametrize("semantic", ["retired", "pre_operational"])
def test_stated_out_of_the_landscape_is_never_live(semantic):
    assert is_live_in_fiscal_year({"_semantic": semantic}, 2026, 1) is False
    assert is_live_in_fiscal_year({"active": "2020-01-01", "_semantic": semantic}, 2026, 1) is False


@pytest.mark.parametrize("lifecycle", [{}, None, {"_semantic": "operational"}, {"_semantic": ""}])
def test_everything_else_keeps_the_dated_rules(lifecycle):
    assert is_live_in_fiscal_year(lifecycle, 2026, 1) is True
