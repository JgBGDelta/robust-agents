"""Contrato de salida: una fila por instancia del experimento para un agente externo.

Serializable a JSONL para ser consumido por `ConsolidationModule`.

Acoplamientos:
- Producido por `ExternalMetricsModule.extract_metrics()`.
- Consumido por `ConsolidationModule` a traves de `extracted_metrics.jsonl`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from analysis.external_metrics_module.external_source_config import default_metadata_for_source


@dataclass
class ExternalMetricsRecord:
    """Metricas de un agente externo para una instancia del experimento.

    Una fila existe por cada ``instance_id`` de la lista del experimento,
    independientemente de si la fuente externa publico parche para esa
    instancia (``matched`` indica si hay parche).

    Atributos
    ---------
    instance_id:
        Identificador de la instancia SWE-bench.
    source_id:
        Identificador de la fuente externa (p. ej. ``agentless_v1.5``).
    agent_id:
        Identificador a usar en el CSV consolidado. Por convencion coincide
        con ``source_id``.
    model_id:
        Modelo usado por la fuente (de ``model_name_or_path``). ``None`` si
        no estaba en el JSONL de origen o si ``matched=False``.
    model_patch:
        Diff del parche externo. ``None`` si ``matched=False``.
    matched:
        ``True`` si la fuente publico un parche para esta instancia.
        ``False`` en el caso (no esperado) de que no haya contraparte.
    patch_metrics:
        Salida literal de ``PatchMetricsExtractor.extract()``. Misma
        estructura que en ``evaluation_result.json.extended.patch_metrics``
        del Bloque 2, garantizando comparabilidad directa.
    resolved:
        Veredicto funcional. ``True``/``False`` si se obtuvo de resultados
        publicados en SWE-bench/experiments (``resolved_source='published'``)
        o de reevaluacion local. ``None`` si la instancia no tiene parche
        emparejado (``matched=False``).
    resolved_source:
        Trazabilidad de como se obtuvo ``resolved``: ``None``, ``published``
        (leido del release oficial) o ``sb_cli_reeval`` (reevaluado localmente).
    """

    instance_id: str
    source_id: str
    agent_id: str
    model_id: str | None
    model_patch: str | None
    matched: bool
    patch_metrics: dict[str, Any]
    resolved: bool | None
    resolved_source: str | None
    external_release: str | None = None
    """Version del release externo (p. ej. ``v1.5.0``). Propagado desde
    ``ExternalSourceConfig.external_release`` por ``ExternalMetricsModule.run()``."""
    external_source_name: str | None = None
    """Nombre legible del sistema externo (p. ej. ``Agentless``). Se mapea a
    ``datos_consolidados.csv::external_source``."""

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable a JSON (compatible con JSONL)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalMetricsRecord:
        """Construye desde un diccionario (por ejemplo, al leer el JSONL de salida)."""
        record = cls(
            instance_id=data["instance_id"],
            source_id=data["source_id"],
            agent_id=data["agent_id"],
            model_id=data.get("model_id"),
            model_patch=data.get("model_patch"),
            matched=data["matched"],
            patch_metrics=data["patch_metrics"],
            resolved=data.get("resolved"),
            resolved_source=data.get("resolved_source"),
            external_release=data.get("external_release"),
            external_source_name=data.get("external_source_name"),
        )
        return cls.with_source_defaults(record)

    @classmethod
    def with_source_defaults(cls, record: ExternalMetricsRecord) -> ExternalMetricsRecord:
        """Rellena ``external_release``/``external_source_name`` si faltan en el registro."""
        defaults = default_metadata_for_source(record.source_id)
        release = record.external_release or defaults.get("external_release")
        source_name = record.external_source_name or defaults.get("external_source_name")
        if release == record.external_release and source_name == record.external_source_name:
            return record
        return replace(
            record,
            external_release=release,
            external_source_name=source_name,
        )

    @classmethod
    def load_jsonl(cls, path: Path) -> list[ExternalMetricsRecord]:
        """Lee todos los registros de un fichero JSONL producido por `write_output`."""
        records: list[ExternalMetricsRecord] = []
        cache_dir = path.parent
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                record = cls.from_dict(json.loads(line))
                records.append(_backfill_published_resolved(record, cache_dir))
        return records


def _backfill_published_resolved(record: ExternalMetricsRecord, cache_dir: Path) -> ExternalMetricsRecord:
    """Rellena ``resolved`` desde ``published_results.json`` en cache si falta."""
    if record.resolved is not None or not record.matched:
        return record
    published_path = cache_dir / "published_results.json"
    if not published_path.exists():
        return record
    payload = json.loads(published_path.read_text(encoding="utf-8"))
    resolved_ids = set(payload.get("resolved") or [])
    return replace(
        record,
        resolved=record.instance_id in resolved_ids,
        resolved_source="published",
    )
