"""Tests unitarios del `BenchmarkResultsModule`.

Cubren: `experiment_id`, escritura atomica, manifiesto minimo (`initialized`),
transiciones de estado, reanudacion, `force_rerun`, persistencia de artefactos
por run. Sin `results.jsonl`. Un solo `evaluation_result.json` por run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from benchmark.config import AgentRunConfig, BenchmarkConfig, DatasetConfig, ExperimentConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.results_module import BenchmarkResultsModule, BenchmarkTaskResult
from benchmark.results_module.artifact_store import ArtifactStore
from benchmark.results_module.contracts import PlannedRun


# --- Fabricas auxiliares ---------------------------------------------------------


def _agent_config(*, agent_id: str = "robust_balanced") -> AgentRunConfig:
    """Auxiliar interno: agent config."""
    return AgentRunConfig(
        agent_id=agent_id,
        agent_class="agent.robust_agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
        seed=42,
    )


def _experiment_config(**overrides: object) -> ExperimentConfig:
    """Auxiliar interno: experiment config."""
    base = ExperimentConfig(
        dataset=DatasetConfig(slice_size=2),
        agents=[_agent_config()],
        benchmark=BenchmarkConfig(runs_root="runs"),
    )
    return base.model_copy(update=overrides)


def _dataset_summary(*instance_ids: str) -> dict[str, object]:
    """Auxiliar interno: dataset summary."""
    return {
        "num_instances_loaded": len(instance_ids),
        "instance_ids": list(instance_ids),
    }


def _planned_run(
    *,
    instance_id: str = "inst-a",
    run_id: str = "run-001",
    agent_config: AgentRunConfig | None = None,
) -> PlannedRun:
    """Auxiliar interno: planned run."""
    return PlannedRun(
        instance_id=instance_id,
        agent_run_config=agent_config or _agent_config(),
        run_id=run_id,
    )


def _run_record(
    *,
    instance_id: str = "inst-a",
    run_id: str = "run-001",
    status: str = "completed",
    exit_status: str | None = None,
    agent_config: AgentRunConfig | None = None,
) -> BenchmarkRunRecord:
    """Auxiliar interno: run record."""
    agent_config = agent_config or _agent_config()
    rel = f"instances/{instance_id}/{agent_config.agent_id}/{run_id}"
    return BenchmarkRunRecord(
        run_id=run_id,
        instance_id=instance_id,
        agent_run_config=agent_config,
        status=status,  # type: ignore[arg-type]
        started_at=1.0,
        ended_at=2.0,
        duration_seconds=1.0,
        exit_status=exit_status,
        trajectory_path=f"{rel}/trajectory.traj.json",
        model_patch_path=f"{rel}/model.patch",
    )


def _evaluation_result(
    *,
    instance_id: str = "inst-a",
    run_id: str = "run-001",
    agent_id: str = "robust_balanced",
) -> EvaluationResult:
    """Auxiliar interno: evaluation result."""
    return EvaluationResult(
        run_id=run_id,
        instance_id=instance_id,
        agent_id=agent_id,
        trace_format=None,
        functional={
            "resolved": True,
            "status": "evaluated",
            "evaluation_backend": "sb_cli",
            "report_path": None,
            "evidence": {},
        },
        extended={
            "run_metrics": {"wallclock_seconds": 1.0},
            "patch_metrics": {"lines_added": 0},
            "availability": {
                "run_metrics": {"status": "available", "cause": None},
                "patch_metrics": {"status": "not_applicable", "cause": "empty_model_patch"},
            },
            "errors": [],
        },
    )


def _task_result(
    *,
    instance_id: str = "inst-a",
    run_id: str = "run-001",
    status: str = "completed",
) -> BenchmarkTaskResult:
    """Auxiliar interno: task result."""
    agent_config = _agent_config()
    return BenchmarkTaskResult(
        run_id=run_id,
        instance=BenchmarkInstance(
            instance_id=instance_id,
            dataset_name="SWE-bench/SWE-bench_Lite",
            subset="lite",
            split="test",
            repo="org/repo",
            base_commit="abc",
            problem_statement="Fix bug.",
        ),
        agent_run_config=agent_config,
        run_record=_run_record(instance_id=instance_id, run_id=run_id, status=status),
        evaluation=_evaluation_result(instance_id=instance_id, run_id=run_id),
    )


def _build_initialized_module(
    tmp_path: Path,
    *,
    experiment_id: str,
    instance_ids: tuple[str, ...] = ("inst-a", "inst-b"),
) -> BenchmarkResultsModule:
    """Auxiliar interno: build initialized module."""
    runs_root = tmp_path / "runs"
    module = BenchmarkResultsModule(
        experiment_config=_experiment_config(
            benchmark=BenchmarkConfig(runs_root=str(runs_root))
        ),
        dataset_summary=_dataset_summary(*instance_ids),
        experiment_id_explicit=experiment_id,
    )
    module.initialize(matrix_size=1)
    return module


# --- experiment_id ---------------------------------------------------------------


def test_config_hash_is_stable_for_same_config():
    """Comprueba que config hash is stable for same config."""
    config_a = _experiment_config()
    config_b = _experiment_config()
    assert BenchmarkResultsModule._config_hash(config_a) == BenchmarkResultsModule._config_hash(config_b)


def test_config_hash_changes_when_config_changes():
    """Comprueba que config hash changes when config changes."""
    base = _experiment_config()
    changed = _experiment_config(benchmark=BenchmarkConfig(runs_root="runs", workers=8))
    assert BenchmarkResultsModule._config_hash(base) != BenchmarkResultsModule._config_hash(changed)


def test_config_hash_excludes_force_rerun_flag():
    """Comprueba que config hash excludes force rerun flag."""
    base = _experiment_config()
    flagged = _experiment_config(force_rerun=True)
    assert BenchmarkResultsModule._config_hash(base) == BenchmarkResultsModule._config_hash(flagged)


def test_config_hash_excludes_explicit_id_field():
    """Comprueba que config hash excludes explicit id field."""
    base = _experiment_config()
    aliased = _experiment_config(experiment_id_explicit="alias-xyz")
    assert BenchmarkResultsModule._config_hash(base) == BenchmarkResultsModule._config_hash(aliased)


def test_hybrid_experiment_id_uses_timestamp_and_hash():
    """Comprueba que hybrid experiment id uses timestamp and hash."""
    config = _experiment_config()
    moment = datetime(2026, 5, 24, 14, 30, 15, tzinfo=timezone.utc)
    experiment_id = BenchmarkResultsModule._build_hybrid_id(config, timestamp=moment)
    assert experiment_id.startswith("20260524-143015__")
    assert len(experiment_id.split("__")[1]) == 8


def test_explicit_experiment_id_overrides_hybrid():
    """Comprueba que explicit experiment id overrides hybrid."""
    module = BenchmarkResultsModule(
        experiment_config=_experiment_config(),
        experiment_id_explicit="my-explicit-id",
    )
    assert module.experiment_id == "my-explicit-id"


# --- initialize ------------------------------------------------------------------


def test_initialize_creates_layout_on_empty_directory(tmp_path: Path):
    """Comprueba que initialize creates layout on empty directory."""
    runs_root = tmp_path / "runs"
    module = BenchmarkResultsModule(
        experiment_config=_experiment_config(benchmark=BenchmarkConfig(runs_root=str(runs_root))),
        dataset_summary=_dataset_summary("inst-a", "inst-b"),
        experiment_id_explicit="exp-new",
    )

    layout = module.initialize(matrix_size=2)

    assert layout.root.exists()
    assert layout.manifest_path.exists()
    assert layout.config_path.exists()
    assert layout.dataset_summary_path.exists()
    assert layout.instances_dir.is_dir()
    assert layout.groups_dir.is_dir()
    assert not hasattr(layout, "results_index_path")

    manifest = ArtifactStore.read_json(layout.manifest_path)
    assert manifest["status"] == "initialized"
    assert manifest["experiment_id"] == "exp-new"
    assert manifest["matrix_size"] == 2
    assert "benchmark_format_version" in manifest
    assert "created_at" in manifest
    assert "runs" not in manifest
    assert "finalized_at" not in manifest


def test_initialize_writes_atomic_config_yaml_and_dataset_summary(tmp_path: Path):
    """Comprueba que initialize writes atomic config yaml and dataset summary."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-atomic")
    root = module.export_path()
    assert (root / "config.yaml").is_file()
    assert (root / "dataset_summary.json").is_file()
    assert list(root.glob("*.tmp")) == []
    assert not (root / "results.jsonl").exists()


def test_initialize_resume_on_compatible_directory(tmp_path: Path):
    """Comprueba que initialize resume on compatible directory."""
    runs_root = tmp_path / "runs"
    config = _experiment_config(benchmark=BenchmarkConfig(runs_root=str(runs_root)))
    summary = _dataset_summary("inst-a")

    first = BenchmarkResultsModule(
        experiment_config=config, dataset_summary=summary, experiment_id_explicit="exp-resume"
    )
    first.initialize(matrix_size=1)

    second = BenchmarkResultsModule(
        experiment_config=config, dataset_summary=summary, experiment_id_explicit="exp-resume"
    )
    layout = second.initialize(matrix_size=1)

    manifest = ArtifactStore.read_json(layout.manifest_path)
    assert manifest["status"] == "running"


def test_initialize_aborts_on_incompatible_config(tmp_path: Path):
    """Comprueba que initialize aborts on incompatible config."""
    runs_root = tmp_path / "runs"
    summary = _dataset_summary("inst-a")
    first = BenchmarkResultsModule(
        experiment_config=_experiment_config(benchmark=BenchmarkConfig(runs_root=str(runs_root))),
        dataset_summary=summary,
        experiment_id_explicit="exp-mismatch",
    )
    first.initialize(matrix_size=1)

    # Cambiamos functional_backend (campo experimental, no operativo) para forzar mismatch.
    # Nota: workers, inter_run_delay_seconds, disk_min_free_gb y evaluation_skip son
    # operativos/de fase y se ignoran en la validacion (ver test siguiente para
    # evaluation_skip en concreto).
    second = BenchmarkResultsModule(
        experiment_config=_experiment_config(
            benchmark=BenchmarkConfig(runs_root=str(runs_root), functional_backend="none")
        ),
        dataset_summary=summary,
        experiment_id_explicit="exp-mismatch",
    )
    with pytest.raises(ValueError, match="config.yaml"):
        second.initialize(matrix_size=1)


def test_initialize_allows_evaluation_skip_toggle_on_resume(tmp_path: Path):
    """Comprueba que cambiar solo evaluation_skip no aborta la reanudacion.

    Este es el mecanismo que usa ``--evaluate``/``--evaluation-skip`` para
    alternar entre fase 1 (ejecucion) y fase 2 (evaluacion funcional) sobre
    el mismo experimento sin disparar el guardrail de config incompatible.
    """
    runs_root = tmp_path / "runs"
    summary = _dataset_summary("inst-a")
    first = BenchmarkResultsModule(
        experiment_config=_experiment_config(
            benchmark=BenchmarkConfig(runs_root=str(runs_root), evaluation_skip=True)
        ),
        dataset_summary=summary,
        experiment_id_explicit="exp-eval-toggle",
    )
    first.initialize(matrix_size=1)

    second = BenchmarkResultsModule(
        experiment_config=_experiment_config(
            benchmark=BenchmarkConfig(runs_root=str(runs_root), evaluation_skip=False)
        ),
        dataset_summary=summary,
        experiment_id_explicit="exp-eval-toggle",
    )
    layout = second.initialize(matrix_size=1)
    manifest = ArtifactStore.read_json(layout.manifest_path)
    assert manifest["status"] == "running"


def test_initialize_aborts_on_incompatible_dataset(tmp_path: Path):
    """Comprueba que initialize aborts on incompatible dataset."""
    runs_root = tmp_path / "runs"
    config = _experiment_config(benchmark=BenchmarkConfig(runs_root=str(runs_root)))
    first = BenchmarkResultsModule(
        experiment_config=config,
        dataset_summary=_dataset_summary("inst-a"),
        experiment_id_explicit="exp-dataset",
    )
    first.initialize(matrix_size=1)

    second = BenchmarkResultsModule(
        experiment_config=config,
        dataset_summary=_dataset_summary("inst-b"),
        experiment_id_explicit="exp-dataset",
    )
    with pytest.raises(ValueError, match="instance_id"):
        second.initialize(matrix_size=1)


def test_force_rerun_moves_previous_directory_to_backup(tmp_path: Path):
    """Comprueba que force rerun moves previous directory to backup."""
    runs_root = tmp_path / "runs"
    config = _experiment_config(benchmark=BenchmarkConfig(runs_root=str(runs_root)))
    summary = _dataset_summary("inst-a")

    first = BenchmarkResultsModule(
        experiment_config=config, dataset_summary=summary, experiment_id_explicit="exp-force"
    )
    first.initialize(matrix_size=1)
    (first.export_path() / "marker.txt").write_text("old", encoding="utf-8")

    second = BenchmarkResultsModule(
        experiment_config=config,
        dataset_summary=summary,
        experiment_id_explicit="exp-force",
        force_rerun=True,
    )
    second.initialize(matrix_size=1)

    assert (second.export_path() / "marker.txt").exists() is False
    backups = list(runs_root.glob("exp-force.bak-*"))
    assert len(backups) == 1
    assert (backups[0] / "marker.txt").read_text(encoding="utf-8") == "old"


# --- pending_runs y mark_running -------------------------------------------------


def test_pending_runs_returns_all_slots_on_empty_tree(tmp_path: Path):
    """Comprueba que pending runs returns all slots on empty tree."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-pending")
    run_list = [_planned_run(run_id="run-001"), _planned_run(run_id="run-002", instance_id="inst-b")]
    pending = module.pending_runs(run_list)
    assert len(pending) == 2
    assert pending[0].run_dir.name == "run-001"


def test_pending_runs_skips_terminal_run_records(tmp_path: Path):
    """Comprueba que pending runs skips terminal run records."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-skip")
    record = _run_record(status="completed")
    module.persist_run_record(record)
    pending = module.pending_runs([_planned_run()])
    assert pending == []


def test_pending_runs_reexecutes_intermediate_run_records(tmp_path: Path):
    """Comprueba que pending runs reexecutes intermediate run records."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-intermediate")
    record = _run_record(status="running")  # type: ignore[arg-type]
    module.persist_run_record(record)
    pending = module.pending_runs([_planned_run()])
    assert len(pending) == 1
    assert pending[0].run_id == "run-001"


def test_pending_runs_skips_limits_exceeded(tmp_path: Path):
    """Comprueba que LimitsExceeded no se reintenta al reanudar."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-limits")
    record = _run_record(status="failed", exit_status="LimitsExceeded")
    module.persist_run_record(record)
    pending = module.pending_runs([_planned_run()])
    assert pending == []


def test_pending_runs_skips_empty_submission(tmp_path: Path):
    """Comprueba que EmptySubmission no se reintenta al reanudar."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-empty")
    record = _run_record(status="failed", exit_status="EmptySubmission")
    module.persist_run_record(record)
    pending = module.pending_runs([_planned_run()])
    assert pending == []


def test_pending_runs_does_not_mutate_manifest(tmp_path: Path):
    """Comprueba que pending runs does not mutate manifest."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-pure")
    before = ArtifactStore.read_json(module.export_path() / "manifest.json")
    module.pending_runs([_planned_run()])
    after = ArtifactStore.read_json(module.export_path() / "manifest.json")
    assert before == after
    assert after["status"] == "initialized"


def test_mark_running_transitions_manifest(tmp_path: Path):
    """Comprueba que mark running transitions manifest."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-mark-running")
    module.mark_running()
    manifest = ArtifactStore.read_json(module.export_path() / "manifest.json")
    assert manifest["status"] == "running"
    assert manifest["updated_at"] is not None


# --- persist_run_record + persist_evaluation_result ------------------------------


def test_persist_run_record_writes_json_roundtrip(tmp_path: Path):
    """Comprueba que persist run record writes json roundtrip."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-persist")
    record = _run_record()
    module.persist_run_record(record)
    run_dir = module.export_path() / "instances/inst-a/robust_balanced/run-001"
    payload = ArtifactStore.read_json(run_dir / "run_record.json")
    assert payload["status"] == "completed"


def test_persist_run_record_path_has_no_profile_segment(tmp_path: Path):
    """Sin segmento de perfil en la ruta: instances/<id>/<agent_id>/<run_id>/."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-no-profile")
    record = _run_record()
    module.persist_run_record(record)
    expected = module.export_path() / "instances/inst-a/robust_balanced/run-001/run_record.json"
    assert expected.is_file()


def test_persist_evaluation_result_writes_unified_json(tmp_path: Path):
    """Escribe un unico `evaluation_result.json` por run."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-eval")
    module.persist_run_record(_run_record())
    module.persist_evaluation_result(_evaluation_result())
    run_dir = module.export_path() / "instances/inst-a/robust_balanced/run-001"
    assert (run_dir / "evaluation_result.json").is_file()
    assert not (run_dir / "functional_result.json").exists()
    assert not (run_dir / "extended_result.json").exists()
    payload = ArtifactStore.read_json(run_dir / "evaluation_result.json")
    assert "functional" in payload
    assert "extended" in payload
    assert payload["functional"]["resolved"] is True


def test_persist_evaluation_result_requires_run_record_first(tmp_path: Path):
    """Comprueba que persist evaluation result requires run record first."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-eval-error")
    with pytest.raises(RuntimeError, match="persist_run_record"):
        module.persist_evaluation_result(_evaluation_result())


def test_evaluation_result_path_returns_none_before_cache(tmp_path: Path):
    """Comprueba que evaluation result path returns none before cache."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-ep")
    assert module.evaluation_result_path("inst-a", "run-001") is None


def test_evaluation_result_path_returns_path_after_persist_run_record(tmp_path: Path):
    """Comprueba que evaluation result path returns path after persist run record."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-ep2")
    module.persist_run_record(_run_record())
    path = module.evaluation_result_path("inst-a", "run-001")
    assert path is not None
    assert path.name == "evaluation_result.json"


# --- layout.group_id sin profile -------------------------------------------------


def test_group_id_sanitizes_slashes_in_model_id():
    """Comprueba que group id sanitizes slashes in model id."""
    from benchmark.results_module.layout import group_id
    gid = group_id(agent_id="robust_balanced", model_id="gemini/gemini-3-flash")
    assert gid == "robust_balanced__gemini-gemini-3-flash"
    assert "/" not in gid


def test_group_id_has_two_segments_not_three():
    """Sin segmento de perfil: <agent_id>__<model_id>."""
    from benchmark.results_module.layout import group_id
    gid = group_id(agent_id="default", model_id="anthropic/claude-3")
    parts = gid.split("__")
    assert len(parts) == 2


# --- persist_functional_group_report --------------------------------------------


def test_persist_functional_group_report_copies_directory(tmp_path: Path):
    """Comprueba que persist functional group report copies directory."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-group-dir")
    source = tmp_path / "raw_report"
    source.mkdir()
    (source / "report.json").write_text('{"resolved": []}', encoding="utf-8")
    (source / "logs.txt").write_text("ok", encoding="utf-8")
    module.persist_functional_group_report("robust_balanced__model-x", source)
    destination = module.export_path() / "groups/robust_balanced__model-x/functional_report"
    assert (destination / "report.json").is_file()
    assert (destination / "logs.txt").is_file()


def test_persist_functional_group_report_copies_single_file(tmp_path: Path):
    """Comprueba que persist functional group report copies single file."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-group-file")
    source = tmp_path / "report.json"
    source.write_text('{"resolved": []}', encoding="utf-8")
    module.persist_functional_group_report("robust_balanced__model-x", source)
    destination = (
        module.export_path() / "groups/robust_balanced__model-x/functional_report/report.json"
    )
    assert destination.is_file()


# --- finalize / mark_aborted -------------------------------------------


def test_finalize_writes_final_manifest_without_results_jsonl(tmp_path: Path):
    """Comprueba que finalize writes final manifest without results jsonl."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-finalize")
    tasks = [_task_result(), _task_result(instance_id="inst-b", run_id="run-002")]

    export = module.finalize(tasks)

    assert export.status == "finalized"
    assert export.runs_count == 2
    manifest = ArtifactStore.read_json(export.manifest_path)
    assert manifest["status"] == "finalized"
    assert manifest["runs_completed"] == 2
    assert not (export.experiment_path / "results.jsonl").exists()
    assert not hasattr(export, "results_index_path")


def test_config_yaml_matches_experiment_config(tmp_path: Path):
    """Comprueba que config yaml matches experiment config."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-config")
    payload = yaml.safe_load((module.export_path() / "config.yaml").read_text(encoding="utf-8"))
    expected = _experiment_config(benchmark=BenchmarkConfig(runs_root=str(tmp_path / "runs"))).model_dump(
        mode="json"
    )
    assert json.loads(json.dumps(payload, sort_keys=True)) == json.loads(
        json.dumps(expected, sort_keys=True)
    )


def test_mark_aborted_updates_manifest(tmp_path: Path):
    """Comprueba que mark aborted updates manifest."""
    module = _build_initialized_module(tmp_path, experiment_id="exp-abort")
    module.mark_aborted("fallo simulado")
    manifest = ArtifactStore.read_json(module.export_path() / "manifest.json")
    assert manifest["status"] == "aborted"
    assert manifest["abort_reason"] == "fallo simulado"


def test_methods_require_initialize_first():
    """Comprueba que methods require initialize first."""
    module = BenchmarkResultsModule(
        experiment_config=_experiment_config(),
        experiment_id_explicit="not-initialized",
    )
    with pytest.raises(RuntimeError, match="initialize"):
        module.persist_run_record(_run_record())
    with pytest.raises(RuntimeError, match="initialize"):
        module.finalize([])
    with pytest.raises(RuntimeError, match="initialize"):
        module.export_path()

