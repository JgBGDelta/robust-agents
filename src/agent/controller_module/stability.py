"""`StabilityMonitor`: corrector de finalizacion por estabilidad.

Cuando la incertidumbre y el riesgo son bajos y la `surface_signature`
del modulo de metricas no cambia en una ventana reciente, promueve la
celda `(low, low)` a `FINALIZE(submit)` (spec `modulo_controlador.md`
4.2 y 6.2).
"""

from __future__ import annotations

from agent.config import ControllerConfig
from agent.episode_state import EpisodeState


class StabilityMonitor:
    """Vigila la trayectoria reciente para detectar estabilidad finalizable."""

    def __init__(self, config: ControllerConfig, episode_state: EpisodeState):
        """Inicializa el monitor sobre el `EpisodeState` compartido."""
        self._config = config
        self._episode_state = episode_state

    def should_promote_submit(self, uncertainty_score: float, risk_score: float) -> bool:
        """Devuelve `True` si procede promover `(low, low)` a `FINALIZE(submit)`.

        Requiere simultaneamente: scores bajo umbral de estabilidad,
        ventana llena, y `surface_signature` constante (no vacia) en toda
        la ventana.
        """
        # Filtro rapido por score: si alguno excede el umbral, no hay estabilidad.
        if uncertainty_score > self._config.stability_max_uncertainty:
            return False
        if risk_score > self._config.stability_max_risk:
            return False

        signatures = self._recent_signatures()
        if len(signatures) < self._config.stability_window:
            return False

        reference = signatures[0]
        if not reference:
            return False
        return all(sig == reference for sig in signatures)

    def _recent_signatures(self) -> list[str]:
        """Extrae las ultimas ``cumulative_surface_signature`` de ``signals_history``.

        Se usa la firma **acumulada** (desde el baseline hasta el estado
        actual) en lugar de la incremental para que el monitor distinga
        correctamente entre:

        - Agente que hizo cambios reales y lleva N pasos estable:
          ``cumulative_surface_signature`` constante y no vacía → promover.
        - Agente atascado sin cambios (loop de errores, sin ediciones):
          ``cumulative_surface_signature`` vacía → no promover.
        """
        signatures: list[str] = []
        for entry in self._episode_state.signals_history:
            if not isinstance(entry, dict) or "structural_risk" not in entry:
                continue
            sig = entry["structural_risk"].get("diff_summary", {}).get(
                "cumulative_surface_signature"
            )
            if isinstance(sig, str):
                signatures.append(sig)
        return signatures[-self._config.stability_window :]
