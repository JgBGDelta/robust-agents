"""Tests de `RowBuilder`: mapeo de campos y funciones puras de recalculo.

- `dispersion_score`/`test_touch_ratio`: funciones puras, comparadas con casos
  conocidos de `StructuralMetricsComputer` (`src/agent/structural_metrics_module/computer.py`).
- `build_own`: mapeo desde `run_record.json`/`evaluation_result.json`/`trajectory.traj.json`
  ya parseados, con degradacion limpia cuando faltan ficheros.
- `build_external`: mapeo desde `ExternalMetricsRecord`.
"""

from __future__ import annotations

from pathlib import Path

from analysis.consolidation_module.row_builder import (
    RowBuilder,
    dispersion_score,
)
from analysis.consolidation_module.row_builder import test_touch_ratio as compute_test_touch_ratio
from analysis.consolidation_module.runs_scanner import ScannedRun
from analysis.external_metrics_module import ExternalMetricsRecord

DEFAULT_TEST_GLOBS = ["tests/**", "**/test_*.py", "**/*_test.py", "**/*test*.py"]


# ---------------------------------------------------------------------------
# dispersion_score
# ---------------------------------------------------------------------------


def test_dispersion_score_empty_list_is_zero():
    """Comprueba que dispersion score empty list is zero."""
    assert dispersion_score([]) == 0.0


def test_dispersion_score_single_directory_is_zero():
    """Comprueba que dispersion score single directory is zero."""
    assert dispersion_score(["a/x.py", "a/y.py", "a/z.py"]) == 0.0


def test_dispersion_score_two_equal_directories_is_one():
    """Comprueba que dispersion score two equal directories is one."""
    assert dispersion_score(["a/x.py", "b/y.py"]) == 1.0


def test_dispersion_score_uneven_distribution_between_zero_and_one():
    """Comprueba que dispersion score uneven distribution between zero and one."""
    score = dispersion_score(["a/x.py", "a/y.py", "a/z.py", "b/w.py"])
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# test_touch_ratio
# ---------------------------------------------------------------------------


def test_test_touch_ratio_empty_patch_is_zero():
    """Comprueba que test touch ratio empty patch is zero."""
    assert compute_test_touch_ratio("", DEFAULT_TEST_GLOBS) == 0.0
    assert compute_test_touch_ratio(None, DEFAULT_TEST_GLOBS) == 0.0


def test_test_touch_ratio_all_lines_in_test_file():
    """Comprueba que test touch ratio all lines in test file."""
    patch = (
        "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
        "--- a/tests/test_foo.py\n"
        "+++ b/tests/test_foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    assert compute_test_touch_ratio(patch, DEFAULT_TEST_GLOBS) == 1.0


def test_test_touch_ratio_mixed_source_and_test_files():
    """Comprueba que test touch ratio mixed source and test files."""
    patch = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
        "--- a/tests/test_foo.py\n"
        "+++ b/tests/test_foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    # 2 lineas totales (1 en src, 1 en test) -> ratio 0.5.
    assert compute_test_touch_ratio(patch, DEFAULT_TEST_GLOBS) == 0.5


def test_test_touch_ratio_unified_diff_format():
    """Comprueba que test touch ratio unified diff format."""
    patch = (
        "--- a/tests/test_foo.py\n"
        "+++ b/tests/test_foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    assert compute_test_touch_ratio(patch, DEFAULT_TEST_GLOBS) == 1.0


# ---------------------------------------------------------------------------
# build_own
# ---------------------------------------------------------------------------


def _scanned(tmp_path: Path, instance_id="repo__repo-1", agent_id="default", run_name="r-aaaaaaaa") -> ScannedRun:
    """Auxiliar interno: scanned."""
    run_dir = tmp_path / "instances" / instance_id / agent_id / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return ScannedRun(
        instance_id=instance_id, agent_id=agent_id, run_dir=run_dir, run_sequence=0, retry_count=0
    )


_MODEL_PATCH = (
    "diff --git a/src/foo.py b/src/foo.py\n"
    "--- a/src/foo.py\n"
    "+++ b/src/foo.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new\n"
)


def _run_record(**overrides) -> dict:
    """Auxiliar interno: run record."""
    base = {
        "run_id": "r-aaaaaaaa",
        "instance_id": "repo__repo-1",
        "status": "completed",
        "exit_status": "Submitted",
        "cost_usd": 0.05,
        "duration_seconds": 12.5,
        "started_at": 1000.0,
        "ended_at": 1012.5,
        "error": None,
        "model_patch_path": "instances/repo__repo-1/default/r-aaaaaaaa/model.patch",
        "trajectory_path": "instances/repo__repo-1/default/r-aaaaaaaa/trajectory.traj.json",
        "agent_run_config": {
            "agent_id": "default",
            "model_id": "gemini/gemini-2.5-flash",
            "dataset_slice_size": None,
        },
        "preds_entry": {"model_patch": _MODEL_PATCH},
    }
    base.update(overrides)
    return base


def _evaluation_result(**overrides) -> dict:
    """Auxiliar interno: evaluation result."""
    base = {
        "functional": {"status": "evaluated", "resolved": True},
        "extended": {
            "run_metrics": {"steps_used": 5, "termination": "model_submitted"},
            "patch_metrics": {
                "files_modified": ["src/foo.py"],
                "lines_added": 1,
                "lines_deleted": 1,
                "hunks_count": 1,
                "files_intersection_with_gold": ["src/foo.py"],
                "files_unexpected": [],
                "files_missing_vs_gold": [],
                "jaccard_files": 1.0,
                "jaccard_lines": 0.5,
            },
        },
    }
    base.update(overrides)
    return base


def test_build_own_maps_identification_fields(tmp_path: Path):
    """Comprueba que build own maps identification fields."""
    scanned = _scanned(tmp_path)
    row, steps = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=_evaluation_result(),
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data=None,
    )

    assert row.run_id == "r-aaaaaaaa"
    assert row.instance_id == "repo__repo-1"
    assert row.repository == "repo/repo"
    assert row.agent_id == "default"
    assert row.configuration_id == "default__gemini-2.5-flash"
    assert row.source_type == "own"
    assert row.model_id == "gemini/gemini-2.5-flash"
    assert row.sample_group == "main"
    assert steps == []


def test_build_own_sample_group_ablation_when_slice_size_set(tmp_path: Path):
    """Comprueba que build own sample group ablation when slice size set."""
    scanned = _scanned(tmp_path, agent_id="robust_no_intervention")
    run_record = _run_record()
    run_record["agent_run_config"]["dataset_slice_size"] = 40
    row, _ = RowBuilder().build_own(
        scanned, run_record=run_record, evaluation_result=None, evaluation_path=None, trajectory_data=None
    )
    assert row.sample_group == "ablation"


def test_build_own_status_and_patch_fields(tmp_path: Path):
    """Comprueba que build own status and patch fields."""
    scanned = _scanned(tmp_path)
    row, _ = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=_evaluation_result(),
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data=None,
    )
    assert row.run_status == "completed"
    assert row.exit_status == "Submitted"
    assert row.patch_available is True
    assert row.submitted is True
    assert row.empty_submission is False
    assert row.evaluable is True
    assert row.resolved is True
    assert row.failure_category == "none"


def test_build_own_degrades_cleanly_without_evaluation_result(tmp_path: Path):
    """PA6: sin evaluation_result.json, campos derivados a null, fila incluida igual."""
    scanned = _scanned(tmp_path)
    row, _ = RowBuilder().build_own(
        scanned, run_record=_run_record(), evaluation_result=None, evaluation_path=None, trajectory_data=None
    )

    assert row.evaluable is None
    assert row.resolved is None
    assert row.files_modified_count is None
    assert row.jaccard_files is None
    # Pero el estado/coste de run_record.json si esta disponible.
    assert row.run_status == "completed"
    assert row.cost_usd == 0.05
    # dispersion_score/test_touch_ratio se calculan del model_patch directamente,
    # no dependen de evaluation_result.json.
    assert row.dispersion_score == 0.0  # un unico directorio "src"
    assert row.test_touch_ratio == 0.0  # ningun fichero de test tocado


def test_build_own_patch_metrics_from_evaluation_result(tmp_path: Path):
    """Comprueba que build own patch metrics from evaluation result."""
    scanned = _scanned(tmp_path)
    row, _ = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=_evaluation_result(),
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data=None,
    )
    assert row.files_modified_count == 1
    assert row.lines_added == 1
    assert row.lines_deleted == 1
    assert row.churn_total == 2
    assert row.hunks_count == 1
    assert row.matching_files_count == 1
    assert row.unexpected_files_count == 0
    assert row.missing_files_count == 0
    assert row.jaccard_files == 1.0
    assert row.jaccard_lines == 0.5


def test_build_own_failure_category_variants(tmp_path: Path):
    """Comprueba que build own failure category variants."""
    scanned = _scanned(tmp_path)
    builder = RowBuilder()

    row_limits, _ = builder.build_own(
        scanned,
        run_record=_run_record(status="failed", exit_status="LimitsExceeded"),
        evaluation_result=None,
        evaluation_path=None,
        trajectory_data=None,
    )
    assert row_limits.failure_category == "limits_exceeded"

    row_empty, _ = builder.build_own(
        scanned,
        run_record=_run_record(status="failed", exit_status="EmptySubmission"),
        evaluation_result=None,
        evaluation_path=None,
        trajectory_data=None,
    )
    assert row_empty.failure_category == "empty_submission"

    row_precond, _ = builder.build_own(
        scanned,
        run_record=_run_record(status="precondition_failed", exit_status=None),
        evaluation_result=None,
        evaluation_path=None,
        trajectory_data=None,
    )
    assert row_precond.failure_category == "precondition_failed"

    row_exc, _ = builder.build_own(
        scanned,
        run_record=_run_record(status="failed", exit_status=None, error={"exception_type": "TimeoutExpired"}),
        evaluation_result=None,
        evaluation_path=None,
        trajectory_data=None,
    )
    assert row_exc.failure_category == "exception"


def test_build_own_steps_used_falls_back_to_trajectory_api_calls(tmp_path: Path):
    """Comprueba que build own steps used falls back to trajectory api calls."""
    scanned = _scanned(tmp_path)
    evaluation_result = _evaluation_result()
    evaluation_result["extended"]["run_metrics"]["steps_used"] = None
    row, _ = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=evaluation_result,
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data={"info": {"model_stats": {"api_calls": 9}}},
    )
    assert row.steps_used == 9


def test_build_own_with_robust_agent_trace_populates_uncertainty(tmp_path: Path):
    """Comprueba que build own with robust agent trace populates uncertainty."""
    scanned = _scanned(tmp_path, agent_id="robust_balanced")
    trajectory_data = {
        "robust_agent": {
            "termination": "model_submitted",
            "episode_state_final": {
                "signals_history": [
                    {"step_index": 1, "uncertainty": {"score": 0.2, "level": "low", "components": {}, "evidence": {}}},
                ],
                "decisions_history": [],
            },
            "steps": [],
        }
    }
    row, steps = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=_evaluation_result(),
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data=trajectory_data,
    )
    assert row.uncertainty_mean == 0.2
    assert row.termination == "model_submitted"
    assert row.controller_intervention_ratio == 0.0  # 0 intervenciones / 5 steps_used
    assert steps == []


def test_build_own_returns_step_rows_when_robust_agent_has_steps(tmp_path: Path):
    """Comprueba que build own returns step rows when robust agent has steps."""
    scanned = _scanned(tmp_path, agent_id="robust_balanced")
    trajectory_data = {
        "robust_agent": {
            "termination": "model_submitted",
            "episode_state_final": {"signals_history": [], "decisions_history": []},
            "steps": [
                {
                    "step_id": 1,
                    "uncertainty": {"score": 0.1, "level": "low"},
                    "structural_risk": {"score": 0.0, "level": "low"},
                    "decision": {"action": "PROCEED"},
                }
            ],
        }
    }
    row, steps = RowBuilder().build_own(
        scanned,
        run_record=_run_record(),
        evaluation_result=_evaluation_result(),
        evaluation_path=scanned.run_dir / "evaluation_result.json",
        trajectory_data=trajectory_data,
    )
    assert len(steps) == 1
    assert steps[0].run_id == row.run_id
    assert steps[0].configuration_id == row.configuration_id


def test_build_own_configuration_id_without_model_id(tmp_path: Path):
    """Comprueba que build own configuration id without model id."""
    scanned = _scanned(tmp_path)
    run_record = _run_record()
    run_record["agent_run_config"]["model_id"] = None
    row, _ = RowBuilder().build_own(
        scanned, run_record=run_record, evaluation_result=None, evaluation_path=None, trajectory_data=None
    )
    assert row.configuration_id == "default"


# ---------------------------------------------------------------------------
# build_external
# ---------------------------------------------------------------------------


def _external_record(**overrides) -> ExternalMetricsRecord:
    """Auxiliar interno: external record."""
    base = dict(
        instance_id="repo__repo-1",
        source_id="agentless_v1.5",
        agent_id="agentless_v1.5",
        model_id="claude-3-5-sonnet-20241022",
        model_patch=_MODEL_PATCH,
        matched=True,
        patch_metrics={
            "files_modified": ["src/foo.py"],
            "lines_added": 1,
            "lines_deleted": 1,
            "hunks_count": 1,
            "files_intersection_with_gold": ["src/foo.py"],
            "files_unexpected": [],
            "files_missing_vs_gold": [],
            "jaccard_files": 1.0,
            "jaccard_lines": 0.5,
        },
        resolved=None,
        resolved_source=None,
    )
    base.update(overrides)
    return ExternalMetricsRecord(**base)


def test_build_external_maps_identification_and_source_type():
    """Comprueba que build external maps identification and source type."""
    record = _external_record()
    row = RowBuilder().build_external(record, Path("data/external_predictions/agentless_v1.5/extracted_metrics.jsonl"))

    assert row.run_id == "ext-agentless_v1.5-repo__repo-1"
    assert row.instance_id == "repo__repo-1"
    assert row.repository == "repo/repo"
    assert row.agent_id == "agentless_v1.5"
    assert row.source_type == "external"
    assert row.sample_group == "external"
    assert row.configuration_id == "agentless_v1.5"
    assert row.external_model_id == "claude-3-5-sonnet-20241022"


def test_build_external_own_only_families_are_null():
    """Comprueba que build external own only families are null."""
    record = _external_record()
    row = RowBuilder().build_external(record, Path("x.jsonl"))

    assert row.steps_used is None
    assert row.cost_usd is None
    assert row.uncertainty_mean is None
    assert row.structural_risk_mean is None
    assert row.controller_intervention_count is None
    assert row.retry_count is None


def test_build_external_patch_family_reuses_patch_metrics():
    """Comprueba que build external patch family reuses patch metrics."""
    record = _external_record()
    row = RowBuilder().build_external(record, Path("x.jsonl"))

    assert row.files_modified_count == 1
    assert row.lines_added == 1
    assert row.churn_total == 2
    assert row.jaccard_files == 1.0
    assert row.dispersion_score == 0.0
    assert row.test_touch_ratio == 0.0


def test_build_external_unmatched_has_no_patch():
    """Comprueba que build external unmatched has no patch."""
    record = _external_record(
        matched=False,
        model_patch=None,
        model_id=None,
        patch_metrics={
            "files_modified": [],
            "lines_added": 0,
            "lines_deleted": 0,
            "hunks_count": 0,
            "files_intersection_with_gold": None,
            "files_unexpected": None,
            "files_missing_vs_gold": None,
            "jaccard_files": None,
            "jaccard_lines": None,
        },
    )
    row = RowBuilder().build_external(record, Path("x.jsonl"))

    assert row.patch_available is False
    assert row.submitted is False
    assert row.run_status is None
    assert row.matching_files_count is None
    assert row.jaccard_files is None


def test_build_external_resolved_from_record():
    """Comprueba que build external resolved from record."""
    record = _external_record(resolved=True, resolved_source="published")
    row = RowBuilder().build_external(record, Path("x.jsonl"))
    assert row.resolved is True
    assert row.evaluable is True
