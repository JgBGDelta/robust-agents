"""Tests de la tasa de resolución centralizada."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.diagrams_module.resolution_metrics import (
    count_evaluable,
    count_resolved,
    resolution_rate_delta_pp,
    resolution_rate_fraction,
    resolution_rate_pct,
    resolution_rate_stats,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_ten_of_one_twenty_yields_eight_point_three_three_percent():
    rows = [{"resolved": True}] * 10 + [{"resolved": False}] * 80 + [{"resolved": pd.NA}] * 30
    df = _df(rows)
    assert count_resolved(df) == 10
    assert len(df) == 120
    assert resolution_rate_fraction(df) == pytest.approx(10 / 120)
    assert resolution_rate_pct(df) == pytest.approx(100 * 10 / 120, rel=1e-6)


def test_null_resolved_stays_in_denominator():
    df = _df([{"resolved": True}, {"resolved": pd.NA}, {"resolved": pd.NA}])
    assert resolution_rate_fraction(df) == pytest.approx(1 / 3)
    assert count_resolved(df) == 1


def test_limits_exceeded_row_stays_in_denominator():
    df = _df(
        [
            {"resolved": True, "failure_category": "none"},
            {"resolved": pd.NA, "failure_category": "limits_exceeded"},
            {"resolved": pd.NA, "failure_category": "limits_exceeded"},
        ]
    )
    assert resolution_rate_fraction(df) == pytest.approx(1 / 3)


def test_one_of_forty_yields_two_point_five_percent():
    rows = [{"resolved": True}] + [{"resolved": False}] * 20 + [{"resolved": pd.NA}] * 19
    df = _df(rows)
    assert len(df) == 40
    assert resolution_rate_pct(df) == pytest.approx(2.5)


def test_repository_group_uses_all_instances():
    df = _df(
        [
            {"repository": "org/a", "resolved": True},
            {"repository": "org/a", "resolved": pd.NA},
            {"repository": "org/a", "resolved": False},
            {"repository": "org/b", "resolved": True},
            {"repository": "org/b", "resolved": True},
        ]
    )
    repo_a = df[df["repository"] == "org/a"]
    repo_b = df[df["repository"] == "org/b"]
    assert resolution_rate_fraction(repo_a) == pytest.approx(1 / 3)
    assert resolution_rate_fraction(repo_b) == pytest.approx(1.0)


def test_evaluable_count_is_secondary_metric():
    df = _df(
        [
            {"resolved": True, "evaluable": True},
            {"resolved": False, "evaluable": True},
            {"resolved": pd.NA, "evaluable": False},
        ]
    )
    stats = resolution_rate_stats(df)
    assert stats.resolved_count == 1
    assert stats.total_instances == 3
    assert stats.evaluable_count == 2
    assert stats.rate_pct == pytest.approx(100 / 3, rel=1e-6)


def test_delta_pp_baseline_vs_robust():
    baseline = _df([{"resolved": True}] * 8 + [{"resolved": pd.NA}] * 112)
    robust = _df([{"resolved": True}] * 10 + [{"resolved": pd.NA}] * 110)
    assert resolution_rate_delta_pp(baseline, robust) == pytest.approx(1.666666, rel=1e-4)


def test_empty_subset_returns_none():
    df = _df([])
    assert resolution_rate_fraction(df) is None
    assert resolution_rate_pct(df) is None
    assert resolution_rate_stats(df).total_instances == 0
