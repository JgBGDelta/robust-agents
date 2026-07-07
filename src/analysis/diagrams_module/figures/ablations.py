"""8.1.9 Ablaciones.

Compara `robust_balanced` con los agentes de ablacion `robust_no_intervention`
y `robust_v2` (`sample_group == "ablation"`, 40 instancias) en resolucion y
coste. `robust_balanced` corre sobre las 120 instancias del pool principal
(`sample_group == "main"`), por lo que los denominadores no son directamente
comparables: se anota `n=` explicitamente por barra en vez de recortar
`robust_balanced` al subconjunto de 40 instancias (S6.1/S5 de las specs
generales, decision documentada, no una limitacion silenciosa).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames
from analysis.diagrams_module.resolution_metrics import resolution_rate_pct

_AGENTS = ("robust_balanced", "robust_no_intervention", "robust_v2")


def plot_ablations(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de resolucion y coste para `robust_balanced` vs agentes de ablacion."""
    df = frames.runs
    if df.empty or "agent_id" not in df.columns:
        raise style.FigureDataError("No hay columna 'agent_id' en los datos.")

    subset = df[df["agent_id"].isin(_AGENTS)]
    present_agents = [a for a in _AGENTS if a in subset["agent_id"].unique()]
    if not present_agents:
        raise style.FigureDataError("No hay runs de 'robust_balanced' ni de los agentes de ablacion.")

    fig, (ax_res, ax_cost) = plt.subplots(1, 2, figsize=style.FIGSIZE_SINGLE)
    sizes = [len(subset[subset["agent_id"] == a]) for a in present_agents]

    for i, agent in enumerate(present_agents):
        agent_subset = subset.loc[subset["agent_id"] == agent]
        rate = resolution_rate_pct(agent_subset)
        if rate is None:
            style.annotate_missing(ax_res, i)
        else:
            style.plot_rate_bar(ax_res, i, rate, style.get_color(agent))

        cost_values = subset.loc[subset["agent_id"] == agent, "cost_usd"].dropna()
        if len(cost_values) == 0:
            style.annotate_missing(ax_cost, i)
        else:
            style.plot_metric_bar(ax_cost, i, float(cost_values.mean()), style.get_color(agent))

    style.configure_rate_axis(ax_res, title="Tasa de resolución (%)")
    ax_cost.set_title("Coste medio (USD)", fontsize=10)
    style.finalize_bar_axis(ax_cost)
    style.enable_grid(ax_res)
    style.enable_grid(ax_cost)

    for ax in (ax_res, ax_cost):
        ax.set_xticks(range(len(present_agents)))
        ax.set_xticklabels(
            style.sample_size_labels([style.format_agent_label(a) for a in present_agents], sizes),
            rotation=45,
            ha="right",
            fontsize=8,
        )

    fig.suptitle("Ablaciones: robust_balanced vs no_intervention/v2")
    fig.tight_layout()
    return style.save_figure(fig, output_path)
