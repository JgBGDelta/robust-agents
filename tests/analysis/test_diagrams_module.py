"""Tests de `DiagramsModule`: registro de figuras + test e2e completo.

Estructura:
- Tests sobre `AnalysisFrames` minimos hechos a mano: `generate_figure`,
  `generate_minimal`/`generate_additional`/`generate_all`, degradacion ante
  columnas/datos insuficientes (`FigureDataError` capturado en `failed`).
- Test e2e: consolida un arbol `runs/` sintetico rico (6 agentes propios +
  1 externo, con traza `robust_agent` completa) con `ConsolidationModule`,
  escribe los CSV y genera el catalogo completo con `DiagramsModule`,
  verificando que las figuras se generan como PNG validos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from analysis.consolidation_module import ConsolidationModule
from analysis.diagrams_module import (
    FIGURE_REGISTRY,
    AnalysisFrames,
    DiagramsModule,
    FigureDataError,
)
from analysis.external_metrics_module import ExternalMetricsRecord


# ---------------------------------------------------------------------------
# Tests sobre AnalysisFrames minimos (registro, degradacion)
# ---------------------------------------------------------------------------


def _empty_frames() -> AnalysisFrames:
    """Auxiliar interno: empty frames."""
    return AnalysisFrames(runs=pd.DataFrame(), steps=None)


def _minimal_frames() -> AnalysisFrames:
    """Auxiliar interno: minimal frames."""
    runs = pd.DataFrame(
        {
            "configuration_id": ["default__m", "default__m"],
            "agent_id": ["default", "default"],
            "source_type": ["own", "own"],
            "sample_group": ["main", "main"],
            "resolved": pd.array([True, False], dtype="boolean"),
            "cost_usd": [0.1, 0.2],
            "repository": ["repo/repo", "repo/repo"],
            "failure_category": ["none", "none"],
            "churn_total": [2, 4],
            "files_modified_count": [1, 2],
            "hunks_count": [1, 1],
            "dispersion_score": [0.0, 0.0],
        }
    )
    return AnalysisFrames(runs=runs, steps=None)


def test_generate_figure_unknown_name_raises_keyerror():
    """Comprueba que generate figure unknown name raises keyerror."""
    with pytest.raises(KeyError):
        DiagramsModule().generate_figure("no_existe", _minimal_frames(), Path("/tmp"))


def test_generate_figure_creates_png(tmp_path: Path):
    """Comprueba que generate figure creates png."""
    output_path = DiagramsModule().generate_figure("resolution_rate", _minimal_frames(), tmp_path)
    assert output_path.exists()
    assert output_path.suffix == ".png"


def test_generate_minimal_only_generates_minimal_group(tmp_path: Path):
    """Comprueba que generate minimal only generates minimal group."""
    report = DiagramsModule().generate_minimal(_minimal_frames(), tmp_path)
    minimal_names = {name for name, (_, group) in FIGURE_REGISTRY.items() if group == "minimal"}
    generated_names = {p.stem for p in report.generated}
    assert generated_names <= minimal_names
    assert "uncertainty_risk_action_map" not in generated_names


def test_generate_additional_only_generates_additional_group(tmp_path: Path):
    """Comprueba que generate additional only generates additional group."""
    report = DiagramsModule().generate_additional(_minimal_frames(), tmp_path)
    additional_names = {name for name, (_, group) in FIGURE_REGISTRY.items() if group == "additional"}
    generated_names = {p.stem for p in report.generated}
    failed_names = set(report.failed.keys())
    skipped_names = set(report.skipped.keys())
    assert generated_names <= additional_names
    assert (generated_names | skipped_names | failed_names) == additional_names


def test_generate_all_covers_full_registry(tmp_path: Path):
    """Comprueba que generate all covers full registry."""
    report = DiagramsModule().generate_all(_minimal_frames(), tmp_path)
    all_names = set(FIGURE_REGISTRY.keys())
    generated_names = {p.stem for p in report.generated}
    failed_names = set(report.failed.keys())
    skipped_names = set(report.skipped.keys())
    assert generated_names.isdisjoint(failed_names)
    assert generated_names.isdisjoint(skipped_names)
    assert (generated_names | skipped_names | failed_names) == all_names


def test_generate_all_never_raises_on_empty_frames(tmp_path: Path):
    """Ninguna figura individual debe abortar el catalogo completo (S7)."""
    report = DiagramsModule().generate_all(_empty_frames(), tmp_path)
    assert report.generated == []
    assert set(report.skipped.keys()) == set(FIGURE_REGISTRY.keys())
    assert report.failed == {}
    for reason in report.skipped.values():
        assert reason  # motivo no vacio


def test_generate_figure_singular_propagates_exception_on_empty_frames(tmp_path: Path):
    """`generate_figure` (singular) no captura `FigureDataError`: se propaga."""
    with pytest.raises(FigureDataError):
        DiagramsModule().generate_figure("resolution_rate", _empty_frames(), tmp_path)


# ---------------------------------------------------------------------------
# Test e2e: ConsolidationModule -> CSV -> DiagramsModule.generate_all
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    """Auxiliar interno: write json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _run_record(instance_id: str, agent_id: str, run_id: str, **overrides) -> dict:
    """Auxiliar interno: run record."""
    base = {
        "run_id": run_id,
        "instance_id": instance_id,
        "status": "completed",
        "exit_status": "Submitted",
        "cost_usd": 0.05,
        "duration_seconds": 10.0,
        "started_at": 100.0,
        "ended_at": 110.0,
        "error": None,
        "model_patch_path": f"instances/{instance_id}/{agent_id}/{run_id}/model.patch",
        "trajectory_path": f"instances/{instance_id}/{agent_id}/{run_id}/trajectory.traj.json",
        "agent_run_config": {
            "agent_id": agent_id,
            "model_id": "gemini/gemini-2.5-flash",
            "dataset_slice_size": None,
        },
        "preds_entry": {
            "model_patch": (
                "diff --git a/src/foo.py b/src/foo.py\n"
                "--- a/src/foo.py\n"
                "+++ b/src/foo.py\n"
                "@@ -1,1 +1,1 @@\n"
                "-old\n"
                "+new\n"
            )
        },
    }
    base.update(overrides)
    return base


def _evaluation_result(resolved: bool | None) -> dict:
    """Auxiliar interno: evaluation result."""
    return {
        "functional": {"status": "evaluated" if resolved is not None else "skipped", "resolved": resolved},
        "extended": {
            "run_metrics": {"steps_used": 4, "termination": "model_submitted"},
            "patch_metrics": {
                "files_modified": ["src/foo.py"],
                "lines_added": 1,
                "lines_deleted": 1,
                "hunks_count": 1,
                "files_intersection_with_gold": ["src/foo.py"],
                "files_unexpected": [],
                "files_missing_vs_gold": [],
                "jaccard_files": 1.0,
                "jaccard_lines": 1.0,
            },
        },
    }


def _robust_agent_trajectory() -> dict:
    """Auxiliar interno: robust agent trajectory."""
    return {
        "messages": [
            {
                "role": "assistant",
                "extra": {"response": {"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}},
            }
        ],
        "info": {"model_stats": {"api_calls": 4}},
        "robust_agent": {
            "termination": "model_submitted",
            "episode_state_final": {
                "signals_history": [
                    {"step_index": 1, "uncertainty": {"score": 0.1, "level": "low", "components": {}, "evidence": {}}},
                    {"step_index": 1, "structural_risk": {"score": 0.0, "level": "low", "metrics": {}, "diff_summary": {"files_touched": []}}},
                ],
                "decisions_history": [
                    {"step_index": 1, "decision": {"action": "PROCEED", "policy_snapshot": {}}},
                ],
            },
            "steps": [
                {
                    "step_id": 1,
                    "uncertainty": {"score": 0.1, "level": "low"},
                    "structural_risk": {"score": 0.0, "level": "low"},
                    "decision": {"action": "PROCEED"},
                    "validation_outcome": None,
                }
            ],
        },
    }


def _make_own_run(
    runs_root: Path, instance_id: str, agent_id: str, run_id: str, *,
    resolved: bool | None, with_trajectory: bool = False,
) -> None:
    """Auxiliar interno: make own run."""
    run_dir = runs_root / "instances" / instance_id / agent_id / run_id
    _write_json(run_dir / "run_record.json", _run_record(instance_id, agent_id, run_id))
    _write_json(run_dir / "evaluation_result.json", _evaluation_result(resolved))
    if with_trajectory:
        _write_json(run_dir / "trajectory.traj.json", _robust_agent_trajectory())


@pytest.fixture
def rich_analysis_frames(tmp_path: Path) -> AnalysisFrames:
    """Arbol runs/ sintetico con 6 agentes propios + 1 externo, consolidado."""
    runs_root = tmp_path / "runs" / "exp"

    _make_own_run(runs_root, "django__django-1", "default", "r-11111111", resolved=True)
    _make_own_run(runs_root, "django__django-2", "default", "r-22222222", resolved=False)
    _make_own_run(runs_root, "django__django-1", "robust_strict", "r-33333333", resolved=True)
    _make_own_run(
        runs_root, "django__django-1", "robust_balanced", "r-44444444",
        resolved=True, with_trajectory=True,
    )
    _make_own_run(runs_root, "astropy__astropy-3", "robust_permissive", "r-55555555", resolved=False)
    _make_own_run(runs_root, "astropy__astropy-3", "robust_no_intervention", "r-66666666", resolved=True)
    _make_own_run(runs_root, "astropy__astropy-3", "robust_v2", "r-77777777", resolved=False)

    external_records = [
        ExternalMetricsRecord(
            instance_id="django__django-1",
            source_id="agentless_v1.5",
            agent_id="agentless_v1.5",
            model_id="claude-3-5-sonnet-20241022",
            model_patch="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
            matched=True,
            patch_metrics={
                "files_modified": ["x.py"], "lines_added": 1, "lines_deleted": 1, "hunks_count": 1,
                "files_intersection_with_gold": ["x.py"], "files_unexpected": [], "files_missing_vs_gold": [],
                "jaccard_files": 1.0, "jaccard_lines": 1.0,
            },
            resolved=None,
            resolved_source=None,
        ),
    ]
    external_path = tmp_path / "external" / "agentless_v1.5" / "extracted_metrics.jsonl"
    external_path.parent.mkdir(parents=True, exist_ok=True)
    external_path.write_text(
        "\n".join(json.dumps(r.to_dict()) for r in external_records) + "\n", encoding="utf-8"
    )

    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(runs_root, external_metrics_paths=[external_path])
    runs_csv, steps_csv = module.write_csv(run_rows, step_rows, tmp_path / "analysis")

    return DiagramsModule().load_data(runs_csv, steps_csv)


def test_e2e_generate_all_produces_most_figures(rich_analysis_frames: AnalysisFrames, tmp_path: Path):
    """Comprueba que e2e generate all produces most figures."""
    report = DiagramsModule().generate_all(rich_analysis_frames, tmp_path / "figures")

    all_names = set(FIGURE_REGISTRY.keys())
    generated_names = {p.stem for p in report.generated}
    skipped_names = set(report.skipped.keys())
    failed_names = set(report.failed.keys())

    assert generated_names | skipped_names | failed_names == all_names
    assert not failed_names, f"Figuras fallidas inesperadamente: {report.failed}"
    assert not skipped_names, f"Figuras omitidas inesperadamente: {report.skipped}"
    assert len(generated_names) == len(all_names)

    for path in report.generated:
        assert path.exists()
        assert path.stat().st_size > 0


def test_e2e_generate_minimal_and_additional_partition_registry(
    rich_analysis_frames: AnalysisFrames, tmp_path: Path
):
    """Comprueba que e2e generate minimal and additional partition registry."""
    module = DiagramsModule()
    minimal_report = module.generate_minimal(rich_analysis_frames, tmp_path / "min")
    additional_report = module.generate_additional(rich_analysis_frames, tmp_path / "add")

    minimal_names = {name for name, (_, g) in FIGURE_REGISTRY.items() if g == "minimal"}
    additional_names = {name for name, (_, g) in FIGURE_REGISTRY.items() if g == "additional"}

    assert {p.stem for p in minimal_report.generated} == minimal_names
    assert {p.stem for p in additional_report.generated} == additional_names


def test_e2e_specific_figures_reflect_expected_data(rich_analysis_frames: AnalysisFrames, tmp_path: Path):
    """Comprobaciones de contenido de datos (no solo que el PNG exista)."""
    runs = rich_analysis_frames.runs
    assert set(runs["source_type"].unique()) == {"own", "external"}
    assert "robust_balanced" in runs["agent_id"].unique()
    assert runs["configuration_id"].nunique() >= 6

    steps = rich_analysis_frames.steps
    assert steps is not None
    assert len(steps) == 1
    assert steps.iloc[0]["controller_action"] == "PROCEED"
