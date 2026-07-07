"""Métricas centralizadas de tasa de resolución para el DiagramsModule.

Definición oficial (alineada con ``especificacion_benchmark.md`` EB.8.4):

- **Numerador**: filas con ``resolved == True``.
- **Denominador**: total de filas del grupo (todas las instancias de la
  configuración o agrupación analizada).
- Cualquier otro valor de ``resolved`` (``False``, ``null``, ausente) cuenta
  como no resuelto y permanece en el denominador.

El número de filas con evaluación funcional disponible (``evaluable``) es una
métrica descriptiva secundaria; no modifica el denominador de la tasa principal.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ResolutionRateStats:
    """Estadísticas de tasa de resolución sobre un subconjunto de runs."""

    resolved_count: int
    total_instances: int
    evaluable_count: int
    rate_fraction: float | None
    rate_pct: float | None


def count_resolved(subset: pd.DataFrame) -> int:
    """Cuenta instancias con ``resolved == True``."""
    if subset.empty or "resolved" not in subset.columns:
        return 0
    return int((subset["resolved"] == True).sum())  # noqa: E712


def count_evaluable(subset: pd.DataFrame) -> int:
    """Cuenta filas con evaluación funcional disponible (métrica secundaria)."""
    if subset.empty:
        return 0
    if "evaluable" in subset.columns:
        return int((subset["evaluable"] == True).sum())  # noqa: E712
    if "resolved" in subset.columns:
        return int(subset["resolved"].notna().sum())
    return 0


def resolution_rate_fraction(subset: pd.DataFrame) -> float | None:
    """Fracción resuelta en ``[0, 1]``, o ``None`` si el subconjunto está vacío."""
    total = len(subset)
    if total == 0:
        return None
    return count_resolved(subset) / total


def resolution_rate_pct(subset: pd.DataFrame) -> float | None:
    """Tasa de resolución en porcentaje (0–100), o ``None`` si no hay filas."""
    fraction = resolution_rate_fraction(subset)
    if fraction is None:
        return None
    return fraction * 100.0


def resolution_rate_stats(subset: pd.DataFrame) -> ResolutionRateStats:
    """Estadísticas completas de resolución sobre un subconjunto."""
    total = len(subset)
    resolved = count_resolved(subset)
    evaluable = count_evaluable(subset)
    rate = None if total == 0 else resolved / total
    return ResolutionRateStats(
        resolved_count=resolved,
        total_instances=total,
        evaluable_count=evaluable,
        rate_fraction=rate,
        rate_pct=None if rate is None else rate * 100.0,
    )


def resolution_rate_delta_pp(
    baseline: pd.DataFrame,
    other: pd.DataFrame,
) -> float | None:
    """Diferencia en puntos porcentuales (``other`` − ``baseline``)."""
    base_rate = resolution_rate_fraction(baseline)
    other_rate = resolution_rate_fraction(other)
    if base_rate is None or other_rate is None:
        return None
    return (other_rate - base_rate) * 100.0
