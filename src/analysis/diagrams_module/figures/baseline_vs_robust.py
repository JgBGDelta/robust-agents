"""8.1.8 Baseline frente a RobustAgent.

Compara `default` con los tres perfiles robustos principales
(`robust_strict`, `robust_balanced`, `robust_permissive`) en resolucion,
coste y estructura del parche. Se agrupa por `agent_id` (no `configuration_id`)
para no fragmentar por variaciones de `model_id` entre agentes.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames
from analysis.diagrams_module.resolution_metrics import resolution_rate_pct

_AGENTS = ("default", "robust_strict", "robust_balanced", "robust_permissive")


def plot_baseline_vs_robust(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de 3 metricas (resolucion, coste, churn) para baseline vs robustos."""
    df = frames.runs
    if df.empty or "agent_id" not in df.columns:
        raise style.FigureDataError("No hay columna 'agent_id' en los datos.")

    subset = df[df["agent_id"].isin(_AGENTS)]
    present_agents = [a for a in _AGENTS if a in subset["agent_id"].unique()]
    if not present_agents:
        raise style.FigureDataError("No hay runs de 'default' ni de perfiles robustos principales.")

    fig, (ax_res, ax_cost, ax_churn) = plt.subplots(1, 3, figsize=style.FIGSIZE_WIDE)
    sizes = [len(subset[subset["agent_id"] == a]) for a in present_agents]

    _bar_metric(ax_res, subset, present_agents, metric="resolved", is_rate=True, title="Tasa de resolución (%)")
    _bar_metric(ax_cost, subset, present_agents, metric="cost_usd", is_rate=False, title="Coste medio (USD)")
    _bar_metric(ax_churn, subset, present_agents, metric="churn_total", is_rate=False, title="Churn medio")

    for ax in (ax_res, ax_cost, ax_churn):
        ax.set_xticks(range(len(present_agents)))
        ax.set_xticklabels(
            style.sample_size_labels([style.format_agent_label(a) for a in present_agents], sizes),
            rotation=45,
            ha="right",
            fontsize=8,
        )
        style.enable_grid(ax)

    fig.suptitle("default (mini-swe-agent) frente a RobustAgent")
    fig.tight_layout()
    return style.save_figure(fig, output_path)


def _bar_metric(ax, df, agents: list[str], *, metric: str, is_rate: bool, title: str) -> None:
    """Auxiliar interno: bar metric."""
    for i, agent in enumerate(agents):
        subset = df.loc[df["agent_id"] == agent]
        if is_rate:
            rate = resolution_rate_pct(subset)
            if rate is None:
                style.annotate_missing(ax, i)
                continue
            style.plot_rate_bar(ax, i, rate, style.get_color(agent))
            continue
        values = subset[metric].dropna()
        if len(values) == 0:
            style.annotate_missing(ax, i)
            continue
        style.plot_metric_bar(ax, i, float(values.mean()), style.get_color(agent))
    if is_rate:
        style.configure_rate_axis(ax, title=title)
    else:
        ax.set_title(title, fontsize=10)
        style.finalize_bar_axis(ax)
