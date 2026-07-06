"""Tests del esqueleto del Bloque 2.

Cubren:
1. La superficie publica del paquete `benchmark` esta en su sitio.
2. Los contratos serializan a JSON de forma estable (roundtrip).
3. El `BenchmarkRunner` instancia los cuatro modulos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import (
    AgentRunConfig,
    BenchmarkConfig,
    BenchmarkDatasetModule,
    BenchmarkEvaluationModule,
    BenchmarkExecutionModule,
    BenchmarkExport,
    BenchmarkInstance,
    BenchmarkResultsModule,
    BenchmarkRunRecord,
    BenchmarkRunner,
    BenchmarkTaskResult,
    DatasetConfig,
    EvaluationResult,
    ExperimentConfig,
    ExperimentLayout,
    PlannedRun,
    RunSlot,
)


# --- Fabricas auxiliares de contratos sinteticos ----------------------------------


def _instance_fixture() -> BenchmarkInstance:
    """Auxiliar interno: instance fixture."""
    return BenchmarkInstance(
        instance_id="astropy__astropy-12907",
        dataset_name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        repo="astropy/astropy",
        base_commit="abc123",
        problem_statement="Fix issue with linear model.",
        gold_patch="diff --git a/foo.py b/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
        test_patch=None,
        fail_to_pass=["tests::test_linear"],
        pass_to_pass=["tests::test_other"],
        metadata={"difficulty": "medium"},
    )


def _agent_run_config_fixture() -> AgentRunConfig:
    """Auxiliar interno: agent run config fixture."""
    return AgentRunConfig(
        agent_id="robust_balanced",
        agent_class="agent.robust_agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
        cost_limit=2.0,
        step_limit=50,
        seed=42,
        config_overrides={"controller.tests_validation_enabled": True},
    )


def _run_record_fixture() -> BenchmarkRunRecord:
    """Auxiliar interno: run record fixture."""
    return BenchmarkRunRecord(
        run_id="run-001",
        instance_id="astropy__astropy-12907",
        agent_run_config=_agent_run_config_fixture(),
        status="completed",
        started_at=1_000_000.0,
        ended_at=1_000_125.5,
        duration_seconds=125.5,
        exit_status="model_submitted",
        trajectory_path="instances/astropy__astropy-12907/robust_balanced/run-001/trajectory.traj.json",
        model_patch_path="instances/astropy__astropy-12907/robust_balanced/run-001/model.patch",
        preds_entry={"model_name_or_path": "robust_balanced__gemini-gemini-3-flash", "instance_id": "astropy__astropy-12907"},
        environment={"image": "swebench/sweb.eval.x86_64.astropy_1776__astropy_12907"},
        contamination_detected=False,
        contamination_evidence=None,
    )


def _evaluation_fixture() -> EvaluationResult:
    """Auxiliar interno: evaluation fixture."""
    return EvaluationResult(
        run_id="run-001",
        instance_id="astropy__astropy-12907",
        agent_id="robust_balanced",
        trace_format="robust-agent-1.0",
        functional={
            "resolved": True,
            "status": "evaluated",
            "evaluation_backend": "sb_cli",
            "report_path": "groups/robust_balanced__gemini-gemini-3-flash/functional_report/report.json",
            "evidence": {"sb_cli_run_id": "abc-def"},
        },
        extended={
            "run_metrics": {
                "trajectory_format": "mini-swe-agent-1.1",
                "exit_status": "Submitted",
                "termination": "model_submitted",
                "steps_used": 15,
                "cost_total": 0.51,
                "wallclock_seconds": 125.5,
            },
            "patch_metrics": {
                "lines_added": 5,
                "lines_deleted": 2,
                "hunks_count": 1,
                "files_modified": ["astropy/core/handlers/base.py"],
                "files_intersection_with_gold": ["astropy/core/handlers/base.py"],
                "files_unexpected": [],
                "files_missing_vs_gold": [],
                "jaccard_files": 1.0,
                "jaccard_lines": 0.41,
            },
            "availability": {
                "run_metrics": {"status": "available", "cause": None},
                "patch_metrics": {"status": "available", "cause": None},
            },
            "errors": [],
        },
    )


def _experiment_config_fixture() -> ExperimentConfig:
    """Auxiliar interno: experiment config fixture."""
    return ExperimentConfig(
        dataset=DatasetConfig(slice_size=10),
        agents=[
            _agent_run_config_fixture(),
            AgentRunConfig(
                agent_id="default",
                agent_class="minisweagent.agents.default.DefaultAgent",
                model_id="gemini/gemini-3-flash",
            ),
        ],
        benchmark=BenchmarkConfig(workers=2),
    )


# --- 1. Superficie publica del paquete --------------------------------------------


def test_public_surface_is_complete():
    """Comprueba que public surface is complete."""
    expected = {
        "AgentRunConfig",
        "BenchmarkConfig",
        "BenchmarkDatasetModule",
        "BenchmarkEvaluationModule",
        "BenchmarkExecutionModule",
        "BenchmarkExport",
        "BenchmarkInstance",
        "BenchmarkResultsModule",
        "BenchmarkRunRecord",
        "BenchmarkRunner",
        "BenchmarkTaskResult",
        "DatasetConfig",
        "EvaluationResult",
        "ExperimentConfig",
        "ExperimentLayout",
        "PlannedRun",
        "RunSlot",
    }
    import benchmark
    assert expected.issubset(set(benchmark.__all__))
    for name in expected:
        assert hasattr(benchmark, name), f"benchmark.{name} no esta exportado"


def test_evaluation_result_is_only_evaluation_export():
    """Solo `EvaluationResult` figura como contrato de evaluacion en la API publica."""
    import benchmark
    assert "EvaluationResult" in benchmark.__all__
    assert not hasattr(benchmark, "FunctionalEvaluationResult")
    assert not hasattr(benchmark, "ExtendedEvaluationResult")


# --- 2. Roundtrip JSON de los contratos -------------------------------------------


@pytest.mark.parametrize(
    "factory_name",
    [
        "_instance_fixture",
        "_run_record_fixture",
        "_evaluation_fixture",
    ],
)
def test_contract_roundtrips_json(factory_name: str):
    """Comprueba que contract roundtrips json."""
    factory = globals()[factory_name]
    contract = factory()
    payload = contract.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded == payload


def test_evaluation_result_to_dict_has_unified_schema():
    """`to_dict` expone functional, extended y metadatos del run."""
    result = _evaluation_fixture()
    d = result.to_dict()
    assert "functional" in d
    assert "extended" in d
    assert "run_id" in d
    assert "instance_id" in d
    assert "agent_id" in d
    for field in ("uncertainty_summary", "structural_risk_summary", "decision_summary",
                  "validation_summary", "stability_summary", "patch_summary", "timing", "cost",
                  "profile"):
        assert field not in d, f"campo inesperado: {field}"


def test_evaluation_result_from_dict_roundtrip():
    """Comprueba que evaluation result from dict roundtrip."""
    result = _evaluation_fixture()
    d = result.to_dict()
    restored = EvaluationResult.from_dict(d)
    assert restored.run_id == result.run_id
    assert restored.functional["resolved"] == result.functional["resolved"]
    assert restored.extended["run_metrics"]["steps_used"] == 15


def test_benchmark_task_result_roundtrip_uses_unified_evaluation():
    """Comprueba que benchmark task result roundtrip uses unified evaluation."""
    task_result = BenchmarkTaskResult(
        run_id="run-001",
        instance=_instance_fixture(),
        agent_run_config=_agent_run_config_fixture(),
        run_record=_run_record_fixture(),
        evaluation=_evaluation_fixture(),
    )
    payload = task_result.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)

    assert decoded["run_id"] == "run-001"
    assert decoded["instance"]["instance_id"] == "astropy__astropy-12907"
    assert decoded["evaluation"]["trace_format"] == "robust-agent-1.0"
    assert "functional" in decoded["evaluation"]
    assert "extended" in decoded["evaluation"]


def test_experiment_layout_roundtrip_serializes_paths_as_posix():
    """Comprueba que experiment layout roundtrip serializes paths as posix."""
    layout = ExperimentLayout(
        experiment_id="20260524-143015__a1b2c3d4",
        root=Path("runs/20260524-143015__a1b2c3d4"),
        manifest_path=Path("runs/20260524-143015__a1b2c3d4/manifest.json"),
        config_path=Path("runs/20260524-143015__a1b2c3d4/config.yaml"),
        dataset_summary_path=Path("runs/20260524-143015__a1b2c3d4/dataset_summary.json"),
        instances_dir=Path("runs/20260524-143015__a1b2c3d4/instances"),
        groups_dir=Path("runs/20260524-143015__a1b2c3d4/groups"),
    )
    payload = layout.to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    assert decoded["experiment_id"] == "20260524-143015__a1b2c3d4"
    assert decoded["root"] == "runs/20260524-143015__a1b2c3d4"
    assert decoded["manifest_path"] == "runs/20260524-143015__a1b2c3d4/manifest.json"
    assert "results_index_path" not in decoded


def test_benchmark_export_roundtrip_serializes_paths_as_posix():
    """Comprueba que benchmark export roundtrip serializes paths as posix."""
    export = BenchmarkExport(
        experiment_id="20260524-143015__a1b2c3d4",
        experiment_path=Path("runs/20260524-143015__a1b2c3d4"),
        manifest_path=Path("runs/20260524-143015__a1b2c3d4/manifest.json"),
        dataset_summary_path=Path("runs/20260524-143015__a1b2c3d4/dataset_summary.json"),
        config_path=Path("runs/20260524-143015__a1b2c3d4/config.yaml"),
        runs_count=20,
        status="finalized",
    )
    payload = export.to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    assert decoded["status"] == "finalized"
    assert decoded["runs_count"] == 20
    assert decoded["experiment_path"] == "runs/20260524-143015__a1b2c3d4"
    assert "results_index_path" not in decoded
    assert "groups" not in decoded


# --- 3. Wiring del BenchmarkRunner -----------------------------------------------


def test_runner_wires_four_modules():
    """Comprueba que runner wires four modules."""
    runner = BenchmarkRunner(_experiment_config_fixture())
    assert isinstance(runner.results_module, BenchmarkResultsModule)
    assert isinstance(runner.dataset_module, BenchmarkDatasetModule)
    assert isinstance(runner.execution_module, BenchmarkExecutionModule)
    assert isinstance(runner.evaluation_module, BenchmarkEvaluationModule)


def test_results_module_constructor_stores_parameters():
    """Comprueba que results module constructor stores parameters."""
    config = _experiment_config_fixture()
    module = BenchmarkResultsModule(
        experiment_config=config,
        runs_root="runs",
        experiment_id_explicit="explicit-id",
        force_rerun=True,
    )
    assert module._runs_root == Path("runs")
    assert module._experiment_id_explicit == "explicit-id"
    assert module._force_rerun is True
    assert module._experiment_config is config


# --- 4. AgentRunConfig sin campo profile ------------------------------------------


def test_agent_run_config_has_no_profile_field():
    """Comprueba que agent run config has no profile field."""
    agent = AgentRunConfig(
        agent_id="robust_balanced",
        agent_class="agent.robust_agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
    )
    assert not hasattr(agent, "profile")


def test_dataset_config_has_no_filter_field():
    """Comprueba que dataset config has no filter field."""
    ds = DatasetConfig()
    assert not hasattr(ds, "filter")
