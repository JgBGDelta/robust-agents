"""Contrato de salida: una fila de `datos_pasos_consolidados.csv` por paso.

Esquema cerrado en `especificacion_analisis.md` S7.
Solo existe para runs propios con traza `robust_agent` (`source_type == "own"` y
`trace_format == "robust-agent-1.0"`); no hay filas de pasos para `default` ni
para agentes externos.

Acoplamientos:
- Producido por `TrajectoryReader.build_step_rows()` / `RowBuilder`.
- Consumido por `ConsolidationModule.write_csv()` y, via CSV, por `DiagramsModule`
  (mapa incertidumbre x riesgo x accion).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class ConsolidatedStepRow:
    """Fila de `datos_pasos_consolidados.csv` (un paso de un run `robust_agent`)."""

    run_id: str
    instance_id: str
    configuration_id: str
    step_index: int
    uncertainty_score: float | None
    uncertainty_level: str | None
    structural_risk_score: float | None
    structural_risk_level: str | None
    controller_action: str | None

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable a CSV (orden estable de columnas)."""
        return asdict(self)

    @classmethod
    def fieldnames(cls) -> list[str]:
        """Nombres de columna en el orden declarado (para `csv.DictWriter`)."""
        return [f.name for f in fields(cls)]
