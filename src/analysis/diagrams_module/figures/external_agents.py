"""8.1.10 Agentes externos.

Resolución y métricas estructurales comparables (`source_type == "external"`)
frente a `default` y los perfiles robustos principales, con nota explícita de
que cada agente externo usa un modelo distinto (columna `model_id`).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_OWN_BASELINE_AGENTS = ("default", "robust_strict", "robust_balanced", "robust_permissive")


def plot_external_agents(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de resolución y churn: externos vs baseline propio."""
    df = frames.runs
    if df.empty or "source_type" not in df.columns:
        raise style.FigureDataError("No hay columna 'source_type' en los datos.")

    external_df = df[df["source_type"] == "external"]
    if external_df.empty:
        raise style.FigureDataError("No hay filas 'source_type == external' todavía.")

    combined = df[df["agent_id"].isin(_OWN_BASELINE_AGENTS) | (df["source_type"] == "external")]
    agents = list(
        dict.fromkeys(
            [a for a in _OWN_BASELINE_AGENTS if a in combined["agent_id"].values]
            + list(external_df["agent_id"].unique())
        )
    )
    labels = [style.format_agent_label(a) for a in agents]
    sizes = [len(combined[combined["agent_id"] == a]) for a in agents]

    fig, (ax_res, ax_churn) = plt.subplots(1, 2, figsize=(10, 5))

    resolution_rates: list[float | None] = []
    churn_means: list[float | None] = []
    for agent in agents:
        subset = combined.loc[combined["agent_id"] == agent]
        resolved_values = subset["resolved"].dropna()
        resolution_rates.append(float(resolved_values.mean()) * 100 if len(resolved_values) else None)
        churn_values = subset["churn_total"].dropna()
        churn_means.append(float(churn_values.mean()) if len(churn_values) else None)

    for i, (agent, rate) in enumerate(zip(agents, resolution_rates)):
        if rate is None:
            style.annotate_missing(ax_res, i)
        else:
            style.plot_rate_bar(ax_res, i, rate, style.get_color(agent))

    churn_present = [v for v in churn_means if v is not None]
    if churn_present:
        max_churn = max(churn_present)
        min_churn = min(churn_present)
        use_symlog = max_churn > 0 and (max_churn / max(min_churn, 0.01)) >= 20
        if use_symlog:
            ax_churn.set_yscale("symlog", linthresh=max(1.0, min_churn if min_churn > 0 else 1.0))

    for i, (agent, churn) in enumerate(zip(agents, churn_means)):
        if churn is None:
            style.annotate_missing(ax_churn, i)
            continue
        style.plot_metric_bar(ax_churn, i, churn, style.get_color(agent))

    style.finalize_bar_axis(ax_churn)
    style.configure_rate_axis(ax_res, title="Tasa de resolución (%)")
    ax_churn.set_title("Churn medio", fontsize=10)
    style.enable_grid(ax_res)
    style.enable_grid(ax_churn)

    for ax in (ax_res, ax_churn):
        ax.set_xticks(range(len(agents)))
        ax.set_xticklabels(style.sample_size_labels(labels, sizes), rotation=45, ha="right", fontsize=8)

    fig.suptitle("Agentes externos frente a baseline propio (modelos distintos por agente)")
    fig.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.28, wspace=0.28)
    return style.save_figure(fig, output_path)
