"""Configuracion declarativa de una fuente externa de parches.

Una instancia por fuente (Agentless v1.5, AutoCodeRover v20240620, etc.).
Se pasa a `ExternalMetricsModule.run()` / `ensure_downloaded()` / `load_patches()`.

Acoplamientos:
- Consumido por `ExternalMetricsModule`, `Downloader`, `JsonlLoader`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Metadatos canonicos por `source_id` para las cuatro fuentes externas del TFG.
# Se aplican al leer `extracted_metrics.jsonl` antiguos que no incluian estos
# campos, y al ejecutar `ExternalMetricsModule.run()` si no se pasan explicitamente.
EXTERNAL_SOURCE_DEFAULTS: dict[str, dict[str, str]] = {
    "agentless_v1.5": {
        "external_release": "v1.5.0",
        "external_source_name": "Agentless",
        "results_url": (
            "https://raw.githubusercontent.com/SWE-bench/experiments/main/"
            "evaluation/lite/20241202_agentless-1.5_claude-3.5-sonnet-20241022/results/results.json"
        ),
    },
    "aider_20240523": {
        "external_release": "20240523",
        "external_source_name": "Aider",
        "results_url": (
            "https://raw.githubusercontent.com/SWE-bench/experiments/main/"
            "evaluation/lite/20240523_aider/results/results.json"
        ),
    },
    "moatless_claude35_20241117": {
        "external_release": "20241117",
        "external_source_name": "Moatless Tools",
        "results_url": (
            "https://raw.githubusercontent.com/SWE-bench/experiments/main/"
            "evaluation/lite/20241117_moatless_claude-3.5-sonnet-20241022/results/results.json"
        ),
    },
    "openhands_claude35_20240725": {
        "external_release": "20240725",
        "external_source_name": "OpenHands",
        "results_url": (
            "https://raw.githubusercontent.com/SWE-bench/experiments/main/"
            "evaluation/lite/20240725_opendevin_codeact_v1.8_claude35sonnet/results/results.json"
        ),
    },
}


def default_metadata_for_source(source_id: str) -> dict[str, str | None]:
    """Devuelve metadatos canonicos (release, nombre legible, URL de resultados)."""
    entry = EXTERNAL_SOURCE_DEFAULTS.get(source_id, {})
    return {
        "external_release": entry.get("external_release"),
        "external_source_name": entry.get("external_source_name"),
        "results_url": entry.get("results_url"),
    }


@dataclass(frozen=True)
class ExternalSourceConfig:
    """Describe como localizar y parsear el release de un agente externo.

    Atributos
    ---------
    source_id:
        Identificador estable de la fuente (p. ej. ``agentless_v1.5``,
        ``autocoderover_v20240620``). Se usa como ``agent_id`` en el CSV
        consolidado y como nombre de directorio bajo ``local_cache_dir``.
    release_url:
        URL publica del archivo de predicciones. Puede apuntar a un ZIP
        (Agentless) o directamente a un JSONL (AutoCodeRover via raw GitHub).
    archive_member:
        Nombre del fichero JSONL *dentro* del ZIP, si ``release_url`` apunta
        a un ZIP. ``None`` si la URL ya es un JSONL directo. Si el ZIP
        contiene un unico ``.jsonl`` y ``archive_member`` es ``None``, el
        ``Downloader`` lo detecta automaticamente.
    local_cache_dir:
        Directorio de destino para la descarga y el JSONL extraido.
        Tipicamente ``data/external_predictions/<source_id>/``.
    default_agent_id:
        Identificador a asignar en ``ExternalMetricsRecord.agent_id``.
        Por convencion coincide con ``source_id``.
    """

    source_id: str
    release_url: str
    archive_member: str | None
    local_cache_dir: Path
    default_agent_id: str
    external_release: str | None = None
    """Version del release (p. ej. ``v1.5.0``, ``20240523``). Se propaga a
    ``ExternalMetricsRecord`` y desde ahi a ``datos_consolidados.csv``."""
    external_source_name: str | None = None
    """Nombre legible del sistema externo (p. ej. ``Agentless``, ``Aider``).
    Se mapea a la columna ``external_source`` del CSV consolidado."""
    results_url: str | None = None
    """URL de ``results/results.json`` en SWE-bench/experiments con veredictos publicados."""
