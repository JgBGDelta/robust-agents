"""Estado del episodio (`EpisodeState`).

Estructura central que mantiene la informacion viva del run: presupuesto,
perfil, historiales de senales y decisiones, referencias git, etc. Es el
**almacen unico** del run; ningun otro componente debe mantener estado
paralelo. Vease spec general 7.

En esta fase de esqueleto solo se materializa el contenedor con los
campos comprometidos por la spec. Los modulos posteriores (incertidumbre,
metricas, controlador) leeran y escribiran sobre el. Los tipos
`UncertaintyResult`, `StructuralRiskResult` y `ControllerDecision`
todavia no existen; sus huecos se tipan como `dict` (resultado serializado
del contrato) para que el contenedor sea estable a JSON desde ya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Profile:
    """Snapshot del perfil de robustez aplicado al run.

    El nombre proviene de `RobustAgentConfig.profile` y es uno de los
    valores cerrados `strict`, `balanced` o `permissive`. Es el unico
    campo necesario para identificar el perfil en la traza.
    """

    name: str


@dataclass
class Budget:
    """Estado del presupuesto del run.

    Almacen unico sobre el que opera `BudgetGuard` (spec
    `modulo_controlador.md` 5). `cost_by_attribution` desglosa el coste
    por origen (p. ej. `agent.query`, `validation.tests`,
    `validation.self_review`); su esquema exacto se cierra en bajo nivel.
    """

    cost_used: float = 0.0
    cost_limit: float = 0.0
    steps_used: int = 0
    step_limit: int = 0
    cost_by_attribution: dict[str, float] = field(default_factory=dict)

    def to_snapshot(self) -> dict[str, Any]:
        """Devuelve el snapshot serializable que esperan `StepTrace.budget_state` y la traza."""
        return {
            "cost_used": self.cost_used,
            "cost_remaining": max(self.cost_limit - self.cost_used, 0.0) if self.cost_limit else None,
            "steps_used": self.steps_used,
            "steps_remaining": max(self.step_limit - self.steps_used, 0) if self.step_limit else None,
            "cost_by_attribution": dict(self.cost_by_attribution),
        }


@dataclass
class EpisodeState:
    """Estado completo del episodio.

    Campos comprometidos por la spec 7 del bloque del agente. Los
    historiales se materializan como listas de dicts (contratos ya
    serializados) para que el estado sea estable a JSON sin depender aun
    de los contratos de los modulos que se implementan en fases
    posteriores.
    """

    profile: Profile
    step_index: int = 0
    budget: Budget = field(default_factory=Budget)
    signals_history: list[dict[str, Any]] = field(default_factory=list)
    decisions_history: list[dict[str, Any]] = field(default_factory=list)
    git_refs: dict[str, str | None] = field(
        default_factory=lambda: {"initial": None, "previous": None, "current": None}
    )
    validation_state: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_snapshot(self) -> dict[str, Any]:
        """Snapshot serializable del estado, apto para `episode_state_final` en la traza."""
        return {
            "profile": {"name": self.profile.name},
            "step_index": self.step_index,
            "budget": self.budget.to_snapshot(),
            "signals_history": list(self.signals_history),
            "decisions_history": list(self.decisions_history),
            "git_refs": dict(self.git_refs),
            "validation_state": (None if self.validation_state is None else dict(self.validation_state)),
            "errors": list(self.errors),
        }
