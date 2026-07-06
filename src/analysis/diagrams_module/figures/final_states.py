"""8.1.2 Estados finales.

Distribucion de exitos, fallos, parches vacios y errores (`run_status`,
`failure_category`, `empty_submission`) por configuracion. Solo aplica a
`source_type == "own"`: `failure_category` no esta disponible para externos.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_CATEGORY_ORDER = ("none", "limits_exceeded", "empty_submission", "precondition_failed", "exception")
_CATEGORY_LABELS = {
    "none": "Completado",
    "limits_exceeded": "Limite agotado",
    "empty_submission": "Parche vacio",
    "precondition_failed": "Precondicion fallida",
    "exception": "Excepcion",
}
_CATEGORY_COLORS = {
    "none": "#2ca02c",
    "limits_exceeded": "#ff7f0e",
    "empty_submission": "#d62728",
    "precondition_failed": "#7f7f7f",
    "exception": "#9467bd",
}


def plot_final_states(frames: AnalysisFrames, output_path: Path) -> Path:
    """Barras apiladas de `failure_category` por configuracion (runs propios)."""
    df = frames.runs
    own = df[df["source_type"] == "own"] if "source_type" in df.columns else df
    if own.empty or "failure_category" not in own.columns:
        raise style.FigureDataError("No hay runs propios con 'failure_category' para graficar.")

    configs = sorted(own["agent_id"].dropna().unique())
    if not configs:
        raise style.FigureDataError("No hay 'agent_id' válidos entre los runs propios.")

    counts = pd.crosstab(own["agent_id"], own["failure_category"])
    counts = counts.reindex(index=configs, columns=_CATEGORY_ORDER, fill_value=0)

    fig, ax = plt.subplots(figsize=style.FIGSIZE_WIDE)
    bottoms = [0.0] * len(configs)
    x_positions = range(len(configs))
    for category in _CATEGORY_ORDER:
        values = counts[category].to_numpy()
        bars = ax.bar(
            x_positions,
            values,
            bottom=bottoms,
            label=_CATEGORY_LABELS[category],
            color=_CATEGORY_COLORS[category],
        )
        facecolor = _CATEGORY_COLORS[category]
        for i, (val, bottom) in enumerate(zip(values, bottoms)):
            if val >= 1:
                style.annotate_stacked_segment(
                    ax, i, bottom, float(val), str(int(val)), facecolor=facecolor
                )
        bottoms = [b + v for b, v in zip(bottoms, values)]

    sizes = counts.sum(axis=1).tolist()
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(
        style.sample_size_labels([style.format_agent_label(c) for c in configs], sizes),
        rotation=45,
        ha="right",
    )
    ax.set_ylabel("Número de runs")
    ax.set_title("Estados finales por configuración")
    style.enable_grid(ax, axis="y")
    style.expand_ylim_for_annotations(ax)
    ax.legend()
    fig.subplots_adjust(bottom=0.28)
    return style.save_figure(fig, output_path)
