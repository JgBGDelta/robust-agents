"""8.2 Resultados por repositorio.

Tasa de resolucion y coste desglosados por `repository`, para detectar
diferencias entre proyectos SWE-bench Lite.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_results_by_repository(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de tasa de resolucion y coste medio por repositorio."""
    df = frames.runs
    if df.empty or "repository" not in df.columns:
        raise style.FigureDataError("No hay columna 'repository' en los datos.")

    repos = sorted(df["repository"].dropna().unique())
    if not repos:
        raise style.FigureDataError("No hay repositorios validos en los datos.")

    fig, (ax_res, ax_cost) = plt.subplots(1, 2, figsize=style.FIGSIZE_WIDE)
    sizes = [len(df[df["repository"] == r]) for r in repos]

    for i, repo in enumerate(repos):
        subset = df[df["repository"] == repo]

        resolved_values = subset["resolved"].dropna()
        if len(resolved_values) == 0:
            style.annotate_missing(ax_res, i)
        else:
            style.plot_rate_bar(ax_res, i, float(resolved_values.mean()) * 100, "#1f77b4")

        cost_values = subset["cost_usd"].dropna() if "cost_usd" in subset.columns else subset.iloc[0:0]
        if len(cost_values) == 0:
            style.annotate_missing(ax_cost, i)
        else:
            style.plot_metric_bar(ax_cost, i, float(cost_values.mean()), "#ff7f0e")

    style.configure_rate_axis(ax_res, title="Tasa de resolución (%)")
    ax_cost.set_title("Coste medio (USD)", fontsize=10)
    style.enable_grid(ax_res)
    style.enable_grid(ax_cost)
    style.finalize_bar_axis(ax_cost)

    for ax in (ax_res, ax_cost):
        ax.set_xticks(range(len(repos)))
        ax.set_xticklabels(style.sample_size_labels(repos, sizes), rotation=90, fontsize=7)

    fig.suptitle("Resultados por repositorio")
    fig.tight_layout()
    return style.save_figure(fig, output_path)
