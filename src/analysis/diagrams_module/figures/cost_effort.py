"""8.1.3 Coste y esfuerzo.

Coste (`cost_usd`), pasos (`steps_used`), duración (`duration_seconds`) y
tokens (`total_tokens`) por configuración. Solo `source_type == "own"`: estos
campos no existen para agentes externos.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_METRICS = (
    ("cost_usd", "Coste medio (USD)"),
    ("steps_used", "Pasos medios"),
    ("duration_seconds", "Duración media (s)"),
    ("total_tokens", "Tokens totales medios"),
)


def plot_cost_effort(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel 2x2 con la media de coste/pasos/duración/tokens por agente."""
    df = frames.runs
    own = df[df["source_type"] == "own"] if "source_type" in df.columns else df
    if own.empty:
        raise style.FigureDataError("No hay runs propios para graficar coste/esfuerzo.")

    agents = sorted(own["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos entre los runs propios.")

    labels = [style.format_agent_label(a) for a in agents]
    fig, axes = plt.subplots(2, 2, figsize=style.FIGSIZE_WIDE)
    for ax, (column, title) in zip(axes.flat, _METRICS):
        if column not in own.columns:
            ax.set_visible(False)
            continue
        x_positions = range(len(agents))
        for i, agent in enumerate(agents):
            values = own.loc[own["agent_id"] == agent, column].dropna()
            if len(values) == 0:
                style.annotate_missing(ax, i)
            else:
                style.plot_metric_bar(ax, i, float(values.mean()), style.get_color(agent))
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_title(title, fontsize=10)
        style.enable_grid(ax)
        style.finalize_bar_axis(ax)

    fig.suptitle("Coste y esfuerzo por configuración")
    fig.tight_layout()
    return style.save_figure(fig, output_path)
