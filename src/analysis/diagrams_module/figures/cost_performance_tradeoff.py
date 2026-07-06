"""8.1.11 Compromiso coste-rendimiento.

Resolución frente a coste (`cost_usd`) y frente a riesgo estructural
(`structural_risk_mean`), por agente. Un punto por `agent_id`
(agregado), tamaño de punto proporcional al número de runs.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames


def plot_cost_performance_tradeoff(frames: AnalysisFrames, output_path: Path) -> Path:
    """Dispersión agregada por agente: resolución vs coste / riesgo estructural."""
    df = frames.runs
    if df.empty or "agent_id" not in df.columns:
        raise style.FigureDataError("No hay filas en 'runs' para el compromiso coste-rendimiento.")

    agents = sorted(df["agent_id"].dropna().unique())
    if not agents:
        raise style.FigureDataError("No hay 'agent_id' válidos en los datos.")

    fig, (ax_cost, ax_risk) = plt.subplots(1, 2, figsize=style.FIGSIZE_WIDE)
    any_cost = any_risk = False

    for agent in agents:
        subset = df[df["agent_id"] == agent]
        color = style.get_color(agent)
        label = style.format_agent_label(agent)
        n = len(subset)

        resolved_values = subset["resolved"].dropna()
        resolution_rate = float(resolved_values.mean()) * 100 if len(resolved_values) > 0 else None

        cost_values = subset["cost_usd"].dropna()
        if resolution_rate is not None and len(cost_values) > 0:
            ax_cost.scatter(float(cost_values.mean()), resolution_rate, s=30 + n, color=color, label=label)
            any_cost = True

        risk_values = subset["structural_risk_mean"].dropna()
        if resolution_rate is not None and len(risk_values) > 0:
            ax_risk.scatter(float(risk_values.mean()), resolution_rate, s=30 + n, color=color, label=label)
            any_risk = True

    if not any_cost and not any_risk:
        raise style.FigureDataError("No hay suficientes datos de coste/riesgo/resolución cruzados.")

    ax_cost.set_xlabel("Coste medio (USD)")
    ax_cost.set_ylabel("Tasa de resolución (%)")
    ax_cost.set_title("Resolución vs coste")
    style.enable_grid(ax_cost)

    ax_risk.set_xlabel("Riesgo estructural medio")
    ax_risk.set_ylabel("Tasa de resolución (%)")
    ax_risk.set_title("Resolución vs riesgo estructural")
    ax_risk.legend(fontsize=7, loc="best")
    style.enable_grid(ax_risk)

    fig.suptitle("Compromiso coste-rendimiento por configuración")
    fig.tight_layout()
    return style.save_figure(fig, output_path)
