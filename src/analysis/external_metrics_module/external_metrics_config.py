"""Configuracion operativa del modulo de metricas externas.

Se inyecta en la construccion de `ExternalMetricsModule`. Sera compuesta
en `AnalysisConfig` cuando los demas modulos de analisis esten implementados.

Acoplamientos:
- Consumido por `ExternalMetricsModule`, `Downloader`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExternalMetricsConfig:
    """Parametros operativos del `ExternalMetricsModule`.

    Atributos
    ---------
    base_cache_dir:
        Directorio raiz para las descargas. Los subdirectorios por fuente
        (``<source_id>/``) se crean automaticamente si no existen.
    max_retries:
        Numero maximo de intentos de descarga antes de abortar (incluyendo
        el primero). Con ``max_retries=3`` se hacen hasta 3 intentos con
        backoff exponencial (2, 4 segundos entre intentos).
    max_download_bytes:
        Tamano maximo aceptado para un archivo descargado en bytes.
        Proteccion ante releases inesperadamente grandes que podrian
        saturar el disco. Por defecto 500 MB.
    """

    base_cache_dir: Path = field(default_factory=lambda: Path("data/external_predictions"))
    max_retries: int = 3
    max_download_bytes: int = 500 * 1024 * 1024  # 500 MB
