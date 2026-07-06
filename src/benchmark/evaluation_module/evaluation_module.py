"""Fachada del modulo de evaluacion.

Expone `BenchmarkEvaluationModule` como unico punto de entrada. Delega
en `Evaluator` para la logica de evaluacion por grupo.

Acoplamientos:
- Invocado por src/benchmark/evaluation_module/__init__.py.
- Usa benchmark.config, benchmark.dataset_module.benchmark_instance, benchmark.evaluation_module.evaluation_result, benchmark.evaluation_module.evaluator, benchmark.evaluation_module.sb_cli_client, benchmark.execution_module.benchmark_run_record."""

from __future__ import annotations

from typing import Any

from benchmark.config import BenchmarkConfig, ExperimentConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.evaluation_module.evaluator import Evaluator
from benchmark.evaluation_module.sb_cli_client import SbCliClient
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord


class BenchmarkEvaluationModule:
    """Punto de entrada del modulo de evaluacion.

    Delega en `Evaluator` la evaluacion por grupo y la persistencia de
    `EvaluationResult` en disco.
    """

    def __init__(
        self,
        *,
        config: BenchmarkConfig,
        results_module: Any,
        sb_cli_client: SbCliClient | None = None,
    ) -> None:
        """Inicializa `BenchmarkEvaluationModule`."""
        self._evaluator = Evaluator(
            config=config, results_module=results_module, sb_cli_client=sb_cli_client
        )

    def evaluate_runs(
        self,
        run_records: list[BenchmarkRunRecord],
        instances: list[BenchmarkInstance],
        experiment_config: ExperimentConfig,
        *,
        force_agent_ids: frozenset[str] | None = None,
    ) -> dict[str, EvaluationResult]:
        """Evalua todos los runs y devuelve `{run_id: EvaluationResult}`."""
        return self._evaluator.evaluate_runs(
            run_records, instances, experiment_config, force_agent_ids=force_agent_ids
        )
