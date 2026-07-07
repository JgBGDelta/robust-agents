"""Tests del modulo de evaluacion unificado.

Prueba `EvaluationResult`, extractores y `Evaluator`.
Sin red, sin Docker, sin sb-cli real.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchmark.config import AgentRunConfig, BenchmarkConfig, DatasetConfig, ExperimentConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.evaluation_module.evaluator import Evaluator, _group_by_agent_model, _sb_cli_subset_alias
from benchmark.evaluation_module.sb_cli_client import SbCliClient
from benchmark.evaluation_module.extractors.patch_metrics import PatchMetricsExtractor
from benchmark.evaluation_module.extractors.run_metrics import RunMetricsExtractor
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from dataclasses import replace


# ---------------------------------------------------------------------------
# Fabricas auxiliares
# ---------------------------------------------------------------------------


def _agent_config(*, agent_id: str = "robust_balanced") -> AgentRunConfig:
    """Auxiliar interno: agent config."""
    return AgentRunConfig(
        agent_id=agent_id,
        agent_class="agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
        seed=42,
    )


def _run_record(
    *,
    run_id: str = "r-abc123",
    instance_id: str = "django__django-001",
    agent_id: str = "robust_balanced",
    status: str = "completed",
    exit_status: str | None = "Submitted",
    trajectory_path: str | None = None,
    model_patch_path: str | None = None,
    duration: float = 60.0,
) -> BenchmarkRunRecord:
    """Auxiliar interno: run record."""
    return BenchmarkRunRecord(
        run_id=run_id,
        instance_id=instance_id,
        agent_run_config=_agent_config(agent_id=agent_id),
        status=status,  # type: ignore[arg-type]
        started_at=0.0,
        ended_at=duration,
        duration_seconds=duration,
        exit_status=exit_status,
        trajectory_path=trajectory_path,
        model_patch_path=model_patch_path,
        preds_entry={"instance_id": instance_id, "model_name_or_path": agent_id, "model_patch": ""},
    )


def _instance(instance_id: str = "django__django-001") -> BenchmarkInstance:
    """Auxiliar interno: instance."""
    return BenchmarkInstance(
        instance_id=instance_id,
        dataset_name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        repo="django/django",
        base_commit="deadbeef",
        problem_statement="Fix bug.",
        gold_patch="diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        fail_to_pass=["tests::test_a"],
        pass_to_pass=["tests::test_b"],
    )


def _experiment_config() -> ExperimentConfig:
    """Auxiliar interno: experiment config."""
    return ExperimentConfig(
        dataset=DatasetConfig(),
        agents=[_agent_config()],
        benchmark=BenchmarkConfig(functional_backend="none"),
    )


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------


def test_evaluation_result_to_dict_has_unified_schema():
    """Comprueba que evaluation result to dict has unified schema."""
    result = EvaluationResult(
        run_id="r-001",
        instance_id="django__django-001",
        agent_id="robust_balanced",
        trace_format=None,
        functional={"resolved": None, "status": "skipped", "evaluation_backend": "none",
                    "report_path": None, "evidence": {}},
        extended={"run_metrics": {}, "patch_metrics": {}, "availability": {}, "errors": []},
    )
    d = result.to_dict()
    assert "functional" in d
    assert "extended" in d
    assert "run_id" in d
    assert "agent_id" in d
    assert "profile" not in d


def test_evaluation_result_from_dict_roundtrip():
    """Comprueba que evaluation result from dict roundtrip."""
    original = EvaluationResult(
        run_id="r-001",
        instance_id="inst",
        agent_id="default",
        trace_format="robust-agent-1.0",
        functional={"resolved": True, "status": "evaluated", "evaluation_backend": "sb_cli",
                    "report_path": None, "evidence": {}},
        extended={"run_metrics": {"steps_used": 5}, "patch_metrics": {}, "availability": {}, "errors": []},
    )
    restored = EvaluationResult.from_dict(original.to_dict())
    assert restored.run_id == original.run_id
    assert restored.functional["resolved"] is True
    assert restored.extended["run_metrics"]["steps_used"] == 5


def test_evaluation_result_try_load_returns_none_on_missing_file(tmp_path: Path):
    """Comprueba que evaluation result try load returns none on missing file."""
    path = tmp_path / "does_not_exist.json"
    assert EvaluationResult.try_load(path) is None


def test_evaluation_result_try_load_returns_none_on_old_schema(tmp_path: Path):
    """Ficheros sin bloques `functional`/`extended` se ignoran."""
    path = tmp_path / "evaluation_result.json"
    path.write_text('{"run_id": "r-001", "resolved": true}', encoding="utf-8")
    assert EvaluationResult.try_load(path) is None


def test_evaluation_result_try_load_loads_valid_file(tmp_path: Path):
    """Comprueba que evaluation result try load loads valid file."""
    result = EvaluationResult(
        run_id="r-x",
        instance_id="inst",
        agent_id="default",
        trace_format=None,
        functional={"resolved": False, "status": "evaluated", "evaluation_backend": "sb_cli",
                    "report_path": None, "evidence": {}},
        extended={"run_metrics": {}, "patch_metrics": {}, "availability": {}, "errors": []},
    )
    path = tmp_path / "evaluation_result.json"
    path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    loaded = EvaluationResult.try_load(path)
    assert loaded is not None
    assert loaded.run_id == "r-x"
    assert loaded.functional["resolved"] is False


# ---------------------------------------------------------------------------
# RunMetricsExtractor
# ---------------------------------------------------------------------------


_ROBUST_TRACE = {
    "trajectory_format": "mini-swe-agent-1.1",
    "robust_agent": {
        "format_version": "robust-agent-1.0",
        "termination": "model_submitted",
        "episode_state_final": {
            "budget": {"steps_used": 12, "cost_used": 0.34}
        },
        "steps": [],
    },
    "info": {"exit_status": "Submitted"},
}

_DEFAULT_TRACE = {
    "trajectory_format": "mini-swe-agent-1.1",
    "info": {"exit_status": "Submitted"},
}


def _write_trace(tmp_path: Path, data: dict) -> Path:
    """Auxiliar interno: write trace."""
    path = tmp_path / "trajectory.traj.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_run_metrics_extractor_parses_robust_trace(tmp_path: Path):
    """Comprueba que run metrics extractor parses robust trace."""
    traj = _write_trace(tmp_path, _ROBUST_TRACE)
    record = _run_record(exit_status="Submitted", duration=90.0)
    metrics, avail, trace_format = RunMetricsExtractor.extract(
        trajectory_path=traj, run_record=record
    )
    assert trace_format == "robust-agent-1.0"
    assert metrics["termination"] == "model_submitted"
    assert metrics["steps_used"] == 12
    assert metrics["cost_total"] == pytest.approx(0.34)
    assert metrics["wallclock_seconds"] == 90.0
    assert avail["status"] == "available"


def test_run_metrics_extractor_handles_missing_trace():
    """Comprueba que run metrics extractor handles missing trace."""
    record = _run_record(exit_status="Submitted", trajectory_path=None)
    metrics, avail, trace_format = RunMetricsExtractor.extract(
        trajectory_path=None, run_record=record
    )
    assert trace_format is None
    assert avail["status"] == "error"
    assert "trajectory_path_missing" in avail["cause"]


def test_run_metrics_extractor_infers_termination_for_baseline(tmp_path: Path):
    """Comprueba que run metrics extractor infers termination for baseline."""
    traj = _write_trace(tmp_path, _DEFAULT_TRACE)
    record = _run_record(exit_status="Submitted", duration=30.0)
    metrics, avail, trace_format = RunMetricsExtractor.extract(
        trajectory_path=traj, run_record=record
    )
    assert trace_format is None
    assert metrics["exit_status"] == "Submitted"
    assert metrics["termination"] == "model_submitted"


def test_run_metrics_extractor_missing_trajectory_file(tmp_path: Path):
    """Comprueba que run metrics extractor missing trajectory file."""
    fake_path = tmp_path / "nonexistent.traj.json"
    record = _run_record(trajectory_path="nonexistent.traj.json")
    metrics, avail, trace_format = RunMetricsExtractor.extract(
        trajectory_path=fake_path, run_record=record
    )
    assert avail["status"] == "error"


# ---------------------------------------------------------------------------
# PatchMetricsExtractor
# ---------------------------------------------------------------------------


_GOLD_PATCH = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    " context\n"
    "-old line\n"
    "+new line\n"
    " more context\n"
)

_MODEL_PATCH = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    " context\n"
    "-old line\n"
    "+new line\n"
    " more context\n"
)


def test_patch_metrics_extractor_computes_metrics():
    """Comprueba que patch metrics extractor computes metrics."""
    metrics = PatchMetricsExtractor.extract(
        model_patch=_MODEL_PATCH, gold_patch=_GOLD_PATCH
    )
    assert metrics["lines_added"] >= 0
    assert metrics["lines_deleted"] >= 0
    assert isinstance(metrics["files_modified"], list)
    assert isinstance(metrics["jaccard_files"], float)


def test_patch_metrics_not_applicable_on_empty_patches():
    """Comprueba que patch metrics not applicable on empty patches."""
    avail = PatchMetricsExtractor.availability(model_patch=None, gold_patch=None)
    assert avail["status"] == "not_applicable"


def test_patch_metrics_not_applicable_on_empty_model_patch():
    """Comprueba que patch metrics not applicable on empty model patch."""
    avail = PatchMetricsExtractor.availability(model_patch="", gold_patch=_GOLD_PATCH)
    assert avail["status"] == "not_applicable"


def test_patch_metrics_available_when_both_present():
    """Comprueba que patch metrics available when both present."""
    avail = PatchMetricsExtractor.availability(model_patch=_MODEL_PATCH, gold_patch=_GOLD_PATCH)
    assert avail["status"] == "available"


# Parche en formato unified diff estandar (sin cabecera "diff --git").
# Usado por agentes como Moatless que no incluyen la cabecera Git extendida.
_UNIFIED_PATCH = (
    "--- a/src/module.py\n"
    "+++ b/src/module.py\n"
    "@@ -10,6 +10,7 @@\n"
    " context line\n"
    "-removed line\n"
    "+added line one\n"
    "+added line two\n"
    " more context\n"
)

_UNIFIED_PATCH_MULTIFILE = (
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
    "--- a/bar.py\n"
    "+++ b/bar.py\n"
    "@@ -5 +5 @@\n"
    "-x\n"
    "+y\n"
)

_UNIFIED_GOLD = (
    "--- a/src/module.py\n"
    "+++ b/src/module.py\n"
    "@@ -10,5 +10,5 @@\n"
    " context line\n"
    "-removed line\n"
    "+replacement\n"
    " more context\n"
)


def test_patch_metrics_unified_format_detects_files():
    """Unified diff sin 'diff --git' debe detectar correctamente el fichero modificado."""
    metrics = PatchMetricsExtractor.extract(model_patch=_UNIFIED_PATCH, gold_patch=None)
    assert metrics["files_modified"] == ["src/module.py"]
    assert metrics["lines_added"] == 2
    assert metrics["lines_deleted"] == 1
    assert metrics["hunks_count"] == 1


def test_patch_metrics_unified_format_multifile():
    """Multiples ficheros en formato unified diff se detectan todos."""
    metrics = PatchMetricsExtractor.extract(model_patch=_UNIFIED_PATCH_MULTIFILE, gold_patch=None)
    assert sorted(metrics["files_modified"]) == ["bar.py", "foo.py"]
    assert metrics["hunks_count"] == 2


def test_patch_metrics_unified_format_jaccard_with_gold():
    """Jaccard se calcula correctamente con gold en formato unified diff."""
    metrics = PatchMetricsExtractor.extract(
        model_patch=_UNIFIED_PATCH, gold_patch=_UNIFIED_GOLD
    )
    assert metrics["jaccard_files"] == 1.0  # mismo fichero
    assert isinstance(metrics["jaccard_lines"], float)


def test_patch_metrics_git_format_takes_priority_over_unified():
    """Si hay cabecera 'diff --git', se usa esa logica (no el fallback unified)."""
    git_patch = (
        "diff --git a/real.py b/real.py\n"
        "--- a/real.py\n"
        "+++ b/real.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    metrics = PatchMetricsExtractor.extract(model_patch=git_patch, gold_patch=None)
    assert metrics["files_modified"] == ["real.py"]


def test_patch_metrics_unified_dev_null_uses_source():
    """Si +++ b//dev/null, se usa el path del --- a/."""
    delete_patch = (
        "--- a/to_delete.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-line1\n"
        "-line2\n"
        "-line3\n"
    )
    metrics = PatchMetricsExtractor.extract(model_patch=delete_patch, gold_patch=None)
    assert metrics["files_modified"] == ["to_delete.py"]


# ---------------------------------------------------------------------------
# _group_by_agent_model
# ---------------------------------------------------------------------------


def test_group_by_agent_model_groups_correctly():
    """Comprueba que group by agent model groups correctly."""
    records = [
        _run_record(run_id="r1", agent_id="robust_balanced"),
        _run_record(run_id="r2", agent_id="robust_balanced"),
        _run_record(run_id="r3", agent_id="default"),
    ]
    groups = _group_by_agent_model(records)
    assert len(groups) == 2
    assert len(groups[("robust_balanced", "gemini/gemini-3-flash")]) == 2
    assert len(groups[("default", "gemini/gemini-3-flash")]) == 1


# ---------------------------------------------------------------------------
# Evaluator (idempotencia)
# ---------------------------------------------------------------------------


class _FakeResultsModule:
    """Mock minimal de `BenchmarkResultsModule` para tests de evaluacion."""

    def __init__(self, tmp_path: Path) -> None:
        """Inicializa `_FakeResultsModule`."""
        self.root = tmp_path
        self._cache: dict[tuple[str, str], Path] = {}
        self.persisted: dict[str, EvaluationResult] = {}

    def evaluation_result_path(self, instance_id: str, run_id: str) -> Path | None:
        """Evaluation result path."""
        return self._cache.get((instance_id, run_id))

    def persist_evaluation_result(self, result: EvaluationResult) -> None:
        """Persist evaluation result."""
        self.persisted[result.run_id] = result

    def groups_directory(self, group_id: str) -> Path:
        """Groups directory."""
        path = self.root / "groups" / group_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_artifact_path(self, instance_id: str, run_id: str, filename: str) -> Path:
        """Run artifact path."""
        p = self.root / "instances" / instance_id / run_id / filename
        return p

    def export_path(self) -> Path:
        """Export path."""
        return self.root


def test_evaluator_skips_run_when_cached(tmp_path: Path):
    """Si `evaluation_result.json` ya existe y es valido, no reevalua."""
    cached_result = EvaluationResult(
        run_id="r-001",
        instance_id="django__django-001",
        agent_id="robust_balanced",
        trace_format=None,
        functional={"resolved": True, "status": "evaluated", "evaluation_backend": "sb_cli",
                    "report_path": None, "evidence": {}},
        extended={"run_metrics": {}, "patch_metrics": {}, "availability": {}, "errors": []},
    )
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached_result.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-001")] = cache_path

    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="none"),
        results_module=rm,
        sb_cli_client=None,
    )
    record = _run_record(run_id="r-001")
    results = evaluator.evaluate_runs(
        [record], [_instance()], _experiment_config()
    )
    assert "r-001" in results
    assert results["r-001"].functional["resolved"] is True
    assert "r-001" not in rm.persisted


def test_evaluator_with_no_backend_produces_skipped_functional(tmp_path: Path):
    """Con `functional_backend='none'`, el resultado funcional es `skipped`."""
    rm = _FakeResultsModule(tmp_path)
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="none"),
        results_module=rm,
        sb_cli_client=None,
    )
    record = _run_record(status="completed", model_patch_path=None)
    results = evaluator.evaluate_runs(
        [record], [_instance()], _experiment_config()
    )
    assert "r-abc123" in results
    result = results["r-abc123"]
    assert result.functional["status"] == "skipped"
    assert result.functional["resolved"] is None
    assert "r-abc123" in rm.persisted


def test_evaluator_evaluation_skip_does_not_call_sb_cli(tmp_path: Path):
    """Con `evaluation_skip=True`, runs completados no consumen sb-cli."""
    rm = _FakeResultsModule(tmp_path)
    client = MagicMock()
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=True),
        results_module=rm,
        sb_cli_client=client,
    )
    record = replace(
        _run_record(status="completed"),
        preds_entry={
            "instance_id": "django__django-001",
            "model_name_or_path": "robust_balanced",
            "model_patch": "diff --git a/x b/x\n",
        },
    )
    results = evaluator.evaluate_runs(
        [record], [_instance()], _experiment_config()
    )
    client.submit.assert_not_called()
    assert results["r-abc123"].functional["status"] == "skipped"
    assert results["r-abc123"].functional["evidence"]["cause"] == "evaluation_skip"
    assert "r-abc123" in rm.persisted


def _cached_functional_result(
    *, run_id: str, status: str, cause: str | None = None, resolved: bool | None = None
) -> EvaluationResult:
    """Auxiliar interno: construye un EvaluationResult cacheado con un `functional.status` dado."""
    evidence: dict[str, Any] = {"cause": cause} if cause is not None else {}
    return EvaluationResult(
        run_id=run_id,
        instance_id="django__django-001",
        agent_id="robust_balanced",
        trace_format=None,
        functional={
            "resolved": resolved,
            "status": status,
            "evaluation_backend": "sb_cli" if status == "evaluated" else "none",
            "report_path": None,
            "evidence": evidence,
        },
        extended={"run_metrics": {}, "patch_metrics": {}, "availability": {}, "errors": []},
    )


def test_evaluator_promotes_cached_skipped_by_config_when_evaluation_activates(tmp_path: Path):
    """Fase 2 (`--evaluate`): un run cacheado `skipped/evaluation_skip` se re-somete a sb-cli.

    Regresion del bug donde `evaluate_runs` nunca volvia a evaluar nada tras una
    fase 1 con `evaluation_skip=True`, porque `status="skipped"` se trataba como
    definitivo igual que `status="evaluated"`.
    """
    cached = _cached_functional_result(run_id="r-abc123", status="skipped", cause="evaluation_skip")
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-abc123")] = cache_path

    client = MagicMock()
    client.submit.return_value = MagicMock(
        status="evaluated",
        backend_run_id="be-1",
        report={"resolved_ids": ["django__django-001"], "unresolved_ids": []},
        report_path=None,
        command=["sb-cli", "submit"],
        log_excerpt=None,
        error_reason=None,
    )
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=False),
        results_module=rm,
        sb_cli_client=client,
    )
    record = replace(
        _run_record(status="completed"),
        preds_entry={
            "instance_id": "django__django-001",
            "model_name_or_path": "robust_balanced",
            "model_patch": "diff --git a/x b/x\n",
        },
    )
    results = evaluator.evaluate_runs([record], [_instance()], _experiment_config())

    client.submit.assert_called_once()
    assert results["r-abc123"].functional["status"] == "evaluated"
    assert results["r-abc123"].functional["resolved"] is True
    assert "r-abc123" in rm.persisted


def test_evaluator_never_resubmits_already_evaluated_group(tmp_path: Path):
    """Un run cacheado `evaluated` no vuelve a gastar cuota aunque la evaluacion este activa."""
    cached = _cached_functional_result(run_id="r-abc123", status="evaluated", resolved=True)
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-abc123")] = cache_path

    client = MagicMock()
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=False),
        results_module=rm,
        sb_cli_client=client,
    )
    record = replace(
        _run_record(status="completed"),
        preds_entry={
            "instance_id": "django__django-001",
            "model_name_or_path": "robust_balanced",
            "model_patch": "diff --git a/x b/x\n",
        },
    )
    results = evaluator.evaluate_runs([record], [_instance()], _experiment_config())

    client.submit.assert_not_called()
    assert results["r-abc123"].functional["status"] == "evaluated"
    assert "r-abc123" not in rm.persisted


def test_evaluator_keeps_failed_run_skipped_without_calling_sb_cli(tmp_path: Path):
    """Un run cacheado `skipped/run_status=failed` sigue sin llamar a sb-cli.

    Se recalcula (barato, sin red) pero no es funcionalmente evaluable, asi que
    no debe generar una llamada `sb-cli submit`.
    """
    cached = _cached_functional_result(
        run_id="r-fail1", status="skipped", cause="run_status=failed"
    )
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-fail1")] = cache_path

    client = MagicMock()
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=False),
        results_module=rm,
        sb_cli_client=client,
    )
    record = _run_record(run_id="r-fail1", status="failed", exit_status="EmptySubmission")
    results = evaluator.evaluate_runs([record], [_instance()], _experiment_config())

    client.submit.assert_not_called()
    assert results["r-fail1"].functional["status"] == "skipped"
    assert results["r-fail1"].functional["evidence"]["cause"] == "run_status=failed"


def test_evaluator_force_agent_ids_resubmits_evaluated_group_with_new_run_id(tmp_path: Path):
    """`force_agent_ids` reenvia un grupo `evaluated` bajo un `run_id` nuevo.

    Regresion P19: `sb-cli` bloquea permanentemente las instancias ya sometidas
    bajo un `run_id` ("no se pueden cambiar"), asi que un reintento deliberado
    de un grupo cuyo `evaluated` previo se sospecha basura del backend debe
    generar un `run_id` distinto del `group_id` normal, o `sb-cli` se limitaria
    a devolver el reporte viejo sin re-evaluar nada.
    """
    cached = _cached_functional_result(run_id="r-abc123", status="evaluated", resolved=False)
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-abc123")] = cache_path

    client = MagicMock()
    client.submit.return_value = MagicMock(
        status="evaluated",
        backend_run_id="be-2",
        report={"resolved_ids": ["django__django-001"], "unresolved_ids": []},
        report_path=None,
        command=["sb-cli", "submit"],
        log_excerpt=None,
        error_reason=None,
    )
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=False),
        results_module=rm,
        sb_cli_client=client,
    )
    record = replace(
        _run_record(status="completed"),
        preds_entry={
            "instance_id": "django__django-001",
            "model_name_or_path": "robust_balanced",
            "model_patch": "diff --git a/x b/x\n",
        },
    )
    results = evaluator.evaluate_runs(
        [record],
        [_instance()],
        _experiment_config(),
        force_agent_ids=frozenset({"robust_balanced"}),
    )

    client.submit.assert_called_once()
    _, kwargs = client.submit.call_args
    expected_group_id = f"robust_balanced__{record.agent_run_config.model_id.replace('/', '-')}"
    assert kwargs["run_id"] is not None
    assert kwargs["run_id"] != expected_group_id
    assert kwargs["run_id"].startswith(f"{expected_group_id}-retry")
    assert results["r-abc123"].functional["status"] == "evaluated"
    assert results["r-abc123"].functional["resolved"] is True
    assert "r-abc123" in rm.persisted


def test_evaluator_force_agent_ids_ignores_unrelated_agents(tmp_path: Path):
    """`force_agent_ids` no afecta a grupos de otros `agent_id` ya `evaluated`."""
    cached = _cached_functional_result(run_id="r-abc123", status="evaluated", resolved=True)
    cache_path = tmp_path / "cached.json"
    cache_path.write_text(json.dumps(cached.to_dict()), encoding="utf-8")

    rm = _FakeResultsModule(tmp_path)
    rm._cache[("django__django-001", "r-abc123")] = cache_path

    client = MagicMock()
    evaluator = Evaluator(
        config=BenchmarkConfig(functional_backend="sb_cli", evaluation_skip=False),
        results_module=rm,
        sb_cli_client=client,
    )
    record = replace(
        _run_record(status="completed", agent_id="robust_balanced"),
        preds_entry={
            "instance_id": "django__django-001",
            "model_name_or_path": "robust_balanced",
            "model_patch": "diff --git a/x b/x\n",
        },
    )
    results = evaluator.evaluate_runs(
        [record],
        [_instance()],
        _experiment_config(),
        force_agent_ids=frozenset({"robust_v2"}),
    )

    client.submit.assert_not_called()
    assert results["r-abc123"].functional["status"] == "evaluated"
    assert "r-abc123" not in rm.persisted


@pytest.mark.parametrize(
    ("subset", "expected"),
    [
        ("lite", "swe-bench_lite"),
        ("", "swe-bench_lite"),
        ("verified", "swe-bench_verified"),
        ("m", "swe-bench-m"),
        ("swe-bench-lite", "swe-bench_lite"),
    ],
)
def test_sb_cli_subset_alias(subset: str, expected: str):
    """Comprueba que sb cli subset alias."""
    assert _sb_cli_subset_alias(subset) == expected


def test_sb_cli_client_passes_api_key_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Comprueba que sb cli client passes api key from env."""
    monkeypatch.setenv("SWEBENCH_API_KEY", "secret-key")
    captured: dict[str, Any] = {}

    def _runner(command, **kwargs):
        """Auxiliar interno: runner."""
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 1, "", "401")

    client = SbCliClient(runner=_runner, max_retries=0)
    preds = tmp_path / "preds.json"
    preds.write_text("[]", encoding="utf-8")
    report_dir = tmp_path / "report"
    client.submit(subset="swe-bench_lite", split="test", predictions_path=preds, report_dir=report_dir)

    assert "--api_key" in captured["command"]
    assert captured["command"][captured["command"].index("--api_key") + 1] == "secret-key"
    assert captured["env"]["SWEBENCH_API_KEY"] == "secret-key"


def test_sb_cli_client_passes_explicit_run_id(tmp_path: Path):
    """`run_id` explicito se traduce en `--run_id <valor>` en el comando.

    Sin esto, `sb-cli` usa el nombre del directorio padre de `predictions_path`
    como `run_id` implicito, y un reintento tras un fallo del backend
    reutilizaria el mismo `run_id` ya bloqueado ("no se pueden cambiar") en
    vez de disparar una evaluacion nueva.
    """
    captured: dict[str, Any] = {}

    def _runner(command, **kwargs):
        """Auxiliar interno: runner."""
        captured["command"] = command
        return subprocess.CompletedProcess(command, 1, "", "error")

    client = SbCliClient(runner=_runner, max_retries=0)
    preds = tmp_path / "group_dir" / "preds.json"
    preds.parent.mkdir(parents=True, exist_ok=True)
    preds.write_text("[]", encoding="utf-8")
    report_dir = tmp_path / "report"

    client.submit(
        subset="swe-bench_lite",
        split="test",
        predictions_path=preds,
        report_dir=report_dir,
        run_id="group_dir-retry123",
    )

    assert "--run_id" in captured["command"]
    assert captured["command"][captured["command"].index("--run_id") + 1] == "group_dir-retry123"


def test_sb_cli_client_omits_run_id_flag_when_not_given(tmp_path: Path):
    """Sin `run_id` explicito, no se anade `--run_id` (comportamiento historico)."""
    captured: dict[str, Any] = {}

    def _runner(command, **kwargs):
        """Auxiliar interno: runner."""
        captured["command"] = command
        return subprocess.CompletedProcess(command, 1, "", "error")

    client = SbCliClient(runner=_runner, max_retries=0)
    preds = tmp_path / "preds.json"
    preds.write_text("[]", encoding="utf-8")
    report_dir = tmp_path / "report"

    client.submit(subset="swe-bench_lite", split="test", predictions_path=preds, report_dir=report_dir)

    assert "--run_id" not in captured["command"]
