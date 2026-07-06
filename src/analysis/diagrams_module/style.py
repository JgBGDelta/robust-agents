"""Convenciones de estilo compartidas por todas las funciones de figura.

Unico estado global permitido en el paquete (`modulo_diagramas.md` S6). Cada
funcion `plot_<nombre>` de `figures/` depende exclusivamente de este modulo
(ademas de `pandas`/`matplotlib`), nunca de otra funcion de figura.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import matplotlib

# Backend no interactivo: el modulo solo escribe figuras a disco (`.png`), nunca
# abre una ventana. Forzarlo aqui evita depender de una instalacion de Tcl/Tk
# valida en el entorno de ejecucion (CI, contenedores, `.venv` sin Tk).
matplotlib.use("Agg")

import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.figure  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# -- Tamanos y resolucion (S5) ------------------------------------------------

FIGSIZE_SINGLE = (8, 5)
"""Figuras de una sola serie/panel."""

FIGSIZE_WIDE = (10, 6)
"""Figuras que comparan mas de 4 configuraciones o incluyen subplots."""

DPI = 150

# -- Paleta de colores estable por `configuration_id` (S5) --------------------

COLOR_MAP: dict[str, str] = {
    "default": "#7f7f7f",
    "robust_strict": "#d62728",
    "robust_balanced": "#1f77b4",
    "robust_permissive": "#2ca02c",
    "robust_no_intervention": "#9467bd",
    "robust_v2": "#8c564b",
    "agentless_v1.5": "#ff7f0e",
    "aider_20240523": "#17becf",
    "moatless_claude35_20241117": "#e377c2",
    "openhands_claude35_20240725": "#bcbd22",
}
"""Colores fijos para los `agent_id`/`configuration_id` esperados del experimento.

`especificacion_analisis.md` S5 documenta los `configuration_id` esperados con
el `agent_id` "pelado" (sin sufijo de modelo normalizado) como ejemplo; en la
practica `configuration_id` es `<agent_id>__<model_id normalizado>`
(`especificacion_analisis.md` S6.1). `get_color()` resuelve ambos casos.
"""

_FALLBACK_PALETTE = (
    "#e377c2",
    "#bcbd22",
    "#aec7e8",
    "#ffbb78",
    "#98df8a",
    "#ff9896",
    "#c5b0d5",
    "#c49c94",
)
"""Colores de reserva para `configuration_id` no previstos (deterministas por hash)."""

# Etiquetas cortas para diagramas (sin modelo ni IDs de release).
DISPLAY_AGENT_NAMES: dict[str, str] = {
    "default": "default (mini-swe-agent)",
    "robust_strict": "robust_strict",
    "robust_balanced": "robust_balanced",
    "robust_permissive": "robust_permissive",
    "robust_no_intervention": "robust_no_intervention",
    "robust_v2": "robust_v2",
    "agentless_v1.5": "agentless",
    "aider_20240523": "aider",
    "moatless_claude35_20241117": "moatless",
    "openhands_claude35_20240725": "openhands",
}


def get_color(configuration_id: str) -> str:
    """Color estable para `configuration_id`, con reserva deterministica.

    Resuelve en este orden: (1) coincidencia exacta en `COLOR_MAP`, (2)
    coincidencia del `agent_id` (prefijo antes de `__`) en `COLOR_MAP`, (3)
    color de reserva determinado por hash del nombre completo, para que el
    modulo nunca falle ante una configuracion nueva.
    """
    if configuration_id in COLOR_MAP:
        return COLOR_MAP[configuration_id]
    agent_id = configuration_id.split("__", 1)[0]
    if agent_id in COLOR_MAP:
        return COLOR_MAP[agent_id]
    digest = hashlib.sha256(configuration_id.encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(_FALLBACK_PALETTE)
    return _FALLBACK_PALETTE[index]


def format_agent_label(agent_id: str) -> str:
    """Etiqueta corta de agente para ejes (sin modelo ni fecha de release)."""
    return DISPLAY_AGENT_NAMES.get(agent_id, agent_id.split("__", 1)[0])


def format_configuration_label(configuration_id: str) -> str:
    """Etiqueta corta a partir de ``configuration_id`` o ``agent_id``."""
    agent_id = configuration_id.split("__", 1)[0]
    return format_agent_label(agent_id)


def enable_grid(ax, *, axis: str = "y") -> None:
    """Activa rejilla suave en un eje para facilitar la lectura."""
    ax.grid(True, axis=axis, alpha=0.35, linestyle="--", linewidth=0.6)
    ax.set_axisbelow(True)


def format_metric_value(value: float) -> str:
    """Formatea un valor numerico para anotacion sobre barras."""
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def symlog_ylim_top(max_height: float, *, linthresh: float = 1.0, bar_fraction: float = 0.80) -> float:
    """Calcula un tope de eje symlog que deja hueco visual para etiquetas sobre la barra más alta."""
    if max_height <= 0:
        return 1.0
    if max_height <= linthresh:
        return max_height * 1.25
    log_ratio = math.log10(max_height / linthresh)
    if log_ratio <= 0:
        return max_height * 1.25
    log_top = log_ratio / bar_fraction
    return linthresh * (10**log_top)


def annotate_bar_value(ax, x: float, height: float, text: str) -> None:
    """Anota el valor numérico justo encima de la barra."""
    y = max(height, 0.0)
    scale = ax.get_yscale()
    if scale == "symlog":
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7,
            clip_on=True,
        )
        return
    ymin, ymax = ax.get_ylim()
    span = max(ymax - ymin, y * 0.5, 1e-9)
    offset = max(y * 0.04, span * 0.03)
    ax.text(x, y + offset, text, ha="center", va="bottom", fontsize=7, clip_on=False)


def finalize_bar_axis(ax, *, min_bottom: float | None = 0.0) -> None:
    """Ajusta el eje Y para que barras y etiquetas quepan sin recortarse."""
    patch_tops: list[float] = []
    for patch in ax.patches:
        if not hasattr(patch, "get_height"):
            continue
        base = patch.get_y() if hasattr(patch, "get_y") else 0.0
        patch_tops.append(base + patch.get_height())
    max_height = max(patch_tops, default=0.0)
    label_top = max_height
    for text_artist in ax.texts:
        y_pos = text_artist.get_position()[1]
        if y_pos > label_top:
            label_top = y_pos

    ymin, ymax = ax.get_ylim()
    bottom = min_bottom if min_bottom is not None else ymin
    scale = ax.get_yscale()
    span = max(ymax - bottom, max_height, label_top - bottom, 1e-9)
    if scale == "symlog":
        linthresh = getattr(ax.yaxis.get_scale(), "linthresh", 1.0)
        if linthresh is None:
            linthresh = 1.0
        top = max(ymax, symlog_ylim_top(max_height, linthresh=float(linthresh)))
    else:
        label_clearance = max(span * 0.06, (label_top - max_height) * 2.5, max_height * 0.05)
        needed_top = label_top + label_clearance
        if ymax > needed_top * 1.12:
            top = needed_top
        else:
            top = max(ymax, needed_top)
        if top <= bottom:
            top = bottom + span * 0.15 if span > 0 else bottom + 1.0
    ax.set_ylim(bottom=bottom, top=top)

    ymin, ymax = ax.get_ylim()
    span = max(ymax - ymin, 1e-9)
    if scale != "symlog":
        for text_artist in ax.texts:
            y_pos = text_artist.get_position()[1]
            if y_pos >= ymax - span * 0.02:
                ymax = y_pos + span * 0.08
    ax.set_ylim(bottom=bottom, top=ymax)


def expand_ylim_for_annotations(ax, *, headroom: float = 0.18) -> None:
    """Alias de ``finalize_bar_axis`` (compatibilidad con figuras existentes)."""
    finalize_bar_axis(ax)


def plot_metric_bar(
    ax,
    x: int,
    value: float,
    color: str,
    *,
    annotate: bool = True,
) -> None:
    """Barra de métrica escalar con etiqueta numérica opcional."""
    ax.bar(x, value, color=color)
    if annotate:
        annotate_bar_value(ax, x, value, format_metric_value(value))


def annotate_stacked_segment(
    ax,
    x: float,
    bottom: float,
    height: float,
    text: str,
    *,
    facecolor: str | None = None,
) -> None:
    """Etiqueta centrada en un segmento de barra apilada (solo si hay espacio)."""
    if height <= 0:
        return
    fontsize = 6 if height < 8 else 7
    text_color = "white"
    if facecolor is not None:
        r, g, b = mcolors.to_rgb(facecolor)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_color = "white" if luminance < 0.55 else "black"
    ax.text(
        x,
        bottom + height / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=text_color,
        fontweight="bold",
        clip_on=False,
    )


def text_color_for_background(color: str) -> str:
    """Color de texto legible sobre un fondo de color sólido."""
    r, g, b = mcolors.to_rgb(color)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "white" if luminance < 0.55 else "black"


def text_color_for_heatmap(value: float, vmax: float) -> str:
    """Color de texto legible sobre celda de mapa de calor."""
    if vmax <= 0:
        return "black"
    return "white" if value / vmax >= 0.45 else "black"


# -- Datos faltantes (S5) ------------------------------------------------------


class FigureDataError(Exception):
    """Datos necesarios no disponibles para generar una figura (modulo_diagramas.md S7)."""


def annotate_missing(ax, x_position: float, y_position: float = 0.0, text: str = "sin datos") -> None:
    """Anota "sin datos" sobre la posicion esperada de una barra/serie vacia.

    Usado cuando una configuracion queda con cero filas validas para la
    metrica de una figura: la ausencia debe ser visible, nunca confundirse
    con un valor de cero real (S5).
    """
    ax.text(
        x_position,
        y_position,
        text,
        ha="center",
        va="bottom",
        fontsize=8,
        color="gray",
        style="italic",
        rotation=90,
    )


def configure_rate_axis(ax, *, title: str | None = None) -> None:
    """Eje Y fijo 0-100 para tasas de resolucion; evita autoescala alrededor de 0."""
    ax.set_ylim(0, 100)
    if title is not None:
        ax.set_title(title, fontsize=10)


def plot_rate_bar(ax, x: int, rate_pct: float, color: str) -> None:
    """Barra de tasa de resolucion con etiqueta numerica visible incluso si rate_pct == 0.

    Un 0% real (todas las instancias evaluadas con ``resolved=False``) no debe
    confundirse con "sin datos": la barra puede tener altura cero, pero la
    etiqueta ``0.0%`` queda visible sobre el eje.
    """
    ax.bar(x, rate_pct, color=color)
    ax.text(
        x,
        max(rate_pct, 0.5) + 1.5,
        f"{rate_pct:.1f}%",
        ha="center",
        va="bottom",
        fontsize=7,
        clip_on=False,
    )
    # configure_rate_axis fija 0-100; no hace falta expandir.


# -- Guardado (S5) --------------------------------------------------------------


def save_figure(fig: matplotlib.figure.Figure, output_path: Path) -> Path:
    """Guarda `fig` en `output_path` y cierra la figura explicitamente."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    return output_path


def sample_size_labels(labels: list[str], sizes: list[int]) -> list[str]:
    """Etiquetas `<label>\\n(n=<size>)` para hacer visible el tamano de muestra (S5)."""
    return [f"{label}\n(n={size})" for label, size in zip(labels, sizes)]
