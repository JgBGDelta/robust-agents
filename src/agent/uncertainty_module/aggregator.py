"""Agregador de incertidumbre.

Combina las seis subseñales (`verbalized`, `token_entropy`, `hedging`,
`cycle`, `failure_rate`, `volatility`) en un `UncertaintyResult` con
`score` continuo y `level` discreto. Aplica renormalizacion de pesos
cuando alguna subseñal esta omitida (degradacion limpia).
"""

from __future__ import annotations

from typing import Any

from agent.uncertainty_module.uncertainty_result import UncertaintyResult


class UncertaintyAggregator:
    """Agrega las subseñales en score y nivel discreto."""

    def __init__(
        self,
        weights: dict[str, float],
        low_threshold: float,
        high_threshold: float,
    ):
        """Inicializa pesos y umbrales de discretizacion."""
        self._weights = weights
        self._low_threshold = low_threshold
        self._high_threshold = high_threshold

    def aggregate(
        self,
        components: dict[str, float],
        evidence: dict[str, Any],
        omitted: list[str],
    ) -> UncertaintyResult:
        """Combina las subseñales y construye el `UncertaintyResult`."""
        # Combinacion lineal con renormalizacion sobre subseñales presentes.
        weighted_sum = 0.0
        weight_sum = 0.0
        for name, weight in self._weights.items():
            if name not in components:
                continue
            weighted_sum += float(components[name]) * weight
            weight_sum += weight

        # Score final acotado a [0, 1] y nivel discreto del perfil.
        score = (weighted_sum / weight_sum) if weight_sum else 0.5
        score = min(max(score, 0.0), 1.0)
        level = self._level(score)

        # Anotacion de omisiones para auditoria.
        merged_evidence = dict(evidence)
        if omitted:
            merged_evidence["omitted_components"] = omitted

        return UncertaintyResult(
            score=score,
            level=level,
            components=components,
            evidence=merged_evidence,
            cost_overhead=0.0,
        )

    def _level(self, score: float) -> str:
        """Discretiza el score continuo en `low`, `medium` o `high`."""
        if score >= self._high_threshold:
            return "high"
        if score >= self._low_threshold:
            return "medium"
        return "low"
