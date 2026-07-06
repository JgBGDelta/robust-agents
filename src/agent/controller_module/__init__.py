"""API publica del modulo controlador.

Reexporta `ControllerModule`, `ControllerDecision` y `BudgetGuard`.
`BudgetGuard` se incluye porque es un servicio publico consultable por
otros componentes del agente (spec `modulo_controlador.md` 5.2).
"""

from agent.controller_module.budget_guard import BudgetGuard
from agent.controller_module.controller_decision import ControllerDecision
from agent.controller_module.controller_module import ControllerModule

__all__ = ["BudgetGuard", "ControllerDecision", "ControllerModule"]
