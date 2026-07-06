"""Configuracion del agente robusto.

Extiende `AgentConfig` de `mini-SWE-agent` con los campos especificos del
TFG. En esta fase de esqueleto solo se compromete el campo `profile`; el
resto de parametros (umbrales, pesos, multiplicadores de presupuesto,
configuracion de validacion) se incorporan en fases posteriores conforme
se implementan los modulos correspondientes.
"""

from __future__ import annotations

from typing import Literal

from minisweagent.agents.default import AgentConfig
from pydantic import BaseModel, Field

ProfileName = Literal["strict", "balanced", "permissive"]


class StructuralMetricsConfig(BaseModel):
    """Configuracion del modulo de metricas estructurales."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "files_changed_ratio": 0.20,
            "hunks_per_file": 0.10,
            "lines_added": 0.10,
            "lines_deleted": 0.10,
            "dispersion_score": 0.20,
            "test_touch_ratio": 0.20,
        }
    )
    low_threshold: float = 0.33
    high_threshold: float = 0.66
    test_globs: list[str] = Field(
        default_factory=lambda: ["tests/**", "**/test_*.py", "**/*_test.py", "**/*test*.py"]
    )


class UncertaintyConfig(BaseModel):
    """Configuracion del modulo de incertidumbre."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "verbalized": 0.20,
            "token_entropy": 0.15,
            "hedging": 0.15,
            "cycle": 0.20,
            "failure_rate": 0.20,
            "volatility": 0.10,
        }
    )
    low_threshold: float = 0.33
    high_threshold: float = 0.66
    history_window: int = 5
    hedging_phrases: list[str] = Field(
        default_factory=lambda: [
            "i think",
            "i'm not sure",
            "i am not sure",
            "not sure",
            "probably",
            "maybe",
            "perhaps",
            "might",
            "could be",
            "let me try",
            "guess",
        ]
    )


class ControllerConfig(BaseModel):
    """Configuracion del modulo controlador.

    Materializa la matriz de decision (`modulo_controlador.md` 4.1), los
    overrides por perfil para `(high, high)`, la celda conservadora ante
    entradas degradadas (spec 6.2), la capacidad de validacion por perfil
    (spec general 5.4) y los parametros de los correctores
    (`BudgetGuard`, `StabilityMonitor`).
    """

    policy_matrix: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "low": {"low": "PROCEED", "medium": "PROCEED", "high": "RUN_VALIDATION"},
            "medium": {"low": "PROCEED", "medium": "INJECT_FEEDBACK", "high": "RUN_VALIDATION"},
            "high": {"low": "INJECT_FEEDBACK", "medium": "RUN_VALIDATION", "high": "FINALIZE_ABORT"},
        }
    )
    high_high_per_profile: dict[str, str] = Field(
        default_factory=lambda: {
            "strict": "FINALIZE_ABORT",
            "balanced": "FINALIZE_ABORT",
            "permissive": "RUN_VALIDATION",
        }
    )
    degraded_per_profile: dict[str, str] = Field(
        default_factory=lambda: {
            "permissive": "PROCEED",
            "balanced": "INJECT_FEEDBACK",
            "strict": "RUN_VALIDATION",
        }
    )
    validation_per_profile: dict[str, str] = Field(
        default_factory=lambda: {"strict": "combined", "balanced": "tests", "permissive": "none"}
    )
    tests_validation_enabled: bool = False
    """Si `True`, la capacidad `RUN_VALIDATION(tests)` esta disponible.

    En el modelo de validacion dirigida (EA.5.4), `tests` se
    materializa como **inyeccion de una directiva** al historial del agente
    (no ejecucion directa de un comando shell). El agente decide en su
    siguiente paso natural si ejecutar tests existentes del repo o crear y
    ejecutar tests propios. Esta capacidad esta disponible siempre que la
    bandera sea `True`; el contenido concreto de la directiva se controla
    via `validation_directive_template` y `forbidden_test_ids`.
    """
    validation_directive_template: str = (
        "[Validacion solicitada por el controlador]\n\n"
        "Estado actual del cambio:\n"
        "- Archivos modificados: {modified_files}\n"
        "- Incertidumbre detectada: {uncertainty_level} ({uncertainty_score})\n"
        "- Riesgo estructural detectado: {risk_level} ({risk_score})\n\n"
        "Valida tu cambio actual ejecutando tests. Tienes dos vias:\n"
        "  1. Si tu cambio toca codigo cubierto por tests existentes en el repositorio, "
        "ejecutalos con la herramienta de tests del proyecto (pytest, unittest, npm test, etc.).\n"
        "  2. Si no existen tests relevantes para el cambio, crea un test minimo en el "
        "directorio de tests del proyecto que verifique el comportamiento que has "
        "implementado, y ejecutalo.\n\n"
        "Reporta el resultado de los tests y, despues, continua con la tarea.\n"
        "{forbidden_block}"
    )
    """Template de la directiva inyectada al agente cuando se dispara
    `RUN_VALIDATION(tests)`.

    Placeholders soportados:
    - `{modified_files}`: lista (separada por comas) de archivos modificados en el diff.
    - `{uncertainty_level}` / `{uncertainty_score}`: nivel y score de incertidumbre del paso.
    - `{risk_level}` / `{risk_score}`: nivel y score de riesgo estructural del paso.
    - `{forbidden_block}`: bloque de restriccion construido a partir de
      `forbidden_test_ids`. Si la lista esta vacia o es `None`, este bloque se
      sustituye por cadena vacia.
    """
    forbidden_test_ids: list[str] | None = None
    """IDs de tests que el agente debe **NO** ejecutar.

    Permite al modulo de ejecucion del benchmark poblar la lista oficial de
    tests del evaluador (`fail_to_pass` + `pass_to_pass` en SWE-bench) por
    instancia y prevenir contaminacion de la metrica `resolved`. El
    controlador renderiza un bloque de restriccion explicito en la directiva
    cuando esta lista no es vacia. Si es `None` o vacia, no se incluye
    bloque de restriccion (caso por defecto fuera del benchmark).
    """
    self_review_enabled: bool = False
    """Si `True`, la capacidad `self_review` esta disponible para el run."""
    self_review_prompt: str = (
        "Revisa el cambio actual paso a paso y reporta riesgos o errores en formato breve."
    )
    feedback_template: str = (
        "Estado actual: incertidumbre={uncertainty_level} ({uncertainty_score}), "
        "riesgo estructural={risk_level} ({risk_score}). "
        "Refina la accion siguiente acotando el alcance y justificando el plan."
    )
    expected_costs: dict[str, float] = Field(
        default_factory=lambda: {"PROCEED": 0.0, "INJECT_FEEDBACK": 0.0, "FINALIZE": 0.0}
    )
    validation_costs: dict[str, float] = Field(
        default_factory=lambda: {
            "tests": 0.0,
            "self_review": 0.05,
            "combined": 0.05,
            "none": 0.0,
        }
    )
    stability_window: int = 10
    stability_max_uncertainty: float = 0.33
    stability_max_risk: float = 0.33


class RobustAgentConfig(AgentConfig):
    """Configuracion extendida del agente robusto.

    Hereda todos los campos de `AgentConfig` (`system_template`,
    `instance_template`, `step_limit`, `cost_limit`, `output_path`) y anade
    el perfil de robustez activo del run. La eleccion del perfil es manual
    al iniciar el run (spec general 5.3); el resto de parametros derivados
    se cierran experimentalmente en bajo nivel.
    """

    profile: ProfileName = "balanced"
    """Perfil de robustez activo: `strict`, `balanced` o `permissive`."""
    structural_metrics: StructuralMetricsConfig = Field(default_factory=StructuralMetricsConfig)
    """Configuracion del `StructuralMetricsModule` para la fase 2."""
    uncertainty: UncertaintyConfig = Field(default_factory=UncertaintyConfig)
    """Configuracion del `UncertaintyModule` para la fase 3."""
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    """Configuracion del `ControllerModule` para la fase 4."""
