"""8.2 Coste/tokens por solución resuelta.

`cost_usd`/`total_tokens` normalizado sobre instancias con `resolved == true`,
para medir eficiencia real por solución (excluye el coste de intentos
fallidos, que no aportan ninguna solución).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_cost_per_solved(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de coste medio y tokens medios por agente, solo runs resueltos."""
    df = frames.runs
    if df.empty or "resolved" not in df.columns:
        raise style.FigureDataError("No hay columna 'resolved' en los datos.")

    resolved_df = df[df["resolved"] == True]  # noqa: E712
    if resolved_df.empty:
        raise style.FigureDataError("No hay runs con 'resolved == True' todavía.")

    fig, (ax_cost, ax_tokens) = plt.subplots(1, 2, figsize=(10, 4.2))

    agents_cost = sorted(resolved_df.dropna(subset=["cost_usd"])["agent_id"].dropna().unique())
    if agents_cost:
        labels_cost = [style.format_agent_label(a) for a in agents_cost]
        sizes_cost = [len(resolved_df[resolved_df["agent_id"] == a]) for a in agents_cost]
        for i, agent in enumerate(agents_cost):
            values = resolved_df.loc[resolved_df["agent_id"] == agent, "cost_usd"].dropna()
            style.plot_metric_bar(ax_cost, i, float(values.mean()), style.get_color(agent))
        ax_cost.set_xticks(range(len(agents_cost)))
        ax_cost.set_xticklabels(style.sample_size_labels(labels_cost, sizes_cost), rotation=45, ha="right", fontsize=7)
        ax_cost.set_title("Coste medio por solución (USD)", fontsize=10)
        style.enable_grid(ax_cost)
        style.finalize_bar_axis(ax_cost)
    else:
        ax_cost.set_visible(False)

    agents_tokens = sorted(resolved_df.dropna(subset=["total_tokens"])["agent_id"].dropna().unique())
    if agents_tokens and "total_tokens" in resolved_df.columns:
        labels_tokens = [style.format_agent_label(a) for a in agents_tokens]
        sizes_tokens = [len(resolved_df[resolved_df["agent_id"] == a]) for a in agents_tokens]
        for i, agent in enumerate(agents_tokens):
            values = resolved_df.loc[resolved_df["agent_id"] == agent, "total_tokens"].dropna()
            mean_k = float(values.mean()) / 1000.0
            style.plot_metric_bar(ax_tokens, i, mean_k, style.get_color(agent))
        ax_tokens.set_ylabel("Tokens (miles)")
        ax_tokens.set_xticks(range(len(agents_tokens)))
        ax_tokens.set_xticklabels(style.sample_size_labels(labels_tokens, sizes_tokens), rotation=45, ha="right", fontsize=7)
        ax_tokens.set_title("Tokens medios por solución (miles)", fontsize=10)
        style.enable_grid(ax_tokens)
        style.finalize_bar_axis(ax_tokens)
    else:
        ax_tokens.set_visible(False)

    fig.suptitle("Coste y tokens por solución resuelta", y=1.02)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.30, wspace=0.28)
    return style.save_figure(fig, output_path)
