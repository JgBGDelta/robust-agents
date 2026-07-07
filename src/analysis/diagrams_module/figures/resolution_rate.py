"""8.1.1 Tasa de resolución por configuración.

Porcentaje de instancias resueltas sobre el total de filas por `agent_id`
(`resolved == True` / total de instancias). Respeta `sample_group` para no
mezclar denominadores de 120 y 40 instancias sin indicarlo.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames
from analysis.diagrams_module.resolution_metrics import resolution_rate_pct


def plot_resolution_rate(frames: AnalysisFrames, output_path: Path) -> Path:
    """Barra de tasa de resolución (%) por agente."""
    df = frames.runs
    if df.empty or "agent_id" not in df.columns:
        raise style.FigureDataError("No hay filas en 'runs' con 'agent_id' para graficar.")

    agents = sorted(df["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos en los datos.")

    labels: list[str] = []
    rates: list[float | None] = []
    sizes: list[int] = []
    for agent in agents:
        subset = df[df["agent_id"] == agent]
        labels.append(style.format_agent_label(agent))
        sizes.append(len(subset))
        rates.append(resolution_rate_pct(subset))

    fig, ax = plt.subplots(figsize=style.FIGSIZE_WIDE)
    x_positions = range(len(agents))
    for i, (rate, agent) in enumerate(zip(rates, agents)):
        color = style.get_color(agent)
        if rate is None:
            style.annotate_missing(ax, i)
        else:
            style.plot_rate_bar(ax, i, rate, color)

    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(style.sample_size_labels(labels, sizes), rotation=45, ha="right")
    ax.set_ylabel("Tasa de resolución (%)")
    style.configure_rate_axis(ax, title="Tasa de resolución por configuración")
    style.enable_grid(ax)
    fig.tight_layout()
    return style.save_figure(fig, output_path)
