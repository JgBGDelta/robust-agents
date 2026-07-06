"""Tests del `BenchmarkRunner.run()` con los cuatro modulos mockeados.

Cubren:
  - orden de invocacion correcto,
  - propagacion del `experiment_id` y de `force_rerun`,
  - manejo de excepciones en cascada,
  - **preflight sb-cli**: abort si no disponible (antes de `initialize`),
  - validacion cruzada del `ExperimentConfig`,
  - CLI: parseo YAML y overrides de flags.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from benchmark import (
    AgentRunConfig,
    BenchmarkConfig,
    BenchmarkExport,
    BenchmarkResultsModule,
    BenchmarkRunner,
    BenchmarkTaskResult,
    DatasetConfig,
    EvaluationResult,
    ExperimentConfig,
)
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.sb_cli_client import SbCliClient
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.run import _apply_cli_overrides, _load_experiment_config


@pytest.fixture(autouse=True)
def _default_swebench_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture de pytest:  default swebench api key."""
    monkeypatch.setenv("SWEBENCH_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _mock_preflight_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture de pytest:  mock preflight docker."""
    monkeypatch.setattr("benchmark.benchmark_runner._preflight_docker", lambda _cfg: None)


# --- Fixtures auxiliares ---------------------------------------------------------


def _agent(agent_id: str = "robust_balanced") -> AgentRunConfig:
    """Auxiliar interno: agent."""
    return AgentRunConfig(
        agent_id=agent_id,
        agent_class="agent.robust_agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
    )


def _experiment_config(*, force_rerun: bool = False) -> ExperimentConfig:
    """Auxiliar interno: experiment config."""
    return ExperimentConfig(
        dataset=DatasetConfig(slice_size=2),
        agents=[_agent()],
        benchmark=BenchmarkConfig(workers=1),
        force_rerun=force_rerun,
    )


def _instance(instance_id: str = "repo__pkg-1") -> BenchmarkInstance:
    """Auxiliar interno: instance."""
    return BenchmarkInstance(
        instance_id=instance_id,
        dataset_name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        repo="repo/pkg",
        base_commit="abc",
        problem_statement="x",
        gold_patch=None,
    )


def _run_record(run_id: str, instance_id: str) -> BenchmarkRunRecord:
    """Auxiliar interno: run record."""
    return BenchmarkRunRecord(
        run_id=run_id,
        instance_id=instance_id,
        agent_run_config=_agent(),
        status="completed",
        started_at=0.0,
        ended_at=10.0,
        duration_seconds=10.0,
        exit_status="model_submitted",
    )


# --- Mocks -----------------------------------------------------------------------


class _RecordingMock:
    """Mock base que registra invocaciones de metodo en una lista compartida."""

    def __init__(self, name: str, calls: list[tuple[str, str]]) -> None:
        """Asocia el mock a un nombre logico y al buffer de llamadas."""
        self._name = name
        self._calls = calls

    def _log(self, method: str) -> None:
        """Auxiliar interno: log."""
        self._calls.append((self._name, method))


class _DatasetMock(_RecordingMock):
    """Clase auxiliar de test `_DatasetMock`."""
    def __init__(self, calls: list[tuple[str, str]], instances: list[BenchmarkInstance]) -> None:
        """Inicializa `_DatasetMock`."""
        super().__init__("dataset", calls)
        self._instances = instances

    def load_instances(self, dataset_config: DatasetConfig) -> list[BenchmarkInstance]:
        """Load instances."""
        self._log("load_instances")
        return list(self._instances)

    def selection_summary(self) -> dict[str, Any]:
        """Selection summary."""
        self._log("selection_summary")
        return {"num_instances_after_filter": len(self._instances), "instance_ids": [i.instance_id for i in self._instances]}


class _ExecutionMock(_RecordingMock):
    """Clase auxiliar de test `_ExecutionMock`."""
    def __init__(
        self,
        calls: list[tuple[str, str]],
        records: list[BenchmarkRunRecord],
        *,
        raises: BaseException | None = None,
    ) -> None:
        """Inicializa `_ExecutionMock`."""
        super().__init__("execution", calls)
        self._records = records
        self._raises = raises

    def run_matrix(
        self,
        experiment_config: ExperimentConfig,
        instances: list[BenchmarkInstance],
        *,
        progress: Any = None,
    ) -> list[BenchmarkRunRecord]:
        """Run matrix."""
        self._log("run_matrix")
        if self._raises is not None:
            raise self._raises
        return list(self._records)


class _EvaluationMock(_RecordingMock):
    """Clase auxiliar de test `_EvaluationMock`."""
    def __init__(
        self,
        calls: list[tuple[str, str]],
        task_results: dict[str, EvaluationResult],
    ) -> None:
        """Inicializa `_EvaluationMock`."""
        super().__init__("evaluation", calls)
        self._task_results = task_results

    def evaluate_runs(
        self,
        run_records: list[BenchmarkRunRecord],
        instances: list[BenchmarkInstance],
        experiment_config: ExperimentConfig,
        *,
        force_agent_ids: frozenset[str] | None = None,
    ) -> dict[str, EvaluationResult]:
        """Evaluate runs."""
        self._log("evaluate_runs")
        self.received_records = list(run_records)
        self.received_force_agent_ids = force_agent_ids
        return dict(self._task_results)


class _ResultsMock(_RecordingMock):
    """Clase auxiliar de test `_ResultsMock`."""
    def __init__(
        self,
        calls: list[tuple[str, str]],
        *,
        experiment_id: str = "test-exp",
        export: BenchmarkExport | None = None,
        export_path: Path | None = None,
    ) -> None:
        """Inicializa `_ResultsMock`."""
        super().__init__("results", calls)
        self._experiment_id = experiment_id
        self._export = export or BenchmarkExport(
            experiment_id=experiment_id,
            experiment_path=Path("runs") / experiment_id,
            manifest_path=Path("runs") / experiment_id / "manifest.json",
            dataset_summary_path=Path("runs") / experiment_id / "dataset_summary.json",
            config_path=Path("runs") / experiment_id / "config.yaml",
            runs_count=0,
            status="finalized",
        )
        self._export_path = export_path or Path("runs") / experiment_id
        self.dataset_summary: dict[str, Any] | None = None
        self.aborted_reason: str | None = None
        self.matrix_size: int | None = None

    @property
    def experiment_id(self) -> str:
        """Experiment id."""
        return self._experiment_id

    def set_dataset_summary(self, summary: dict[str, Any]) -> None:
        """Set dataset summary."""
        self._log("set_dataset_summary")
        self.dataset_summary = dict(summary)

    def initialize(self, *, matrix_size: int = 0):  # type: ignore[no-untyped-def]
        """Registra la inicializacion y el tamano de la matriz de runs."""
        self._log("initialize")
        self.matrix_size = matrix_size
        return None

    def mark_running(self) -> None:
        """Marca el experimento como en ejecucion."""
        self._log("mark_running")

    def finalize(self, task_results: list[Any]) -> BenchmarkExport:
        """Finalize."""
        self._log("finalize")
        self.finalize_count = len(task_results)
        return self._export

    def pending_runs(self, run_list: list[Any]) -> list[Any]:
        """Pending runs."""
        return list(run_list)

    def mark_aborted(self, reason: str) -> None:
        """Mark aborted."""
        self._log("mark_aborted")
        self.aborted_reason = reason

    def export_path(self) -> Path:
        """Export path."""
        return self._export_path


def _make_sb_cli_available(available: bool) -> SbCliClient:
    """Auxiliar interno: make sb cli available."""
    client = MagicMock(spec=SbCliClient)
    client.is_available.return_value = available
    return client


def _build_runner(
    *,
    instances: list[BenchmarkInstance] | None = None,
    records: list[BenchmarkRunRecord] | None = None,
    task_results: dict[str, EvaluationResult] | None = None,
    execution_raises: BaseException | None = None,
    experiment_config: ExperimentConfig | None = None,
    sb_cli_available: bool = True,
):
    """Auxiliar interno: build runner."""
    calls: list[tuple[str, str]] = []
    instances = instances or [_instance("repo__pkg-1"), _instance("repo__pkg-2")]
    records = records if records is not None else [_run_record("r-1", "repo__pkg-1")]
    task_results = task_results if task_results is not None else {}

    results = _ResultsMock(calls)
    dataset = _DatasetMock(calls, instances)
    execution = _ExecutionMock(calls, records, raises=execution_raises)
    evaluation = _EvaluationMock(calls, task_results)
    sb_cli = _make_sb_cli_available(sb_cli_available)

    runner = BenchmarkRunner(
        experiment_config or _experiment_config(),
        results_module=results,  # type: ignore[arg-type]
        dataset_module=dataset,  # type: ignore[arg-type]
        execution_module=execution,  # type: ignore[arg-type]
        evaluation_module=evaluation,  # type: ignore[arg-type]
        sb_cli_client=sb_cli,  # type: ignore[arg-type]
    )
    return runner, calls, results, dataset, execution, evaluation


# --- Validacion del ExperimentConfig --------------------------------------------


def test_runner_rejects_empty_agents_list():
    """Comprueba que runner rejects empty agents list."""
    cfg = ExperimentConfig(dataset=DatasetConfig(slice_size=1), agents=[])
    with pytest.raises(ValueError, match="agents no puede estar vacio"):
        BenchmarkRunner(cfg)


def test_runner_rejects_duplicate_agent_cells():
    """Comprueba que runner rejects duplicate agent cells."""
    cfg = ExperimentConfig(
        dataset=DatasetConfig(slice_size=1),
        agents=[_agent(), _agent()],
    )
    with pytest.raises(ValueError, match="duplicadas"):
        BenchmarkRunner(cfg)


# --- Orden de invocacion ---------------------------------------------------------


def test_run_invokes_modules_in_correct_order():
    """Comprueba que run invokes modules in correct order."""
    runner, calls, *_ = _build_runner()
    export = runner.run()

    expected = [
        ("dataset", "load_instances"),
        ("dataset", "selection_summary"),
        ("results", "set_dataset_summary"),
        ("results", "initialize"),
        ("results", "mark_running"),
        ("execution", "run_matrix"),
        ("evaluation", "evaluate_runs"),
        ("results", "finalize"),
    ]
    assert calls == expected
    assert isinstance(export, BenchmarkExport)


def test_run_propagates_dataset_summary_to_results_module():
    """Comprueba que run propagates dataset summary to results module."""
    runner, _calls, results, *_ = _build_runner()
    runner.run()
    assert results.dataset_summary is not None
    assert "num_instances_after_filter" in results.dataset_summary
    assert results.dataset_summary["num_instances_after_filter"] == 2


def test_run_passes_matrix_size_to_initialize():
    """Comprueba que run passes matrix size to initialize."""
    cfg = _experiment_config()
    runner, _calls, results, *_ = _build_runner(experiment_config=cfg)
    runner.run()
    assert results.matrix_size == 2


def test_run_with_only_agent_ids_filters_records_passed_to_evaluation():
    """`only_agent_ids` restringe que records llegan a `evaluate_runs`.

    Permite someter a `sb-cli` un perfil/ablacion a la vez (control de cuota)
    sin tocar `ExperimentConfig` ni la validacion de reanudacion.
    """
    from dataclasses import replace

    records = [
        _run_record("r-1", "repo__pkg-1"),
        replace(
            _run_record("r-2", "repo__pkg-2"),
            agent_run_config=_agent("robust_strict"),
        ),
    ]
    runner, _calls, _results, _dataset, _execution, evaluation = _build_runner(records=records)

    runner.run(only_agent_ids=frozenset({"robust_strict"}))

    received_ids = {r.run_id for r in evaluation.received_records}
    assert received_ids == {"r-2"}


def test_run_without_only_agent_ids_passes_all_records_to_evaluation():
    """Sin `only_agent_ids`, el comportamiento por defecto no cambia (todos los records)."""
    from dataclasses import replace

    records = [
        _run_record("r-1", "repo__pkg-1"),
        replace(
            _run_record("r-2", "repo__pkg-2"),
            agent_run_config=_agent("robust_strict"),
        ),
    ]
    runner, _calls, _results, _dataset, _execution, evaluation = _build_runner(records=records)

    runner.run()

    received_ids = {r.run_id for r in evaluation.received_records}
    assert received_ids == {"r-1", "r-2"}


# --- evaluation_only: --evaluate no debe re-ejecutar agentes --------------------


def test_run_with_evaluation_only_never_calls_execution_run_matrix():
    """`evaluation_only=True` no debe invocar `execution_module.run_matrix`.

    Regresion: tras permitir el reintento de `EmptySubmission` en reanudacion
    (P16), una pasada `--evaluate` sin este flag ejecutaba de verdad cualquier
    run no terminal de TODO el experimento (no solo el agente filtrado por
    `--evaluate-agents`), levantando contenedores Docker y gastando cuota del
    LLM durante lo que debia ser una pasada de solo evaluacion (P18).
    """
    runner, calls, *_ = _build_runner()
    runner.run(evaluation_only=True)
    assert ("execution", "run_matrix") not in calls


def test_run_with_evaluation_only_skips_docker_preflight(monkeypatch: pytest.MonkeyPatch):
    """`evaluation_only=True` tampoco debe comprobar/levantar Docker."""
    docker_preflight = MagicMock()
    monkeypatch.setattr("benchmark.benchmark_runner._preflight_docker", docker_preflight)
    runner, *_ = _build_runner()
    runner.run(evaluation_only=True)
    docker_preflight.assert_not_called()


def test_run_with_evaluation_only_still_evaluates_persisted_records():
    """`evaluation_only=True` sigue invocando la evaluacion (paso 5), solo salta ejecucion."""
    runner, calls, *_ = _build_runner()
    runner.run(evaluation_only=True)
    assert ("evaluation", "evaluate_runs") in calls
    assert ("results", "finalize") in calls


def test_run_without_evaluation_only_still_calls_execution_run_matrix():
    """Por defecto (`evaluation_only=False`), el comportamiento no cambia."""
    runner, calls, *_ = _build_runner()
    runner.run()
    assert ("execution", "run_matrix") in calls


# --- force_reevaluate_agents: reintento deliberado de un grupo `evaluated` -------


def test_run_propagates_force_reevaluate_agents_to_evaluation_module():
    """`force_reevaluate_agents` llega intacto a `evaluation_module.evaluate_runs`."""
    runner, _calls, _results, _dataset, _execution, evaluation = _build_runner()
    runner.run(force_reevaluate_agents=frozenset({"robust_v2"}))
    assert evaluation.received_force_agent_ids == frozenset({"robust_v2"})


def test_run_without_force_reevaluate_agents_passes_none():
    """Sin `force_reevaluate_agents`, se propaga `None` (comportamiento por defecto)."""
    runner, _calls, _results, _dataset, _execution, evaluation = _build_runner()
    runner.run()
    assert evaluation.received_force_agent_ids is None


def test_cli_parser_accepts_force_reevaluate_agents_flag():
    """Comprueba que `--force-reevaluate-agents` se parsea junto a `--evaluate`."""
    from benchmark.run import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        ["--config", "exp.yaml", "--evaluate", "--force-reevaluate-agents", "robust_v2"]
    )
    assert args.evaluate is True
    assert args.force_reevaluate_agents == "robust_v2"


def test_cli_main_rejects_force_reevaluate_agents_without_evaluate(
    capsys: pytest.CaptureFixture[str],
):
    """`--force-reevaluate-agents` sin `--evaluate` debe fallar rapido."""
    from benchmark.run import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--config", "does-not-exist.yaml", "--force-reevaluate-agents", "robust_v2"])
    assert exc_info.value.code == 2
    assert "--force-reevaluate-agents requiere --evaluate" in capsys.readouterr().err


def test_cli_main_passes_force_reevaluate_agents_and_widens_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`--force-reevaluate-agents` se traduce en `force_reevaluate_agents` y amplia
    `only_agent_ids` aunque no se haya pasado `--evaluate-agents` explicitamente.
    """
    from benchmark.run import main

    cfg = _experiment_config()
    cfg_path = tmp_path / "exp.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    captured_kwargs: dict[str, Any] = {}

    class _FakeRunner:
        """Auxiliar interno: fake runner que solo registra los kwargs de run()."""

        def __init__(self, _cfg: ExperimentConfig) -> None:
            """Ignora el config recibido."""

        def run(self, **kwargs: Any) -> BenchmarkExport:
            """Registra los kwargs y devuelve un export minimo."""
            captured_kwargs.update(kwargs)
            return BenchmarkExport(
                experiment_id="test-exp",
                experiment_path=tmp_path / "test-exp",
                manifest_path=tmp_path / "test-exp" / "manifest.json",
                dataset_summary_path=tmp_path / "test-exp" / "dataset_summary.json",
                config_path=tmp_path / "test-exp" / "config.yaml",
                runs_count=0,
                status="finalized",
            )

    monkeypatch.setattr("benchmark.run.BenchmarkRunner", _FakeRunner)

    main(["--config", str(cfg_path), "--evaluate", "--force-reevaluate-agents", "robust_v2"])

    assert captured_kwargs.get("force_reevaluate_agents") == frozenset({"robust_v2"})
    assert captured_kwargs.get("only_agent_ids") == frozenset({"robust_v2"})
    assert captured_kwargs.get("evaluation_only") is True


# --- Preflight sb-cli -----------------------------------------------------------


def test_run_aborts_before_initialize_when_sb_cli_unavailable():
    """Comprueba que run aborts before initialize when sb cli unavailable."""
    runner, calls, results, *_ = _build_runner(sb_cli_available=False)

    with pytest.raises(RuntimeError, match="sb-cli"):
        runner.run()

    assert ("results", "initialize") not in calls
    assert ("results", "mark_aborted") not in calls


def test_run_succeeds_when_sb_cli_available():
    """Comprueba que run succeeds when sb cli available."""
    runner, calls, *_ = _build_runner(sb_cli_available=True)
    export = runner.run()
    assert export.status == "finalized"
    assert ("results", "initialize") in calls


def test_run_aborts_when_swebench_api_key_missing(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que run aborts when swebench api key missing."""
    monkeypatch.delenv("SWEBENCH_API_KEY", raising=False)
    runner, calls, *_ = _build_runner(sb_cli_available=True)

    with pytest.raises(RuntimeError, match="SWEBENCH_API_KEY"):
        runner.run()

    assert ("results", "initialize") not in calls


def test_run_skips_preflight_when_evaluation_skip_is_true(tmp_path: Path):
    """Comprueba que run skips preflight when evaluation skip is true."""
    cfg = ExperimentConfig(
        dataset=DatasetConfig(slice_size=2),
        agents=[_agent()],
        benchmark=BenchmarkConfig(workers=1, functional_backend="sb_cli", evaluation_skip=True),
    )
    runner, calls, *_ = _build_runner(experiment_config=cfg, sb_cli_available=False)
    runner.run()
    assert ("results", "initialize") in calls


def test_run_skips_preflight_when_functional_backend_is_none(tmp_path: Path):
    """Comprueba que run skips preflight when functional backend is none."""
    cfg = ExperimentConfig(
        dataset=DatasetConfig(slice_size=2),
        agents=[_agent()],
        benchmark=BenchmarkConfig(workers=1, functional_backend="none"),
    )
    runner, calls, *_ = _build_runner(experiment_config=cfg, sb_cli_available=False)
    runner.run()
    assert ("results", "initialize") in calls


# --- Manejo de excepciones en cascada -------------------------------------------


def test_run_marks_aborted_when_execution_raises():
    """Comprueba que run marks aborted when execution raises."""
    boom = RuntimeError("execution crashed")
    runner, calls, results, *_ = _build_runner(execution_raises=boom)

    with pytest.raises(RuntimeError, match="execution crashed"):
        runner.run()

    assert any(c == ("results", "mark_aborted") for c in calls)
    assert ("results", "finalize") not in calls
    assert results.aborted_reason is not None
    assert "execution crashed" in results.aborted_reason


def test_run_marks_aborted_when_evaluation_raises():
    """Comprueba que run marks aborted when evaluation raises."""
    runner, calls, results, _dataset, _execution, evaluation = _build_runner()

    def boom(*args, **kwargs):  # noqa: ANN001
        """Lanza `ValueError` para simular un fallo en evaluacion."""
        raise ValueError("evaluation crashed")

    evaluation.evaluate_runs = boom  # type: ignore[assignment]

    with pytest.raises(ValueError, match="evaluation crashed"):
        runner.run()

    assert ("results", "mark_aborted") in calls
    assert ("results", "finalize") not in calls
    assert "evaluation crashed" in (results.aborted_reason or "")


def test_run_does_not_mark_aborted_when_construction_fails():
    """Comprueba que run does not mark aborted when construction fails."""
    cfg = ExperimentConfig(dataset=DatasetConfig(slice_size=1), agents=[])
    with pytest.raises(ValueError):
        BenchmarkRunner(cfg)


# --- Propagacion de experiment_id -----------------------------------------------


def test_runner_exposes_experiment_id_via_results_module():
    """Comprueba que runner exposes experiment id via results module."""
    runner, *_ = _build_runner()
    assert runner.experiment_id == "test-exp"


# --- CLI: parsing YAML y overrides ----------------------------------------------


def test_cli_load_experiment_config_from_yaml(tmp_path: Path):
    """Comprueba que cli load experiment config from yaml."""
    cfg_path = tmp_path / "exp.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "dataset": {"slice_size": 5, "slice_seed": 7},
                "agents": [
                    {
                        "agent_id": "robust_balanced",
                        "agent_class": "agent.robust_agent.RobustAgent",
                        "model_id": "gemini/gemini-3-flash",
                    }
                ],
                "benchmark": {"workers": 2},
            }
        ),
        encoding="utf-8",
    )

    cfg = _load_experiment_config(cfg_path)
    assert cfg.dataset.slice_size == 5
    assert cfg.dataset.slice_seed == 7
    assert len(cfg.agents) == 1
    assert cfg.agents[0].agent_id == "robust_balanced"
    assert cfg.benchmark.workers == 2


def test_cli_apply_cli_overrides_force_rerun_and_runs_root():
    """Comprueba que cli apply cli overrides force rerun and runs root."""
    cfg = _experiment_config()
    overridden = _apply_cli_overrides(
        cfg,
        force_rerun=True,
        experiment_id="explicit-id",
        runs_root="/tmp/runs",
        evaluation_skip=False,
        evaluate=False,
    )
    assert overridden.force_rerun is True
    assert overridden.experiment_id_explicit == "explicit-id"
    assert overridden.benchmark.runs_root == "/tmp/runs"
    assert cfg.force_rerun is False
    assert cfg.experiment_id_explicit is None


def test_cli_parser_accepts_evaluate_agents_flag():
    """Comprueba que `--evaluate-agents` se parsea junto a `--evaluate`."""
    from benchmark.run import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        [
            "--config",
            "exp.yaml",
            "--evaluate",
            "--evaluate-agents",
            "robust_strict,robust_balanced",
        ]
    )
    assert args.evaluate is True
    assert args.evaluate_agents == "robust_strict,robust_balanced"


def test_cli_main_rejects_evaluate_agents_without_evaluate(capsys: pytest.CaptureFixture[str]):
    """`--evaluate-agents` sin `--evaluate` debe fallar rapido (antes de leer el YAML)."""
    from benchmark.run import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--config", "does-not-exist.yaml", "--evaluate-agents", "robust_strict"])
    assert exc_info.value.code == 2
    assert "--evaluate-agents requiere --evaluate" in capsys.readouterr().err


def test_cli_main_passes_evaluation_only_when_evaluate_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`--evaluate` debe propagarse como `evaluation_only=True` a `runner.run(...)`.

    Regresion P18: sin esto, `--evaluate` re-ejecutaba de verdad los runs no
    terminales (p. ej. `EmptySubmission`) de todo el experimento en vez de
    limitarse a evaluar lo ya persistido en disco.
    """
    from benchmark.run import main

    cfg = _experiment_config()
    cfg_path = tmp_path / "exp.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    captured_kwargs: dict[str, Any] = {}

    class _FakeRunner:
        """Auxiliar interno: fake runner que solo registra los kwargs de run()."""

        def __init__(self, _cfg: ExperimentConfig) -> None:
            """Ignora el config recibido."""

        def run(self, **kwargs: Any) -> BenchmarkExport:
            """Registra los kwargs y devuelve un export minimo."""
            captured_kwargs.update(kwargs)
            return BenchmarkExport(
                experiment_id="test-exp",
                experiment_path=tmp_path / "test-exp",
                manifest_path=tmp_path / "test-exp" / "manifest.json",
                dataset_summary_path=tmp_path / "test-exp" / "dataset_summary.json",
                config_path=tmp_path / "test-exp" / "config.yaml",
                runs_count=0,
                status="finalized",
            )

    monkeypatch.setattr("benchmark.run.BenchmarkRunner", _FakeRunner)

    main(["--config", str(cfg_path), "--evaluate"])

    assert captured_kwargs.get("evaluation_only") is True


def test_cli_main_evaluation_only_false_without_evaluate_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sin `--evaluate`, `evaluation_only` debe ser `False` (comportamiento normal)."""
    from benchmark.run import main

    cfg = _experiment_config()
    cfg_path = tmp_path / "exp.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    captured_kwargs: dict[str, Any] = {}

    class _FakeRunner:
        """Auxiliar interno: fake runner que solo registra los kwargs de run()."""

        def __init__(self, _cfg: ExperimentConfig) -> None:
            """Ignora el config recibido."""

        def run(self, **kwargs: Any) -> BenchmarkExport:
            """Registra los kwargs y devuelve un export minimo."""
            captured_kwargs.update(kwargs)
            return BenchmarkExport(
                experiment_id="test-exp",
                experiment_path=tmp_path / "test-exp",
                manifest_path=tmp_path / "test-exp" / "manifest.json",
                dataset_summary_path=tmp_path / "test-exp" / "dataset_summary.json",
                config_path=tmp_path / "test-exp" / "config.yaml",
                runs_count=0,
                status="finalized",
            )

    monkeypatch.setattr("benchmark.run.BenchmarkRunner", _FakeRunner)

    main(["--config", str(cfg_path)])

    assert captured_kwargs.get("evaluation_only") is False


def test_cli_load_experiment_config_rejects_non_mapping(tmp_path: Path):
    """Comprueba que cli load experiment config rejects non mapping."""
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        _load_experiment_config(cfg_path)


# --- Integracion mini: `set_dataset_summary` real -------------------------------


def test_results_module_set_dataset_summary_replaces_in_memory(tmp_path: Path):
    """Comprueba que results module set dataset summary replaces in memory."""
    cfg = _experiment_config()
    module = BenchmarkResultsModule(
        experiment_config=cfg,
        runs_root=str(tmp_path / "runs"),
        experiment_id_explicit="exp-1",
    )
    module.set_dataset_summary({"num_instances_after_filter": 5, "instance_ids": ["a", "b"]})
    layout = module.initialize(matrix_size=1)
    persisted = json.loads(layout.dataset_summary_path.read_text(encoding="utf-8"))
    assert persisted["num_instances_after_filter"] == 5
