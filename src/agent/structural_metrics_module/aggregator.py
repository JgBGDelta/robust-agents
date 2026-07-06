"""Agregador de riesgo estructural."""

from __future__ import annotations

from typing import Any

from agent.structural_metrics_module.structural_risk_result import StructuralRiskResult


class RiskAggregator:
    """Combina metricas estructurales en score y nivel discreto."""

    def __init__(self, weights: dict[str, float], low_threshold: float, high_threshold: float):
        """Inicializa pesos y umbrales de discretizacion."""
        self._weights = weights
        self._low_threshold = low_threshold
        self._high_threshold = high_threshold

    def aggregate(
        self,
        metrics: dict[str, float | int],
        diff_summary: dict[str, Any],
        evidence: dict[str, Any] | None = None,
    ) -> StructuralRiskResult:
        """Agrega metricas y devuelve `StructuralRiskResult`."""
        # Combinación de métricas disponibles con pesos (renormalizando por omisiones).
        weighted_sum = 0.0
        weight_sum = 0.0
        omitted: list[str] = []
        for name, weight in self._weights.items():
            if name not in metrics:
                omitted.append(name)
                continue
            weighted_sum += self._normalize_metric(name, float(metrics[name])) * weight
            weight_sum += weight

        # Normalización del score final al rango [0, 1] y discretización de nivel.
        score = (weighted_sum / weight_sum) if weight_sum else 0.0
        score = min(max(score, 0.0), 1.0)
        level = self._level(score)

        # Componentes omitidos para auditoría y depuración.
        merged_evidence = dict(evidence or {})
        if omitted:
            merged_evidence["omitted_components"] = omitted
        return StructuralRiskResult(
            score=score,
            level=level,
            metrics=metrics,
            diff_summary=diff_summary,
            cost_overhead=0.0,
            evidence=merged_evidence,
        )

    def _level(self, score: float) -> str:
        """Discretiza el score continuo en `low`, `medium` o `high`."""
        if score >= self._high_threshold:
            return "high"
        if score >= self._low_threshold:
            return "medium"
        return "low"

    def _normalize_metric(self, name: str, value: float) -> float:
        """Normaliza cada metrica a rango [0, 1] para agregacion ponderada."""
        if name in {"files_changed_ratio", "dispersion_score", "test_touch_ratio"}:
            return value
        if name == "hunks_per_file":
            return min(value / 5.0, 1.0)
        if name in {"files_changed", "hunks"}:
            return min(value / 20.0, 1.0)
        if name in {"lines_added", "lines_deleted", "net_lines"}:
            return min(abs(value) / 200.0, 1.0)
        return min(max(value, 0.0), 1.0)
