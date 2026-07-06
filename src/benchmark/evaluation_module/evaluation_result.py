"""Contrato `EvaluationResult` — un unico JSON de evaluacion por run.

Serializa los bloques `functional` (veredicto sb-cli) y `extended`
(`run_metrics`, `patch_metrics`, disponibilidad y errores). El Bloque 3
lee `functional.resolved` y las metricas en `extended`.

Esquema documentado en:
`docs/especificaciones/benchmark/ejemplo_evaluation_result.md`
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvaluationResult:
    """Evaluacion completa (funcional + ampliada) de un run."""

    run_id: str
    instance_id: str
    agent_id: str
    trace_format: str | None
    functional: dict[str, Any] = field(default_factory=dict)
    """Veredicto sb-cli: `resolved`, `status`, `evaluation_backend`, `report_path`, `evidence`."""
    extended: dict[str, Any] = field(default_factory=dict)
    """`run_metrics`, `patch_metrics`, `availability`, `errors`."""

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable a JSON."""
        return {
            "run_id": self.run_id,
            "instance_id": self.instance_id,
            "agent_id": self.agent_id,
            "trace_format": self.trace_format,
            "functional": dict(self.functional),
            "extended": dict(self.extended),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationResult:
        """Reconstruye desde un diccionario persistido."""
        return cls(
            run_id=data.get("run_id", ""),
            instance_id=data.get("instance_id", ""),
            agent_id=data.get("agent_id", ""),
            trace_format=data.get("trace_format"),
            functional=dict(data.get("functional") or {}),
            extended=dict(data.get("extended") or {}),
        )

    @classmethod
    def try_load(cls, path: Path) -> EvaluationResult | None:
        """Carga desde disco si existe y es valido; devuelve `None` si no."""
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        # Validacion minima: debe tener los bloques del nuevo contrato.
        if "functional" not in data or "extended" not in data:
            return None
        return cls.from_dict(data)
