"""API publica del Bloque 2 (benchmark).

Punto de entrada principal para el Bloque 3. Reexporta los contratos y
clases principales del paquete. Las clases internas de cada submodulo
son importables directamente pero no forman parte de la API estable.
"""

from benchmark.benchmark_runner import BenchmarkRunner
from benchmark.config import (
    AgentRunConfig,
    BenchmarkConfig,
    DatasetConfig,
    ExperimentConfig,
)
from benchmark.dataset_module import BenchmarkDatasetModule, BenchmarkInstance
from benchmark.evaluation_module import BenchmarkEvaluationModule, EvaluationResult
from benchmark.execution_module import BenchmarkExecutionModule, BenchmarkRunRecord
from benchmark.results_module import (
    BenchmarkExport,
    BenchmarkResultsModule,
    BenchmarkTaskResult,
    ExperimentLayout,
    PlannedRun,
    RunSlot,
)

__all__ = [
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
]
