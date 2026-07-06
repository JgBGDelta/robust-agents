"""API publica del modulo de evaluacion.

Reexporta `BenchmarkEvaluationModule` y `EvaluationResult` (su contrato
de salida). Los submodulos internos (`Evaluator`, extractores) no forman
parte de la API publica del paquete pero son importables directamente
desde tests.
"""

from benchmark.evaluation_module.evaluation_module import BenchmarkEvaluationModule
from benchmark.evaluation_module.evaluation_result import EvaluationResult

__all__ = ["BenchmarkEvaluationModule", "EvaluationResult"]
