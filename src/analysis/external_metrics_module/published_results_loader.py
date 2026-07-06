"""Carga veredictos ``resolved`` publicados en SWE-bench/experiments.

Los JSONL de predicciones no incluyen ``resolved`` por instancia, pero cada
submission en ``evaluation/lite/<run_id>/results/results.json`` lista las
instancias resueltas del harness oficial.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def load_published_resolved_ids(results_url: str, cache_path: Path | None = None) -> set[str]:
    """Devuelve el conjunto de ``instance_id`` resueltos segun ``results.json`` publicado."""
    if cache_path is not None and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        logger.info("Descargando resultados publicados: %s", results_url)
        with urllib.request.urlopen(results_url, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload), encoding="utf-8")

    resolved = payload.get("resolved")
    if not isinstance(resolved, list):
        raise ValueError(f"'resolved' no es una lista en {results_url}")
    return set(resolved)
