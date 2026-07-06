"""API publica del modulo de diagramas.

Reexporta `DiagramsModule`, `DiagramsConfig`, `AnalysisFrames`, `DiagramsReport`
y `FigureDataError`. Las funciones individuales de `figures/` no forman parte
de la API publica recomendada; se accede a ellas via `generate_figure(name, ...)`.
"""

from analysis.diagrams_module.analysis_frames import AnalysisFrames
from analysis.diagrams_module.diagrams_module import (
    DiagramsConfig,
    DiagramsModule,
    DiagramsReport,
    FIGURE_REGISTRY,
)
from analysis.diagrams_module.style import FigureDataError

__all__ = [
    "DiagramsModule",
    "DiagramsConfig",
    "AnalysisFrames",
    "DiagramsReport",
    "FigureDataError",
    "FIGURE_REGISTRY",
]
