"""8.2 Motivos de terminación.

Desglose de `termination` (`especificacion_analisis.md` S6.2 "Estado y
resultado") por configuración, separando terminaciones normales (submission
del modelo) de otros motivos (límite de pasos/coste agotado, aborto del
controlador). Solo disponible para runs con traza `robust_agent`
(`termination` es "own (parcial)").
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import pandas as pd

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_termination_reasons(frames: AnalysisFrames, output_path: Path) -> Path:
    """Barras apiladas de `termination` por configuración (runs con traza robust_agent)."""
    df = frames.runs
    if df.empty or "termination" not in df.columns:
        raise style.FigureDataError("No hay columna 'termination' en los datos.")

    with_termination = df.dropna(subset=["termination"])
    if with_termination.empty:
        raise style.FigureDataError("No hay runs con 'termination' registrada todavía.")

    agents = sorted(with_termination["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos entre los runs con 'termination'.")

    counts = pd.crosstab(with_termination["agent_id"], with_termination["termination"])
    counts = counts.reindex(index=agents, fill_value=0)
    reasons = list(counts.columns)

    fig, ax = plt.subplots(figsize=style.FIGSIZE_WIDE)
    bottoms = [0.0] * len(agents)
    x_positions = range(len(agents))
    for reason in reasons:
        values = counts[reason].to_numpy()
        bars = ax.bar(x_positions, values, bottom=bottoms, label=str(reason))
        bar_color = bars[0].get_facecolor() if len(bars) else None
        facecolor = mcolors.to_hex(bar_color[:3]) if bar_color is not None else None
        for i, (val, bottom) in enumerate(zip(values, bottoms)):
            if val >= 1:
                style.annotate_stacked_segment(
                    ax, i, bottom, float(val), str(int(val)), facecolor=facecolor
                )
        bottoms = [b + v for b, v in zip(bottoms, values)]

    sizes = counts.sum(axis=1).tolist()
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(
        style.sample_size_labels([style.format_agent_label(a) for a in agents], sizes),
        rotation=45,
        ha="right",
    )
    ax.set_ylabel("Número de runs")
    ax.set_title("Motivos de terminación por configuración")
    style.enable_grid(ax, axis="y")
    style.expand_ylim_for_annotations(ax, headroom=0.05)
    ax.legend(fontsize=8)
    fig.subplots_adjust(bottom=0.28)
    return style.save_figure(fig, output_path)
