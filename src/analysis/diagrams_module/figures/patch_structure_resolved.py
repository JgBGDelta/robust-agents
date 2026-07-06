"""8.1.5 Estructura de parches resueltos.

Igual que "Estructura de los parches" (8.1.4), filtrado a `resolved == true`,
para comparar solo entre soluciones correctas.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_METRICS = (
    ("files_modified_count", "Ficheros modificados (media)"),
    ("churn_total", "Churn total (media)"),
    ("hunks_count", "Hunks (media)"),
    ("dispersion_score", "Dispersión (media)"),
)


def plot_patch_structure_resolved(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel 2x2 con la media de métricas estructurales, solo runs resueltos."""
    df = frames.runs
    if df.empty or "resolved" not in df.columns:
        raise style.FigureDataError("No hay columna 'resolved' para filtrar runs resueltos.")

    resolved_df = df[df["resolved"] == True]  # noqa: E712 (booleano nulable)
    if resolved_df.empty:
        raise style.FigureDataError("No hay runs con 'resolved == True' todavía.")

    agents = sorted(resolved_df["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos entre los runs resueltos.")

    labels = [style.format_agent_label(a) for a in agents]
    sizes = [len(resolved_df[resolved_df["agent_id"] == a]) for a in agents]
    tick_labels = style.sample_size_labels(labels, sizes)

    fig, axes = plt.subplots(2, 2, figsize=(style.FIGSIZE_WIDE[0], style.FIGSIZE_WIDE[1] + 3.0))
    for panel_idx, (ax, (column, subtitle)) in enumerate(zip(axes.flat, _METRICS)):
        if column not in resolved_df.columns:
            ax.set_visible(False)
            continue
        x_positions = range(len(agents))
        for i, agent in enumerate(agents):
            values = resolved_df.loc[resolved_df["agent_id"] == agent, column].dropna()
            if len(values) == 0:
                style.annotate_missing(ax, i)
            else:
                style.plot_metric_bar(ax, i, float(values.mean()), style.get_color(agent))
        ax.set_title(subtitle, fontsize=10)
        style.enable_grid(ax)
        style.finalize_bar_axis(ax)
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=7)
        ax.tick_params(axis="x", pad=2 if panel_idx < 2 else 4)
        for tick_label in ax.get_xticklabels():
            tick_label.set_clip_on(False)

    fig.suptitle("Estructura de parches resueltos por configuración")
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.20, hspace=0.88, wspace=0.25)
    return style.save_figure(fig, output_path)
