"""API publica del modulo de metricas de agentes externos.

Reexporta `ExternalMetricsModule`, `ExternalSourceConfig`,
`ExternalMetricsConfig`, `ExternalPatchRecord` y `ExternalMetricsRecord`.
Los submodulos internos (`Downloader`, `JsonlLoader`, `Matcher`) no forman
parte de la API publica pero son importables directamente desde tests.
"""

from analysis.external_metrics_module.external_metrics_config import ExternalMetricsConfig
from analysis.external_metrics_module.external_metrics_module import ExternalMetricsModule
from analysis.external_metrics_module.external_metrics_record import ExternalMetricsRecord
from analysis.external_metrics_module.external_patch_record import ExternalPatchRecord
from analysis.external_metrics_module.external_source_config import ExternalSourceConfig

__all__ = [
    "ExternalMetricsModule",
    "ExternalSourceConfig",
    "ExternalMetricsConfig",
    "ExternalPatchRecord",
    "ExternalMetricsRecord",
]
