"""8.1.6 Incertidumbre y resultado.

Relación de `uncertainty_mean`/`uncertainty_high_ratio` con `resolved`,
`cost_usd` y `structural_risk_mean`. Solo aplica a `robust_*` (traza
`robust_agent`): son las únicas filas con `uncertainty_mean` no nulo.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_uncertainty_outcome(frames: AnalysisFrames, output_path: Path) -> Path:
    """Panel de 3 paneles: incertidumbre vs resultado/coste/riesgo estructural."""
    df = frames.runs
    if df.empty or "uncertainty_mean" not in df.columns:
        raise style.FigureDataError("No hay columna 'uncertainty_mean' en los datos.")

    robust_df = df[df["uncertainty_mean"].notna()]
    if robust_df.empty:
        raise style.FigureDataError("No hay runs con traza robust_agent (uncertainty_mean disponible).")

    fig, (ax_bar, ax_cost, ax_risk) = plt.subplots(1, 3, figsize=(12, 5))

    evaluated = robust_df[robust_df["resolved"].notna()]
    if evaluated.empty:
        style.annotate_missing(ax_bar, 0)
    else:
        for i, outcome in enumerate([True, False]):
            values = evaluated.loc[evaluated["resolved"] == outcome, "uncertainty_mean"].dropna()
            if len(values) == 0:
                style.annotate_missing(ax_bar, i)
            else:
                mean_val = float(values.mean())
                ax_bar.bar(i, mean_val, color="#2ca02c" if outcome else "#d62728")
                style.annotate_bar_value(ax_bar, i, mean_val, style.format_metric_value(mean_val))
        style.finalize_bar_axis(ax_bar)
        ax_bar.set_xticks([0, 1])
        ax_bar.set_xticklabels(["Resuelto", "No resuelto"])
        ax_bar.legend(
            handles=[
                Patch(facecolor="#2ca02c", label="Resuelto"),
                Patch(facecolor="#d62728", label="No resuelto"),
                Line2D(
                    [],
                    [],
                    color="none",
                    label="Incertidumbre media del run = promedio de la puntuación en cada paso",
                ),
                Line2D(
                    [],
                    [],
                    color="none",
                    label="Cada barra = promedio de esa incertidumbre por grupo de resultado",
                ),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, -0.22),
            fontsize=6,
            framealpha=0.92,
            ncol=1,
        )
    ax_bar.set_ylabel("Incertidumbre media")
    ax_bar.set_title("Incertidumbre / resultado", fontsize=10)
    style.enable_grid(ax_bar)

    for config in sorted(robust_df["agent_id"].dropna().unique()):
        subset = robust_df[robust_df["agent_id"] == config]
        ax_cost.scatter(
            subset["cost_usd"],
            subset["uncertainty_mean"],
            color=style.get_color(config),
            label=style.format_agent_label(config),
            alpha=0.7,
        )
    ax_cost.set_xlabel("Coste (USD)")
    ax_cost.set_ylabel("Incertidumbre media")
    ax_cost.set_title("Incertidumbre / coste", fontsize=10)
    ax_cost.text(
        0.5,
        -0.28,
        "Cada punto: coste (USD) / incertidumbre media del run",
        transform=ax_cost.transAxes,
        ha="center",
        fontsize=7,
        style="italic",
        color="#444444",
    )
    style.enable_grid(ax_cost)

    for config in sorted(robust_df["agent_id"].dropna().unique()):
        subset = robust_df[robust_df["agent_id"] == config]
        ax_risk.scatter(
            subset["structural_risk_mean"],
            subset["uncertainty_mean"],
            color=style.get_color(config),
            label=style.format_agent_label(config),
            alpha=0.7,
        )
    ax_risk.set_xlabel("Riesgo estructural medio")
    ax_risk.set_ylabel("Incertidumbre media")
    ax_risk.set_title("Incertidumbre / riesgo estructural", fontsize=10)
    style.enable_grid(ax_risk)

    profile_handles, _ = ax_risk.get_legend_handles_labels()
    if profile_handles:
        ax_risk.legend(
            handles=profile_handles
            + [
                Line2D(
                    [],
                    [],
                    color="none",
                    label="Cada punto: riesgo estructural medio / incertidumbre media del run",
                ),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, -0.22),
            fontsize=6,
            framealpha=0.92,
            ncol=1,
            title="Perfil robusto",
        )

    fig.suptitle("Incertidumbre y resultado (solo robust_*)")
    fig.subplots_adjust(left=0.06, right=0.98, top=0.88, bottom=0.30, wspace=0.32)
    return style.save_figure(fig, output_path)
