"""8.1.7 Intervenciones del controlador.

Frecuencia de cada acción (`proceed_count`, `feedback_count`,
`validation_count`, `abort_count`) por perfil `robust_*`.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_ACTIONS = (
    ("proceed_count", "PROCEED"),
    ("feedback_count", "INJECT_FEEDBACK"),
    ("validation_count", "RUN_VALIDATION"),
    ("abort_count", "FINALIZE(abort)"),
)


def plot_controller_interventions(frames: AnalysisFrames, output_path: Path) -> Path:
    """Barras agrupadas: suma de cada acción del controlador por configuración."""
    df = frames.runs
    if df.empty or "proceed_count" not in df.columns:
        raise style.FigureDataError("No hay columnas de intervención del controlador en los datos.")

    robust_df = df[df["proceed_count"].notna()]
    if robust_df.empty:
        raise style.FigureDataError("No hay runs con traza robust_agent (controller_intervention disponible).")

    agents = sorted(robust_df["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos entre los runs con controlador.")

    fig, ax = plt.subplots(figsize=style.FIGSIZE_WIDE)
    n_actions = len(_ACTIONS)
    width = 0.8 / n_actions
    x_base = np.arange(len(agents))

    for action_index, (column, label) in enumerate(_ACTIONS):
        totals = [float(robust_df.loc[robust_df["agent_id"] == a, column].sum()) for a in agents]
        offset = (action_index - (n_actions - 1) / 2) * width
        bars = ax.bar(x_base + offset, totals, width=width, label=label)
        for bar, total in zip(bars, totals):
            if total > 0:
                style.annotate_bar_value(ax, bar.get_x() + bar.get_width() / 2, total, f"{int(total)}")

    style.finalize_bar_axis(ax)
    ax.set_xticks(list(x_base))
    ax.set_xticklabels([style.format_agent_label(a) for a in agents], rotation=45, ha="right")
    ax.set_ylabel("Número total de intervenciones")
    ax.set_title("Intervenciones del controlador por perfil")
    style.enable_grid(ax, axis="y")
    ax.legend(fontsize=8)
    fig.subplots_adjust(bottom=0.28)
    return style.save_figure(fig, output_path)
