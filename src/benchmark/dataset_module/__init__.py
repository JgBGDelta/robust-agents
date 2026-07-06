"""API publica del modulo de dataset.

Reexporta `BenchmarkDatasetModule` y `BenchmarkInstance` (su contrato
de salida). La normalizacion y validacion estructural se realizan en
`InstanceNormalizer`.
"""

from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.dataset_module.dataset_module import BenchmarkDatasetModule

__all__ = ["BenchmarkDatasetModule", "BenchmarkInstance"]
