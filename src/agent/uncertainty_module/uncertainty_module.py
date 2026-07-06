"""Modulo principal de incertidumbre.

Ensambla las dos capas de señales (single-shot del modelo y proceso) y
las agrega en un unico `UncertaintyResult` por paso. Sigue la spec
`docs/especificaciones/agente/modulo_incertidumbre.md`: una instancia por run,
inyectada al `RobustAgent`, sin llamadas adicionales al modelo en la
version inicial.
"""

from __future__ import annotations

from typing import Any

from agent.config import UncertaintyConfig
from agent.episode_state import EpisodeState
from agent.uncertainty_module.aggregator import UncertaintyAggregator
from agent.uncertainty_module.process import ProcessSignals
from agent.uncertainty_module.single_shot import SingleShotSignals
from agent.uncertainty_module.uncertainty_result import UncertaintyResult

_NEUTRAL_COMPONENTS: dict[str, float] = {
    "verbalized": 0.5,
    "token_entropy": 0.5,
    "hedging": 0.5,
    "cycle": 0.5,
    "failure_rate": 0.5,
    "volatility": 0.5,
}


class UncertaintyModule:
    """Estima la incertidumbre del paso actual del agente."""

    def __init__(self, config: UncertaintyConfig, episode_state: EpisodeState):
        """Inicializa dependencias y submodulos internos."""
        self._config = config
        self._episode_state = episode_state
        self._single_shot = SingleShotSignals(hedging_phrases=config.hedging_phrases)
        self._process = ProcessSignals(history_window=config.history_window)
        self._aggregator = UncertaintyAggregator(
            weights=config.weights,
            low_threshold=config.low_threshold,
            high_threshold=config.high_threshold,
        )

    def evaluate(
        self,
        message: dict[str, Any],
        recent_history: list[dict[str, Any]] | None = None,
    ) -> UncertaintyResult:
        """Calcula las señales L1 + L2 y devuelve el `UncertaintyResult` del paso.

        Si alguna subseñal no puede computarse, se omite y los pesos se
        renormalizan sobre las restantes (degradacion limpia). Si el
        modulo entero falla, se devuelve un resultado neutro (`score=0.5`,
        `level=medium`) y se registra la incidencia en
        `EpisodeState.errors`.
        """
        history = recent_history or []
        try:
            components, evidence, omitted = self._collect_components(message, history)
            return self._aggregator.aggregate(
                components=components,
                evidence=evidence,
                omitted=omitted,
            )
        except Exception as exc:
            return self._neutral_result(exc)

    def reset(self) -> None:
        """Reinicia el estado interno reiniciable entre runs.

        En la version inicial los submodulos son sin estado entre pasos,
        por lo que la operacion es un no-op documentado para uso futuro
        (p. ej. cache del detector de hedging o ventanas internas).
        """
        return None

    def _collect_components(
        self,
        message: dict[str, Any],
        recent_history: list[dict[str, Any]],
    ) -> tuple[dict[str, float], dict[str, Any], list[str]]:
        """Computa las seis subseñales y registra las que se omiten."""
        components: dict[str, float] = {}
        omitted: list[str] = []
        logprobs_available = False

        # Capa 1: señales single-shot del modelo (verbalized, token entropy, hedging).
        verbalized = self._single_shot.verbalized(message)
        if verbalized is None:
            omitted.append("verbalized:not_reported")
        else:
            components["verbalized"] = verbalized

        token_entropy = self._single_shot.token_entropy(message)
        if token_entropy is None:
            omitted.append("token_entropy:no_logprobs")
        else:
            components["token_entropy"] = token_entropy
            logprobs_available = True

        hedging = self._single_shot.hedging(message)
        if hedging is None:
            omitted.append("hedging:empty_content")
        else:
            components["hedging"] = hedging

        # Capa 2: señales de proceso sobre la traza acumulada.
        components["cycle"] = self._process.cycle(message, recent_history)
        components["failure_rate"] = self._process.failure_rate(recent_history)
        components["volatility"] = self._process.volatility(self._episode_state.signals_history)

        evidence: dict[str, Any] = {"logprobs_available": logprobs_available}
        return components, evidence, omitted

    def _neutral_result(self, exc: Exception) -> UncertaintyResult:
        """Construye un `UncertaintyResult` neutro y registra el fallo."""
        error_payload = {
            "component": "uncertainty",
            "error_type": type(exc).__name__,
            "detail": str(exc),
        }
        self._episode_state.errors.append(error_payload)
        return UncertaintyResult(
            score=0.5,
            level="medium",
            components=dict(_NEUTRAL_COMPONENTS),
            evidence={"logprobs_available": False, "error": error_payload},
            cost_overhead=0.0,
        )
