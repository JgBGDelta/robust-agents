"""8.2 Mapa incertidumbre x riesgo x acción.

A partir de `datos_pasos_consolidados.csv`, muestra la acción del controlador
más frecuente observada para cada combinación `(uncertainty_level,
structural_risk_level)`, junto al número de pasos en esa celda. Permite
comprobar la aplicación real de la matriz de decisión frente a la matriz
teórica de `especificacion_agente.md` S5.1 (la matriz teórica no se reproduce
aquí: `DiagramsModule` solo lee el CSV consolidado, no las trazas).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from analysis.diagrams_module import style
from analysis.diagrams_module.analysis_frames import AnalysisFrames

_LEVELS = ("low", "medium", "high")


def plot_uncertainty_risk_action_map(frames: AnalysisFrames, output_path: Path) -> Path:
    """Grid `uncertainty_level` x `structural_risk_level` con la acción más frecuente."""
    if frames.steps is None or frames.steps.empty:
        raise style.FigureDataError("No hay 'datos_pasos_consolidados.csv' cargado.")

    steps = frames.steps
    required = {"uncertainty_level", "structural_risk_level", "controller_action"}
    if not required.issubset(steps.columns):
        raise style.FigureDataError(f"Faltan columnas en 'steps': {required - set(steps.columns)}")

    valid = steps.dropna(subset=["uncertainty_level", "structural_risk_level"])
    if valid.empty:
        raise style.FigureDataError("No hay pasos con nivel de incertidumbre/riesgo asignado.")

    fig, ax = plt.subplots(figsize=style.FIGSIZE_SINGLE)
    grid_counts = np.zeros((len(_LEVELS), len(_LEVELS)), dtype=int)
    cell_labels: list[list[str | None]] = [[None] * len(_LEVELS) for _ in _LEVELS]

    for i, u_level in enumerate(_LEVELS):
        for j, r_level in enumerate(_LEVELS):
            cell = valid[(valid["uncertainty_level"] == u_level) & (valid["structural_risk_level"] == r_level)]
            grid_counts[i, j] = len(cell)
            if cell.empty:
                cell_labels[i][j] = "sin datos"
                continue
            actions = cell["controller_action"].dropna()
            if actions.empty:
                cell_labels[i][j] = "N/A"
            else:
                mode = actions.mode()
                label = mode.iloc[0] if not mode.empty else "N/A"
                cell_labels[i][j] = f"{label}\n(n={len(cell)})"

    vmax = max(int(grid_counts.max()), 1)
    im = ax.imshow(grid_counts, cmap="Blues", aspect="auto", vmin=0)

    for i in range(len(_LEVELS)):
        for j in range(len(_LEVELS)):
            text = cell_labels[i][j]
            if text is None:
                continue
            if text == "sin datos":
                ax.text(j, i, text, ha="center", va="center", fontsize=7, color="gray", style="italic")
                continue
            text_color = style.text_color_for_heatmap(float(grid_counts[i, j]), float(vmax))
            ax.text(
                j,
                i,
                text,
                ha="center",
                va="center",
                fontsize=8,
                color=text_color,
                fontweight="bold" if text_color == "white" else "normal",
            )

    ax.set_xticks(range(len(_LEVELS)))
    ax.set_xticklabels(_LEVELS)
    ax.set_yticks(range(len(_LEVELS)))
    ax.set_yticklabels(_LEVELS)
    ax.set_xlabel("Riesgo estructural")
    ax.set_ylabel("Incertidumbre")
    ax.set_title("Acción del controlador más frecuente por (incertidumbre, riesgo)")
    fig.colorbar(im, ax=ax, label="Número de pasos")
    fig.subplots_adjust(right=0.88)
    return style.save_figure(fig, output_path)
