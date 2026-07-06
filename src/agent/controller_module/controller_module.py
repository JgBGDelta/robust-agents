"""Modulo principal del controlador.

Combina los tres insumos del paso (incertidumbre, riesgo estructural y
estado del presupuesto) y emite un `ControllerDecision` por paso.
Sigue la spec `docs/especificaciones/agente/modulo_controlador.md`:

- Una instancia por run, inyectada al `RobustAgent`.
- Pipeline: politica base (`PolicyEngine`) -> resolucion de subtipo de
  validacion (con cadena de fallback de la spec general 5.4) ->
  corrector de presupuesto (`BudgetGuard`) -> corrector de estabilidad
  (`StabilityMonitor`).
- Persistencia via `DecisionLogger` en `EpisodeState.decisions_history`.

El controlador **decide**; la aplicacion efectiva (inyectar mensajes,
ejecutar validacion, finalizar) la realiza el `RobustAgent` leyendo el
`ControllerDecision`. Esta separacion mantiene al modulo puro y
testeable.
"""

from __future__ import annotations

from typing import Any

from agent.config import ControllerConfig
from agent.episode_state import EpisodeState
from agent.controller_module.budget_guard import BudgetGuard
from agent.controller_module.logger import DecisionLogger
from agent.controller_module.controller_decision import ControllerDecision
from agent.controller_module.policy import PolicyEngine
from agent.controller_module.stability import StabilityMonitor
from agent.structural_metrics_module import StructuralRiskResult
from agent.uncertainty_module import UncertaintyResult

# Cadena de degradacion permitida para `validation` segun spec general 5.4.
_FALLBACK_CHAIN: dict[str, list[str]] = {
    "combined": ["combined", "tests", "self_review", "none"],
    "tests": ["tests", "self_review", "none"],
    "self_review": ["self_review", "none"],
    "none": ["none"],
}


class ControllerModule:
    """Decide la accion de control del siguiente paso del agente."""

    def __init__(self, config: ControllerConfig, episode_state: EpisodeState):
        """Inicializa submodulos internos y el `BudgetGuard` publico."""
        self._config = config
        self._episode_state = episode_state
        self._profile_name = episode_state.profile.name
        self._policy = PolicyEngine(config, self._profile_name)
        self._stability = StabilityMonitor(config, episode_state)
        self._logger = DecisionLogger(episode_state)
        self.budget_guard = BudgetGuard(episode_state)

    def decide(
        self,
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> ControllerDecision:
        """Devuelve la decision del paso y la registra en el historial.

        Captura cualquier excepcion inesperada y la traduce a
        `FINALIZE(abort)` con `overrides.controller_error`, conforme a
        la spec 6.2 (ningun fallo del controlador aborta abruptamente el
        run; siempre se persiste una decision).
        """
        try:
            return self._decide(uncertainty, structural_risk)
        except Exception as exc:
            return self._abort_on_error(exc)

    def _decide(
        self,
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> ControllerDecision:
        """Implementa el pipeline de decision (politica + correctores)."""
        overrides: dict[str, Any] = {}

        # Politica base: entrada degradada o decision por matriz.
        if self._is_degraded_uncertainty(uncertainty):
            raw = self._policy.degraded_decision()
            overrides["degraded_input"] = "uncertainty_neutral"
        else:
            raw = self._policy.decide_base(uncertainty.level, structural_risk.level)

        # Resolucion del subtipo de validacion con cadena de fallback (spec general 5.4).
        if raw["action"] == "RUN_VALIDATION":
            raw, overrides = self._resolve_validation_subtype(raw, overrides)

        # Corrector de presupuesto: clamp a FINALIZE(abort) si no cabe.
        raw, overrides = self._apply_budget_corrector(raw, overrides)

        # Corrector de estabilidad: promueve (low,low) estable a FINALIZE(submit).
        raw, overrides = self._apply_stability_corrector(raw, uncertainty, structural_risk, overrides)

        # Construccion final de la decision con payload de intencion.
        decision = ControllerDecision(
            action=raw["action"],
            subtype=raw["subtype"],
            rationale=raw["rationale"],
            policy_snapshot=self._policy_snapshot(uncertainty, structural_risk),
            overrides=overrides,
            payload=self._build_payload(raw, uncertainty, structural_risk),
        )
        self._logger.record(decision, step_index=self._episode_state.step_index)
        return decision

    def _is_degraded_uncertainty(self, uncertainty: UncertaintyResult) -> bool:
        """Detecta un `UncertaintyResult` neutro emitido por fallo aguas arriba."""
        evidence = uncertainty.evidence or {}
        return "error" in evidence

    def _resolve_validation_subtype(
        self,
        raw: dict[str, Any],
        overrides: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resuelve el subtipo de `RUN_VALIDATION` con cadena de fallback."""
        target = self._config.validation_per_profile.get(self._profile_name, "tests")
        effective = self._fallback_subtype(target)

        # Registro de la degradacion si el subtipo efectivo difiere del solicitado.
        if effective != target:
            overrides["validation_fallback"] = f"{target} -> {effective}"

        # Subtipo `none`: degenera la accion en PROCEED segun spec 4 / 5.4.
        if effective == "none":
            overrides["validation_degenerated"] = "RUN_VALIDATION -> PROCEED"
            raw = {
                "action": "PROCEED",
                "subtype": None,
                "rationale": raw["rationale"] + " | sin validacion disponible, degenera a PROCEED",
            }
        else:
            raw = {**raw, "subtype": effective}
        return raw, overrides

    def _fallback_subtype(self, target: str) -> str:
        """Recorre la cadena de fallback hasta encontrar un subtipo soportado."""
        chain = _FALLBACK_CHAIN.get(target, [target, "none"])
        for candidate in chain:
            if self._validation_supported(candidate):
                return candidate
        return "none"

    def _validation_supported(self, subtype: str) -> bool:
        """Indica si la capacidad concreta esta configurada en este run."""
        if subtype == "none":
            return True
        if subtype == "tests":
            return bool(self._config.tests_validation_enabled)
        if subtype == "self_review":
            return bool(self._config.self_review_enabled)
        if subtype == "combined":
            return bool(self._config.tests_validation_enabled) and bool(
                self._config.self_review_enabled
            )
        return False

    def _apply_budget_corrector(
        self,
        raw: dict[str, Any],
        overrides: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Reduce la accion a `FINALIZE(abort)` si no cabe en el presupuesto."""
        expected = self._expected_cost(raw)
        if expected <= 0:
            return raw, overrides
        if self.budget_guard.can_afford(raw["action"], expected):
            return raw, overrides
        overrides["budget_clamp"] = f"{raw['action']} -> FINALIZE(abort) por presupuesto"
        clamped = {
            "action": "FINALIZE",
            "subtype": "abort",
            "rationale": raw["rationale"] + " | BudgetGuard: presupuesto insuficiente -> FINALIZE(abort)",
        }
        return clamped, overrides

    def _expected_cost(self, raw: dict[str, Any]) -> float:
        """Coste esperado declarado en config para la accion/subtipo."""
        if raw["action"] == "RUN_VALIDATION":
            return float(self._config.validation_costs.get(raw["subtype"] or "none", 0.0))
        return float(self._config.expected_costs.get(raw["action"], 0.0))

    def _apply_stability_corrector(
        self,
        raw: dict[str, Any],
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
        overrides: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Promueve `(low, low)` a `FINALIZE(submit)` cuando hay estabilidad."""
        if raw["action"] != "PROCEED":
            return raw, overrides
        if uncertainty.level != "low" or structural_risk.level != "low":
            return raw, overrides
        if not self._stability.should_promote_submit(uncertainty.score, structural_risk.score):
            return raw, overrides
        overrides["stability_promotion"] = "(low,low) -> FINALIZE(submit)"
        promoted = {
            "action": "FINALIZE",
            "subtype": "submit",
            "rationale": raw["rationale"] + " | StabilityMonitor: trayectoria estable -> FINALIZE(submit)",
        }
        return promoted, overrides

    def _build_payload(
        self,
        raw: dict[str, Any],
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> dict[str, Any]:
        """Construye el payload de intencion segun la accion final."""
        if raw["action"] == "INJECT_FEEDBACK":
            return {"feedback_text": self._render_feedback(uncertainty, structural_risk)}
        if raw["action"] == "RUN_VALIDATION":
            payload: dict[str, Any] = {"subtype": raw["subtype"]}
            if raw["subtype"] in {"tests", "combined"} and self._config.tests_validation_enabled:
                payload["directive"] = self._render_validation_directive(
                    uncertainty, structural_risk
                )
                payload["forbidden_test_ids"] = list(self._config.forbidden_test_ids or [])
            if raw["subtype"] in {"self_review", "combined"} and self._config.self_review_enabled:
                payload["prompt"] = self._config.self_review_prompt
            return payload
        return {}

    def _render_validation_directive(
        self,
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> str:
        """Renderiza la directiva de validacion `tests` con contexto del paso."""
        diff_summary = structural_risk.diff_summary or {}
        modified_files = diff_summary.get("files_modified") or diff_summary.get("files_changed")
        if isinstance(modified_files, (list, tuple)) and modified_files:
            modified_block = ", ".join(str(f) for f in modified_files)
        else:
            modified_block = "(no detectados)"

        forbidden_ids = list(self._config.forbidden_test_ids or [])
        if forbidden_ids:
            forbidden_block = (
                "\nRestriccion importante:\n"
                "NO debes ejecutar ni inspeccionar tests que coincidan con los siguientes "
                "identificadores: " + ", ".join(forbidden_ids)
            )
        else:
            forbidden_block = ""

        return self._config.validation_directive_template.format(
            modified_files=modified_block,
            uncertainty_level=uncertainty.level,
            uncertainty_score=round(uncertainty.score, 3),
            risk_level=structural_risk.level,
            risk_score=round(structural_risk.score, 3),
            forbidden_block=forbidden_block,
        )

    def _render_feedback(
        self,
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> str:
        """Renderiza el texto de feedback con los niveles y scores del paso."""
        return self._config.feedback_template.format(
            uncertainty_level=uncertainty.level,
            uncertainty_score=round(uncertainty.score, 3),
            risk_level=structural_risk.level,
            risk_score=round(structural_risk.score, 3),
        )

    def _policy_snapshot(
        self,
        uncertainty: UncertaintyResult,
        structural_risk: StructuralRiskResult,
    ) -> dict[str, Any]:
        """Snapshot serializable de la politica aplicada (auditoria)."""
        return {
            "profile": self._profile_name,
            "uncertainty_level": uncertainty.level,
            "uncertainty_score": uncertainty.score,
            "risk_level": structural_risk.level,
            "risk_score": structural_risk.score,
            "policy_matrix": {k: dict(v) for k, v in self._config.policy_matrix.items()},
            "high_high_per_profile": dict(self._config.high_high_per_profile),
            "validation_per_profile": dict(self._config.validation_per_profile),
        }

    def _abort_on_error(self, exc: Exception) -> ControllerDecision:
        """Construye un `FINALIZE(abort)` cuando el controlador falla."""
        self._episode_state.errors.append(
            {
                "component": "controller",
                "error_type": type(exc).__name__,
                "detail": str(exc),
            }
        )
        decision = ControllerDecision(
            action="FINALIZE",
            subtype="abort",
            rationale=f"Excepcion en el controlador: {exc}",
            policy_snapshot={"profile": self._profile_name},
            overrides={"controller_error": str(exc)},
            payload={},
        )
        self._logger.record(decision, step_index=self._episode_state.step_index)
        return decision
