"""Contrato `BenchmarkInstance` del modulo de dataset.

Unidad normalizada de tarea producida por `BenchmarkDatasetModule`.
EB.7.1 y `modulo_dataset.md` MD.10.

Es el **unico contrato** que cruza desde el modulo de dataset hacia los
modulos de ejecucion y evaluacion. Estable a JSON via `to_dict()`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BenchmarkInstance:
    """Tarea normalizada de SWE-bench lista para ejecucion y evaluacion."""

    instance_id: str
    dataset_name: str
    subset: str
    split: str
    repo: str | None
    base_commit: str | None
    problem_statement: str
    gold_patch: str | None = None
    test_patch: str | None = None
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Devuelve un diccionario serializable a JSON."""
        return asdict(self)
