"""Layout del experimento en disco.

Centraliza rutas estandar bajo `runs/<experiment_id>/` y funciones
auxiliares de construccion de segmentos.

Layout objetivo:
    runs/<experiment_id>/
      manifest.json, config.yaml, dataset_summary.json
      groups/<agent_id>__<model_id>/preds.json, functional_report/
      instances/<instance_id>/<agent_id>/<run_id>/
        trajectory.traj.json, model.patch, run_record.json, evaluation_result.json
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def path_safe(value: str) -> str:
    """Reemplaza caracteres invalidos de path por guion."""
    for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        value = value.replace(ch, "-")
    return value


def group_id(*, agent_id: str, model_id: str) -> str:
    """Construye el identificador de grupo `<agent_id>__<model_id>`."""
    return f"{agent_id}__{path_safe(model_id)}"


def run_directory(
    layout: ExperimentLayout,
    *,
    instance_id: str,
    agent_id: str,
    run_id: str,
) -> Path:
    """Ruta absoluta del directorio de un run (sin segmento de perfil)."""
    return layout.instances_dir / instance_id / agent_id / run_id


@dataclass
class ExperimentLayout:
    """Conjunto de rutas estandar de un experimento."""

    experiment_id: str
    root: Path
    manifest_path: Path
    config_path: Path
    dataset_summary_path: Path
    instances_dir: Path
    groups_dir: Path

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable con rutas POSIX."""
        data = asdict(self)
        return {
            key: (value.as_posix() if isinstance(value, Path) else value)
            for key, value in data.items()
        }
