"""Modulo de diagramas (`DiagramsModule`).

Lee exclusivamente `datos_consolidados.csv`/`datos_pasos_consolidados.csv`
(nunca el arbol `runs/` ni las trazas) y genera con `matplotlib` el catalogo
completo de figuras `.png` de `especificacion_analisis.md` S8. Ver
`docs/especificaciones/analisis/modulo_diagramas.md`.

`FIGURE_REGISTRY` es el unico punto de alta de una figura nueva: anadir un
diagrama al catalogo no requiere tocar `generate_all`/`generate_minimal`/
`generate_additional`, solo escribir la funcion en `figures/` y registrar su
nombre aqui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from analysis.diagrams_module.analysis_frames import AnalysisFrames
from analysis.diagrams_module.style import FigureDataError

from analysis.diagrams_module.figures.ablations import plot_ablations
from analysis.diagrams_module.figures.baseline_vs_robust import plot_baseline_vs_robust
from analysis.diagrams_module.figures.controller_interventions import plot_controller_interventions
from analysis.diagrams_module.figures.cost_effort import plot_cost_effort
from analysis.diagrams_module.figures.cost_performance_tradeoff import plot_cost_performance_tradeoff
from analysis.diagrams_module.figures.external_agents import plot_external_agents
from analysis.diagrams_module.figures.final_states import plot_final_states
from analysis.diagrams_module.figures.patch_structure import plot_patch_structure
from analysis.diagrams_module.figures.patch_structure_resolved import plot_patch_structure_resolved
from analysis.diagrams_module.figures.resolution_rate import plot_resolution_rate
from analysis.diagrams_module.figures.uncertainty_outcome import plot_uncertainty_outcome

from analysis.diagrams_module.figures.additional.cost_per_solved import plot_cost_per_solved
from analysis.diagrams_module.figures.additional.cumulative_resolution_vs_cost import (
    plot_cumulative_resolution_vs_cost,
)
from analysis.diagrams_module.figures.additional.results_by_repository import plot_results_by_repository
from analysis.diagrams_module.figures.additional.termination_reasons import plot_termination_reasons
from analysis.diagrams_module.figures.additional.uncertainty_risk_action_map import (
    plot_uncertainty_risk_action_map,
)

logger = logging.getLogger(__name__)

_FigureFunc = Callable[[AnalysisFrames, Path], Path]

FIGURE_REGISTRY: dict[str, tuple[_FigureFunc, str]] = {
    # -- Catalogo minimo (S8.1) --
    "resolution_rate": (plot_resolution_rate, "minimal"),
    "final_states": (plot_final_states, "minimal"),
    "cost_effort": (plot_cost_effort, "minimal"),
    "patch_structure": (plot_patch_structure, "minimal"),
    "patch_structure_resolved": (plot_patch_structure_resolved, "minimal"),
    "uncertainty_outcome": (plot_uncertainty_outcome, "minimal"),
    "controller_interventions": (plot_controller_interventions, "minimal"),
    "baseline_vs_robust": (plot_baseline_vs_robust, "minimal"),
    "ablations": (plot_ablations, "minimal"),
    "external_agents": (plot_external_agents, "minimal"),
    "cost_performance_tradeoff": (plot_cost_performance_tradeoff, "minimal"),
    # -- Catalogo adicional (S8.2) --
    "uncertainty_risk_action_map": (plot_uncertainty_risk_action_map, "additional"),
    "cost_per_solved": (plot_cost_per_solved, "additional"),
    "cumulative_resolution_vs_cost": (plot_cumulative_resolution_vs_cost, "additional"),
    "results_by_repository": (plot_results_by_repository, "additional"),
    "termination_reasons": (plot_termination_reasons, "additional"),
}


@dataclass
class DiagramsReport:
    """Contrato de retorno de `generate_all`/`generate_minimal`/`generate_additional`."""

    generated: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    """Figuras omitidas por datos insuficientes (`FigureDataError`); no son errores del pipeline."""
    failed: dict[str, str] = field(default_factory=dict)
    """Figuras con excepcion inesperada (posible bug)."""


@dataclass
class DiagramsConfig:
    """Configuracion operativa de `DiagramsModule` (reservado para extensiones futuras).

    Los valores de estilo (tamano, DPI, paleta) viven en `style.py`, que no
    depende de configuracion en tiempo de ejecucion (S5 de la spec de modulo).
    """


class DiagramsModule:
    """Genera el catalogo de figuras `.png` a partir del CSV consolidado.

    Uso tipico::

        module = DiagramsModule()
        frames = module.load_data(
            Path("data/exp/datos_consolidados.csv"),
            Path("data/exp/datos_pasos_consolidados.csv"),
        )
        report = module.generate_all(frames, Path("data/exp/figures"))
    """

    def __init__(self, config: DiagramsConfig | None = None) -> None:
        """Inicializa `DiagramsModule`."""
        self._config = config or DiagramsConfig()

    def load_data(self, csv_path: Path, steps_csv_path: Path | None = None) -> AnalysisFrames:
        """Carga y tipa `datos_consolidados.csv`/`datos_pasos_consolidados.csv`."""
        return AnalysisFrames.load(csv_path, steps_csv_path)

    def generate_all(self, frames: AnalysisFrames, output_dir: Path) -> DiagramsReport:
        """Genera el catalogo completo (minimo S8.1 + adicional S8.2)."""
        return self._generate_group(frames, output_dir, {"minimal", "additional"})

    def generate_minimal(self, frames: AnalysisFrames, output_dir: Path) -> DiagramsReport:
        """Genera solo el catalogo minimo (S8.1)."""
        return self._generate_group(frames, output_dir, {"minimal"})

    def generate_additional(self, frames: AnalysisFrames, output_dir: Path) -> DiagramsReport:
        """Genera solo el catalogo adicional (S8.2)."""
        return self._generate_group(frames, output_dir, {"additional"})

    def generate_figure(self, name: str, frames: AnalysisFrames, output_dir: Path) -> Path:
        """Genera una unica figura por nombre; propaga cualquier excepcion (depuracion puntual).

        Lanza
        -----
        KeyError
            Si `name` no esta registrado en `FIGURE_REGISTRY`.
        """
        func, _ = FIGURE_REGISTRY[name]
        output_dir.mkdir(parents=True, exist_ok=True)
        return func(frames, output_dir / f"{name}.png")

    def _generate_group(self, frames: AnalysisFrames, output_dir: Path, groups: set[str]) -> DiagramsReport:
        """Auxiliar interno: generate group."""
        output_dir.mkdir(parents=True, exist_ok=True)
        report = DiagramsReport()

        for name, (func, group) in FIGURE_REGISTRY.items():
            if group not in groups:
                continue
            try:
                path = func(frames, output_dir / f"{name}.png")
                report.generated.append(path)
            except FigureDataError as exc:
                logger.info("Figura '%s' omitida: %s", name, exc)
                report.skipped[name] = str(exc)
            except Exception as exc:  # noqa: BLE001 - aislar fallos por figura (S7).
                logger.error("Figura '%s' fallo con excepcion inesperada: %s", name, exc)
                report.failed[name] = f"{type(exc).__name__}: {exc}"

        return report
