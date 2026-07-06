"""Paquete `agent` del TFG.

Expone la superficie publica del bloque del agente robusto: configuracion,
estado de episodio, los tres modulos (metricas, incertidumbre y
controlador) y el agente que los integra.
"""

import os

# Silencia el mensaje de bienvenida de mini-swe-agent (emoji 👋 no imprimible
# en terminales Windows con cp1252) antes de que sus modulos sean importados.
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "1")

from agent.config import (
    ControllerConfig,
    RobustAgentConfig,
    StructuralMetricsConfig,
    UncertaintyConfig,
)
from agent.episode_state import Budget, EpisodeState, Profile
from agent.controller_module import BudgetGuard, ControllerDecision, ControllerModule
from agent.robust_agent import ROBUST_TRACE_FORMAT_VERSION, RobustAgent
from agent.step_trace import StepTrace
from agent.structural_metrics_module import StructuralMetricsModule, StructuralRiskResult
from agent.uncertainty_module import UncertaintyModule, UncertaintyResult

__all__ = [
    "ROBUST_TRACE_FORMAT_VERSION",
    "Budget",
    "BudgetGuard",
    "EpisodeState",
    "ControllerDecision",
    "ControllerConfig",
    "ControllerModule",
    "Profile",
    "RobustAgent",
    "RobustAgentConfig",
    "StepTrace",
    "StructuralMetricsConfig",
    "StructuralMetricsModule",
    "StructuralRiskResult",
    "UncertaintyConfig",
    "UncertaintyModule",
    "UncertaintyResult",
]
