"""8.1.4 Estructura de los parches.

Archivos modificados, *churn*, *hunks* y dispersión (`files_modified_count`,
`churn_total`, `hunks_count`, `dispersion_score`) por agente,
incluyendo agentes externos: estas métricas se recalculan de forma uniforme
para `own` y `external` vía `PatchMetricsExtractor` extendido
(`especificacion_analisis.md` S6.3.2).
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


def plot_patch_structure(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel 2x2 con la media de métricas estructurales del parche por agente."""
    df = frames.runs
    if df.empty or "agent_id" not in df.columns:
        raise style.FigureDataError("No hay filas para graficar la estructura de los parches.")

    agents = sorted(df["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos en los datos.")

    width = max(style.FIGSIZE_WIDE[0], 0.75 * len(agents))
    fig, axes = plt.subplots(2, 2, figsize=(width, style.FIGSIZE_WIDE[1] + 3.5))
    labels = [style.format_agent_label(a) for a in agents]
    sizes = [len(df[df["agent_id"] == a]) for a in agents]
    tick_labels = style.sample_size_labels(labels, sizes)

    for panel_idx, (ax, (column, subtitle)) in enumerate(zip(axes.flat, _METRICS)):
        if column not in df.columns:
            ax.axis("off")
            continue
        values: list[float | None] = []
        for agent in agents:
            series = df.loc[df["agent_id"] == agent, column].dropna()
            values.append(float(series.mean()) if len(series) else None)

        if column == "churn_total":
            present = [v for v in values if v is not None]
            if present and max(present) / max(min(present), 0.01) >= 20:
                linthresh = max(1.0, min(present))
                ax.set_yscale("symlog", linthresh=linthresh)

        x_positions = range(len(agents))
        for i, (agent, value) in enumerate(zip(agents, values)):
            if value is None:
                style.annotate_missing(ax, i)
            else:
                style.plot_metric_bar(ax, i, value, style.get_color(agent))

        ax.set_title(subtitle, fontsize=10)
        style.enable_grid(ax)
        style.finalize_bar_axis(ax)
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=7)
        ax.tick_params(axis="x", pad=2 if panel_idx < 2 else 4)
        for tick_label in ax.get_xticklabels():
            tick_label.set_clip_on(False)

    fig.suptitle("Estructura de los parches por configuración")
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.20, hspace=0.88, wspace=0.25)
    return style.save_figure(fig, output_path)
