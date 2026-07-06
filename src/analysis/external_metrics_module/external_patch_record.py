"""Contrato de entrada: una linea del JSONL publicado por la fuente externa.

Formato estandar SWE-bench, usado tanto por Agentless como por AutoCodeRover
en el repositorio `SWE-bench/experiments`.

Acoplamientos:
- Producido por `JsonlLoader`.
- Consumido por `Matcher` y `ExternalMetricsModule.extract_metrics()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExternalPatchRecord:
    """Una prediccion publicada por un agente externo.

    Atributos
    ---------
    instance_id:
        Identificador de la instancia SWE-bench (p. ej.
        ``astropy__astropy-12907``).
    model_patch:
        Diff unificado generado por el agente. Cadena vacia si el agente
        no produjo parche para esta instancia.
    model_name_or_path:
        Identificador del modelo usado por la fuente externa. ``None`` si
        la linea JSONL no incluye este campo (no esperado en la practica
        para las fuentes soportadas, pero se tolera).
    """

    instance_id: str
    model_patch: str
    model_name_or_path: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalPatchRecord:
        """Construye desde el dict parseado de una linea JSONL.

        Lanza ``KeyError`` si ``instance_id`` o ``model_patch`` faltan;
        el llamador (`JsonlLoader`) captura la excepcion y descarta la linea.
        """
        return cls(
            instance_id=data["instance_id"],
            model_patch=data["model_patch"],
            model_name_or_path=data.get("model_name_or_path"),
        )
