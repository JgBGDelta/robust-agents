"""Contrato `UncertaintyResult` del modulo de incertidumbre."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

UncertaintyLevel = Literal["low", "medium", "high"]


@dataclass
class UncertaintyResult:
    """Resultado serializable de la incertidumbre estimada para un paso.

    El contrato fija los campos comprometidos por la spec general 8 y por
    `modulo_incertidumbre.md` 6. Los nombres de `components` son estables:
    el controlador y el Bloque 2 dependen de esta superficie.
    """

    score: float
    level: UncertaintyLevel
    components: dict[str, float]
    evidence: dict[str, Any] = field(default_factory=dict)
    cost_overhead: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Devuelve un diccionario serializable a JSON."""
        return asdict(self)
