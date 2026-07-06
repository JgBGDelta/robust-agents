"""Tests de `AnalysisFrames`: carga y tipado explicito de columnas."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from analysis.diagrams_module.analysis_frames import AnalysisFrames


def _write_runs_csv(tmp_path: Path) -> Path:
    """Auxiliar interno: write runs csv."""
    path = tmp_path / "datos_consolidados.csv"
    path.write_text(
        "run_id,instance_id,configuration_id,source_type,sample_group,run_status,"
        "failure_category,agent_id,resolved,patch_available,submitted,empty_submission,evaluable,cost_usd\n"
        "r-1,repo__repo-1,default__m,own,main,completed,none,default,True,True,True,False,True,0.1\n"
        "r-2,repo__repo-2,default__m,own,main,completed,none,default,False,True,True,False,True,0.2\n"
        "r-3,repo__repo-3,default__m,own,main,failed,exception,default,,False,False,,,\n",
        encoding="utf-8",
    )
    return path


def _write_steps_csv(tmp_path: Path) -> Path:
    """Auxiliar interno: write steps csv."""
    path = tmp_path / "datos_pasos_consolidados.csv"
    path.write_text(
        "run_id,instance_id,configuration_id,step_index,uncertainty_score,uncertainty_level,"
        "structural_risk_score,structural_risk_level,controller_action\n"
        "r-1,repo__repo-1,robust_balanced__m,1,0.1,low,0.0,low,PROCEED\n",
        encoding="utf-8",
    )
    return path


def test_load_runs_only(tmp_path: Path):
    """Comprueba que load runs only."""
    csv_path = _write_runs_csv(tmp_path)
    frames = AnalysisFrames.load(csv_path)
    assert isinstance(frames.runs, pd.DataFrame)
    assert frames.steps is None
    assert len(frames.runs) == 3


def test_load_types_boolean_columns_as_nullable_boolean(tmp_path: Path):
    """Comprueba que load types boolean columns as nullable boolean."""
    csv_path = _write_runs_csv(tmp_path)
    frames = AnalysisFrames.load(csv_path)

    assert str(frames.runs["resolved"].dtype) == "boolean"
    assert frames.runs["resolved"].iloc[0] == True  # noqa: E712
    assert frames.runs["resolved"].iloc[1] == False  # noqa: E712
    assert pd.isna(frames.runs["resolved"].iloc[2])


def test_load_types_categorical_columns(tmp_path: Path):
    """Comprueba que load types categorical columns."""
    csv_path = _write_runs_csv(tmp_path)
    frames = AnalysisFrames.load(csv_path)

    assert str(frames.runs["source_type"].dtype) == "category"
    assert str(frames.runs["run_status"].dtype) == "category"


def test_load_with_steps_csv(tmp_path: Path):
    """Comprueba que load with steps csv."""
    runs_path = _write_runs_csv(tmp_path)
    steps_path = _write_steps_csv(tmp_path)
    frames = AnalysisFrames.load(runs_path, steps_path)

    assert frames.steps is not None
    assert len(frames.steps) == 1
    assert str(frames.steps["controller_action"].dtype) == "category"


def test_load_missing_csv_raises(tmp_path: Path):
    """Comprueba que load missing csv raises."""
    with pytest.raises(FileNotFoundError):
        AnalysisFrames.load(tmp_path / "does-not-exist.csv")
