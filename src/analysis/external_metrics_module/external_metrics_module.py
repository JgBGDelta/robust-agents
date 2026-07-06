"""Modulo de metricas de agentes externos (`ExternalMetricsModule`).

Incorpora al analisis comparativo los resultados de agentes externos sin
ejecutarlos: descarga parches publicados, los empareja con las instancias
del experimento y extrae las mismas metricas de parche que se usan para
los runs propios.

Es el primer modulo a desarrollar del Bloque 3 porque es independiente del
resto (no depende del arbol `runs/` del Bloque 2 ni de pandas/matplotlib)
y no consume tokens de LLM.

Acoplamientos:
- Usa `Downloader`, `JsonlLoader`, `Matcher` (submodulos internos).
- Usa `PatchMetricsExtractor` de `benchmark.evaluation_module.extractors`
  (unica dependencia de codigo del Bloque 2, utilidad pura sin estado).
- No importa nada de `src/agent`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from benchmark.evaluation_module.extractors.patch_metrics import PatchMetricsExtractor

from analysis.external_metrics_module.downloader import Downloader
from analysis.external_metrics_module.external_metrics_config import ExternalMetricsConfig
from analysis.external_metrics_module.external_metrics_record import ExternalMetricsRecord
from analysis.external_metrics_module.external_patch_record import ExternalPatchRecord
from analysis.external_metrics_module.external_source_config import (
    ExternalSourceConfig,
    default_metadata_for_source,
)
from analysis.external_metrics_module.jsonl_loader import JsonlLoader
from analysis.external_metrics_module.matcher import Matcher
from analysis.external_metrics_module.published_results_loader import load_published_resolved_ids

logger = logging.getLogger(__name__)

# Nombre del fichero de salida dentro de `local_cache_dir`.
_OUTPUT_FILENAME = "extracted_metrics.jsonl"


class ExternalMetricsModule:
    """Extrae metricas de parche de agentes externos precomputados.

    Uso tipico (fachada end-to-end)::

        config = ExternalMetricsConfig()
        source = ExternalSourceConfig(
            source_id="agentless_v1.5",
            release_url="https://github.com/.../agentless_swebench_lite.zip",
            archive_member=None,
            local_cache_dir=Path("data/external_predictions/agentless_v1.5"),
            default_agent_id="agentless_v1.5",
        )
        module = ExternalMetricsModule(config)
        records = module.run(source, instance_ids=my_ids, gold_patch_lookup=my_lookup)

    Uso granular (para tests o pipelines parciales)::

        jsonl_path = module.ensure_downloaded(source)
        patches    = module.load_patches(source)
        records    = module.extract_metrics(patches, instance_ids, gold_patch_lookup)
        out_path   = module.write_output(records, source)
    """

    def __init__(self, config: ExternalMetricsConfig | None = None) -> None:
        """Inicializa submodulos internos. `config=None` usa los defaults de `ExternalMetricsConfig`."""
        self._config = config or ExternalMetricsConfig()
        self._downloader = Downloader(self._config)
        self._loader = JsonlLoader()
        self._matcher = Matcher()

    # ------------------------------------------------------------------
    # Interfaz publica
    # ------------------------------------------------------------------

    def ensure_downloaded(self, source_config: ExternalSourceConfig) -> Path:
        """Garantiza que el JSONL de la fuente esta en cache local. Idempotente.

        Lanza `RuntimeError` si la descarga falla tras agotar todos los reintentos.
        """
        return self._downloader.ensure_jsonl(source_config)

    def load_patches(self, source_config: ExternalSourceConfig) -> list[ExternalPatchRecord]:
        """Descarga (si no esta en cache) y parsea el JSONL de predicciones."""
        jsonl_path = self.ensure_downloaded(source_config)
        return self._loader.load(jsonl_path)

    def extract_metrics(
        self,
        patches: list[ExternalPatchRecord],
        instance_ids: list[str],
        gold_patch_lookup: Callable[[str], str | None],
        *,
        published_resolved_ids: set[str] | None = None,
    ) -> list[ExternalMetricsRecord]:
        """Empareja parches con instancias y calcula metricas comparables.

        Devuelve exactamente `len(instance_ids)` registros: uno por instancia,
        con `matched=False` y metricas vacias si la fuente no publico parche
        para esa instancia.

        La metrica de parche se calcula con `PatchMetricsExtractor.extract()`
        —la misma formula que usa el Bloque 2— para garantizar comparabilidad
        directa en el CSV consolidado. `gold_patch_lookup` es una funcion
        `instance_id -> gold_patch | None`; en integracion real se obtiene de
        HuggingFace, en tests se inyecta un stub.
        """
        match_map = self._matcher.match(patches, instance_ids)
        records: list[ExternalMetricsRecord] = []

        for iid in instance_ids:
            patch_record = match_map[iid]

            if patch_record is None:
                # Instancia sin contraparte: fila de degradacion limpia.
                records.append(
                    ExternalMetricsRecord(
                        instance_id=iid,
                        source_id="",  # Se rellena en el llamador si procede.
                        agent_id="",
                        model_id=None,
                        model_patch=None,
                        matched=False,
                        patch_metrics=PatchMetricsExtractor.extract(
                            model_patch=None, gold_patch=None
                        ),
                        resolved=None,
                        resolved_source=None,
                    )
                )
                continue

            gold_patch = gold_patch_lookup(iid)
            patch_metrics = PatchMetricsExtractor.extract(
                model_patch=patch_record.model_patch or None,
                gold_patch=gold_patch,
            )
            resolved, resolved_source = _resolve_from_publication(iid, published_resolved_ids)

            records.append(
                ExternalMetricsRecord(
                    instance_id=iid,
                    source_id="",  # Rellena `run()` con source_config.source_id.
                    agent_id="",   # Rellena `run()` con source_config.default_agent_id.
                    model_id=patch_record.model_name_or_path,
                    model_patch=patch_record.model_patch,
                    matched=True,
                    patch_metrics=patch_metrics,
                    resolved=resolved,
                    resolved_source=resolved_source,
                )
            )

        return records

    def run(
        self,
        source_config: ExternalSourceConfig,
        instance_ids: list[str],
        gold_patch_lookup: Callable[[str], str | None],
    ) -> list[ExternalMetricsRecord]:
        """Fachada end-to-end: descarga -> parseo -> emparejamiento -> escritura.

        Encadena `ensure_downloaded` -> `load_patches` -> `extract_metrics`
        -> `write_output` y devuelve los registros producidos. Es el punto de
        entrada recomendado para `AnalysisRunner`, CLI o notebook.
        """
        logger.info("[%s] Iniciando pipeline de metricas externas.", source_config.source_id)

        patches = self.load_patches(source_config)
        published_resolved_ids = _load_published_resolved(source_config)
        records = self.extract_metrics(
            patches,
            instance_ids,
            gold_patch_lookup,
            published_resolved_ids=published_resolved_ids,
        )

        # Rellenar source_id y agent_id (extract_metrics los deja en blanco
        # para no acoplar esa logica al contrato de datos).
        records = _fill_source_fields(records, source_config)

        self.write_output(records, source_config)

        matched = sum(1 for r in records if r.matched)
        logger.info(
            "[%s] Pipeline completado: %d/%d instancias con match.",
            source_config.source_id,
            matched,
            len(records),
        )
        return records

    def write_output(
        self,
        records: list[ExternalMetricsRecord],
        source_config: ExternalSourceConfig,
    ) -> Path:
        """Serializa `records` a JSONL en `local_cache_dir/extracted_metrics.jsonl`.

        Sobrescribe el fichero si ya existe (el pipeline es idempotente dado
        el mismo estado de los parches descargados). Devuelve la ruta al fichero escrito.
        """
        source_config.local_cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = source_config.local_cache_dir / _OUTPUT_FILENAME

        lines = [
            json.dumps(r.to_dict(), ensure_ascii=False) for r in records
        ]
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        logger.info(
            "[%s] %d registros escritos en %s",
            source_config.source_id,
            len(records),
            out_path,
        )
        return out_path


def _resolve_from_publication(
    instance_id: str, published_resolved_ids: set[str] | None
) -> tuple[bool | None, str | None]:
    """Deriva ``resolved`` desde resultados publicados si estan disponibles."""
    if published_resolved_ids is None:
        return None, None
    return instance_id in published_resolved_ids, "published"


def _load_published_resolved(source_config: ExternalSourceConfig) -> set[str] | None:
    """Carga ``results.json`` publicado para la fuente, si hay URL configurada."""
    defaults = default_metadata_for_source(source_config.source_id)
    results_url = source_config.results_url or defaults.get("results_url")
    if not results_url:
        return None
    cache_path = source_config.local_cache_dir / "published_results.json"
    return load_published_resolved_ids(results_url, cache_path=cache_path)


# ------------------------------------------------------------------
# Funciones privadas de modulo
# ------------------------------------------------------------------


def _fill_source_fields(
    records: list[ExternalMetricsRecord],
    source_config: ExternalSourceConfig,
) -> list[ExternalMetricsRecord]:
    """Rellena ``source_id`` y ``agent_id`` en registros producidos por ``extract_metrics``.

    ``extract_metrics`` deja estos campos vacios para mantenerse desacoplado
    de ``ExternalSourceConfig``; ``run()`` los rellena aqui antes de persistir.
    """
    filled: list[ExternalMetricsRecord] = []
    defaults = default_metadata_for_source(source_config.source_id)
    for r in records:
        filled.append(
            ExternalMetricsRecord(
                instance_id=r.instance_id,
                source_id=source_config.source_id,
                agent_id=source_config.default_agent_id,
                model_id=r.model_id,
                model_patch=r.model_patch,
                matched=r.matched,
                patch_metrics=r.patch_metrics,
                resolved=r.resolved,
                resolved_source=r.resolved_source,
                external_release=source_config.external_release or defaults.get("external_release"),
                external_source_name=source_config.external_source_name
                or defaults.get("external_source_name"),
            )
        )
    return filled
