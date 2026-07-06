"""Motor de politica del controlador (`PolicyEngine`).

Implementa la matriz de decision `(uncertainty_level x risk_level)` de la
spec `modulo_controlador.md` 4.1, con los overrides por perfil para la
celda `(high, high)` y la celda conservadora a aplicar ante entradas
degradadas (spec 6.2).
"""

from __future__ import annotations

from typing import Any

from agent.config import ControllerConfig


class PolicyEngine:
    """Aplica la matriz de decision sobre niveles discretos."""

    def __init__(self, config: ControllerConfig, profile_name: str):
        """Inicializa con la configuracion y el perfil activo."""
        self._config = config
        self._profile = profile_name

    def decide_base(self, uncertainty_level: str, risk_level: str) -> dict[str, Any]:
        """Devuelve la decision cruda como `{action, subtype, rationale}`.

        La celda `(high, high)` se resuelve con `high_high_per_profile`
        para reflejar la diferencia de severidad entre perfiles.
        """
        cell = self._config.policy_matrix.get(uncertainty_level, {}).get(risk_level)
        token = cell if cell else "PROCEED"
        if uncertainty_level == "high" and risk_level == "high":
            token = self._config.high_high_per_profile.get(self._profile, token)
        return self._token_to_decision(token, uncertainty_level, risk_level)

    def degraded_decision(self) -> dict[str, Any]:
        """Devuelve la celda conservadora del perfil ante entradas degradadas."""
        token = self._config.degraded_per_profile.get(self._profile, "INJECT_FEEDBACK")
        return self._token_to_decision(token, "?", "?")

    def _token_to_decision(self, token: str, u_level: str, r_level: str) -> dict[str, Any]:
        """Traduce un token de la matriz (`FINALIZE_ABORT`, etc.) a decision cruda."""
        rationale = f"Politica base (perfil={self._profile}, u={u_level}, r={r_level}) -> {token}"
        if token == "FINALIZE_ABORT":
            return {"action": "FINALIZE", "subtype": "abort", "rationale": rationale}
        if token == "FINALIZE_SUBMIT":
            return {"action": "FINALIZE", "subtype": "submit", "rationale": rationale}
        if token in {"PROCEED", "INJECT_FEEDBACK"}:
            return {"action": token, "subtype": None, "rationale": rationale}
        if token == "RUN_VALIDATION":
            # El subtipo concreto lo resuelve el ControllerModule en funcion del perfil.
            return {"action": "RUN_VALIDATION", "subtype": "pending", "rationale": rationale}
        # Token desconocido: degradacion conservadora hacia PROCEED.
        return {"action": "PROCEED", "subtype": None, "rationale": rationale + " (token desconocido)"}
