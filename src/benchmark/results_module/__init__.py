"""API publica del modulo de resultados del benchmark.

Reexporta `BenchmarkResultsModule`, `BenchmarkExport`, `BenchmarkTaskResult`
y `ExperimentLayout` (contratos de salida). La logica interna
(`ResumeDetector`, `ArtifactStore`, funciones de `layout`) no forma parte
de la API publica pero es importable directamente desde tests.
"""

from benchmark.results_module.contracts import (
    BenchmarkExport,
    BenchmarkTaskResult,
    PlannedRun,
    RunSlot,
)
from benchmark.results_module.layout import ExperimentLayout
from benchmark.results_module.results_module import BenchmarkResultsModule

__all__ = [
    "BenchmarkResultsModule",
    "BenchmarkExport",
    "BenchmarkTaskResult",
    "ExperimentLayout",
    "PlannedRun",
    "RunSlot",
]
