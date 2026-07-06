"""API publica del modulo de consolidacion.

Reexporta `ConsolidationModule`, `ConsolidationConfig`, `ConsolidatedRunRow` y
`ConsolidatedStepRow`. Los submodulos internos (`RunsScanner`, `TrajectoryReader`,
`RowBuilder`) no forman parte de la API publica pero son importables
directamente desde tests.
"""

from analysis.consolidation_module.consolidated_run_row import ConsolidatedRunRow
from analysis.consolidation_module.consolidated_step_row import ConsolidatedStepRow
from analysis.consolidation_module.consolidation_module import (
    ConsolidationConfig,
    ConsolidationModule,
)

__all__ = [
    "ConsolidationModule",
    "ConsolidationConfig",
    "ConsolidatedRunRow",
    "ConsolidatedStepRow",
]
