"""Contrato `BenchmarkRunRecord` del modulo de ejecucion.

Registro bruto producido por `BenchmarkExecutionModule.run_one`. Spec
`EB.7.3` y extensiones de `modulo_ejecucion.md` (`ContaminationDetector`):

- Al contrato base se anaden los campos `contamination_detected` y
  `contamination_evidence` para el resultado del `ContaminationDetector`
  (auditoria post-run de `forbidden_test_ids`).

El `agent_run_config` se almacena como objeto pydantic (`AgentRunConfig`).
La serializacion JSON se delega a `to_dict()`, que invoca
`model_dump(mode="json")` sobre el subobjeto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from benchmark.config import AgentRunConfig

RunStatus = Literal["completed", "failed", "precondition_failed"]
"""Estados terminales de un run. EB.7.3."""


@dataclass
class BenchmarkRunRecord:
    """Registro bruto de un run del benchmark."""

    run_id: str
    instance_id: str
    agent_run_config: AgentRunConfig
    status: RunStatus
    started_at: float
    ended_at: float
    duration_seconds: float
    exit_status: str | None = None
    trajectory_path: str | None = None
    model_patch_path: str | None = None
    preds_entry: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    environment: dict[str, Any] = field(default_factory=dict)
    contamination_detected: bool = False
    """Resultado del `ContaminationDetector`. Ver `modulo_ejecucion.md`."""
    contamination_evidence: dict[str, Any] | None = None
    """Detalle de coincidencias cuando `contamination_detected == True`."""
    had_rate_limit_retries: bool = False
    """True si el run tuvo al menos un reintento por RateLimitError (429).
    Usado por el bucle de ejecucion secuencial para ajustar dinamicamente
    el delay entre runs. No se serializa al JSON de resultados."""
    cost_usd: float = 0.0
    """Coste en USD del run segun model_stats.instance_cost de la trayectoria."""

    def to_dict(self) -> dict[str, Any]:
        """Devuelve un diccionario serializable a JSON."""
        return {
            "run_id": self.run_id,
            "instance_id": self.instance_id,
            "agent_run_config": self.agent_run_config.model_dump(mode="json"),
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "exit_status": self.exit_status,
            "trajectory_path": self.trajectory_path,
            "model_patch_path": self.model_patch_path,
            "preds_entry": (None if self.preds_entry is None else dict(self.preds_entry)),
            "error": (None if self.error is None else dict(self.error)),
            "environment": dict(self.environment),
            "contamination_detected": self.contamination_detected,
            "contamination_evidence": (
                None
                if self.contamination_evidence is None
                else dict(self.contamination_evidence)
            ),
            "cost_usd": self.cost_usd,
        }
