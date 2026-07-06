"""Contrato `StepTrace`.

Unidad de persistencia por paso del agente robusto. Agrega los tres
contratos producidos por los modulos del bloque (`UncertaintyResult`,
`StructuralRiskResult`, `ControllerDecision`) junto al resultado de
validacion adicional, snapshot de presupuesto, refs git y errores no
fatales. Vease spec general 8.1.

Los huecos de los contratos producidos por los modulos se tipan como
`dict[str, Any]`, es decir, se almacenan ya en su forma serializada
(via `to_dict()` del contrato correspondiente). De este modo el
`StepTrace` se mantiene plano y estable a JSON sin acoplar el agregado
a las firmas concretas de los contratos productores.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StepTrace:
    """Traza serializable de un paso del agente robusto."""

    step_id: int
    timestamp: float
    uncertainty: dict[str, Any]
    structural_risk: dict[str, Any]
    budget_state: dict[str, Any]
    git_refs: dict[str, Any]
    decision: dict[str, Any] | None = None
    validation_outcome: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Devuelve la traza como dict serializable a JSON."""
        return asdict(self)
