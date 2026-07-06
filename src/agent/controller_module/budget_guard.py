"""Subservicio `BudgetGuard`.

Autoridad unica sobre el presupuesto del run. Opera sobre
`EpisodeState.budget` como almacen unico de estado (spec
`modulo_controlador.md` 5). No mantiene contadores paralelos: cualquier
otro componente que incurra en coste fuera del bucle estandar
`RobustAgent.query()` debe notificarlo via `notify_cost`.
"""

from __future__ import annotations

from agent.episode_state import EpisodeState


class BudgetGuard:
    """Servicio consultable y corrector de presupuesto del run."""

    def __init__(self, episode_state: EpisodeState):
        """Inicializa el guard sobre el `EpisodeState` compartido."""
        self._episode_state = episode_state

    def can_afford(self, operation: str, expected_cost: float) -> bool:
        """Comprueba si el coste esperado cabe en el presupuesto restante.

        Es una consulta pura: no muta `EpisodeState`. Devuelve `True`
        cuando no hay limite de coste configurado (`cost_limit <= 0`).
        """
        del operation  # reservado para auditoria futura por operacion
        budget = self._episode_state.budget
        if budget.cost_limit <= 0:
            return True
        return budget.cost_used + expected_cost <= budget.cost_limit

    def notify_cost(self, amount: float, attribution: str) -> None:
        """Imputa coste al presupuesto con la atribucion indicada.

        `attribution` debe ser una etiqueta estable (p. ej.
        `"agent.query"`, `"validation.tests"`, `"validation.self_review"`).
        Acumula tanto en `cost_used` como en `cost_by_attribution`.
        """
        if amount <= 0:
            return
        budget = self._episode_state.budget
        budget.cost_used += amount
        budget.cost_by_attribution[attribution] = (
            budget.cost_by_attribution.get(attribution, 0.0) + amount
        )
