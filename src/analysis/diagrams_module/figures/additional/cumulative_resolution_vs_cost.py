"""8.2 Resolución acumulada frente a coste.

Curva de instancias resueltas acumuladas a medida que aumenta el gasto, por
configuración. Solo aplica a `source_type == "own"` (única fuente con
`cost_usd` disponible).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_cumulative_resolution_vs_cost(frames: AnalysisFrames, output_path: Path) -> Path:
    """Curva de resueltos acumulados vs coste acumulado, por configuración."""
    df = frames.runs
    if df.empty or "cost_usd" not in df.columns:
        raise style.FigureDataError("No hay columna 'cost_usd' en los datos.")

    own = df[df["source_type"] == "own"] if "source_type" in df.columns else df
    own = own.dropna(subset=["cost_usd"])
    if own.empty:
        raise style.FigureDataError("No hay runs propios con 'cost_usd' disponible.")

    fig, ax = plt.subplots(figsize=style.FIGSIZE_WIDE)
    plotted_any = False

    for agent in sorted(own["agent_id"].dropna().unique()):
        subset = own[own["agent_id"] == agent].sort_values("cost_usd")
        if subset.empty:
            continue
        cumulative_cost = subset["cost_usd"].cumsum()
        resolved_flags = subset["resolved"].fillna(False).astype(bool).astype(int)
        cumulative_resolved = resolved_flags.cumsum()
        x_values = pd.concat([pd.Series([0.0]), cumulative_cost.reset_index(drop=True)], ignore_index=True)
        y_values = pd.concat([pd.Series([0]), cumulative_resolved.reset_index(drop=True)], ignore_index=True)
        ax.plot(
            x_values,
            y_values,
            label=style.format_agent_label(agent),
            color=style.get_color(agent),
            linewidth=1.8,
        )
        plotted_any = True

    if not plotted_any:
        raise style.FigureDataError("No hay series válidas para graficar.")

    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Coste acumulado (USD)")
    ax.set_ylabel("Instancias resueltas acumuladas")
    ax.set_title("Resolución acumulada frente a coste")
    style.enable_grid(ax)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return style.save_figure(fig, output_path)
