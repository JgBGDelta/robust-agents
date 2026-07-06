"""Contrato `StructuralRiskResult` del modulo de metricas estructurales."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


RiskLevel = Literal["low", "medium", "high"]


@dataclass
class StructuralRiskResult:
    """Resultado serializable del riesgo estructural de un paso."""

    score: float
    level: RiskLevel
    metrics: dict[str, float | int]
    diff_summary: dict[str, Any]
    cost_overhead: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Devuelve un diccionario serializable a JSON."""
        return asdict(self)
