"""`DecisionLogger`: persiste `ControllerDecision` en `EpisodeState`."""

from __future__ import annotations

from agent.episode_state import EpisodeState
from agent.controller_module.controller_decision import ControllerDecision


class DecisionLogger:
    """Vuelca cada decision en `EpisodeState.decisions_history`."""

    def __init__(self, episode_state: EpisodeState):
        """Inicializa el logger sobre el `EpisodeState` compartido."""
        self._episode_state = episode_state

    def record(self, decision: ControllerDecision, step_index: int) -> None:
        """Anade la decision serializada al historial del episodio."""
        self._episode_state.decisions_history.append(
            {"decision": decision.to_dict(), "step_index": step_index}
        )
