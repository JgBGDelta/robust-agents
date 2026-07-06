"""API publica del modulo de ejecucion del benchmark.

Reexporta `BenchmarkExecutionModule` y `BenchmarkRunRecord` (su
contrato de salida).
"""

from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.execution_module.execution_module import BenchmarkExecutionModule

__all__ = ["BenchmarkExecutionModule", "BenchmarkRunRecord"]
