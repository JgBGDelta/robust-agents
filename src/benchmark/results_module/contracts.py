"""Contratos compartidos del modulo de resultados.

`PlannedRun` — celda de la lista de runs antes de filtrar reanudaciones.
`RunSlot`    — run pendiente con ruta de persistencia.
`BenchmarkTaskResult` — vista unificada por run (instancia + config + record + evaluacion).
`BenchmarkExport`     — salida compacta del Bloque 2 hacia el Bloque 3.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from benchmark.config import AgentRunConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord

ExportStatus = Literal["finalized", "aborted"]


@dataclass
class PlannedRun:
    """Celda de la lista experimental antes de filtrar reanudaciones."""

    instance_id: str
    agent_run_config: AgentRunConfig
    run_id: str


@dataclass
class RunSlot:
    """Run pendiente de ejecucion con ruta de persistencia."""

    instance_id: str
    agent_run_config: AgentRunConfig
    run_id: str
    run_dir: Path


@dataclass
class BenchmarkTaskResult:
    """Vista unificada por run: instancia + config + record + evaluacion."""

    run_id: str
    instance: BenchmarkInstance
    agent_run_config: AgentRunConfig
    run_record: BenchmarkRunRecord
    evaluation: EvaluationResult

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable a JSON."""
        return {
            "run_id": self.run_id,
            "instance": self.instance.to_dict(),
            "agent_run_config": self.agent_run_config.model_dump(mode="json"),
            "run_record": self.run_record.to_dict(),
            "evaluation": self.evaluation.to_dict(),
        }


@dataclass
class BenchmarkExport:
    """Vista compacta del experimento finalizado."""

    experiment_id: str
    experiment_path: Path
    manifest_path: Path
    dataset_summary_path: Path
    config_path: Path
    runs_count: int
    status: ExportStatus

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable con rutas POSIX."""
        data = asdict(self)
        return {
            key: (value.as_posix() if isinstance(value, Path) else value)
            for key, value in data.items()
        }
