"""Subclase `RobustAgent` del bloque del agente robusto.

Subclase de `DefaultAgent` de `mini-SWE-agent` que envuelve su bucle con
los tres hooks del bloque del agente robusto y materializa la traza
extendida descrita en `especificacion_agente.md`:

- `query()` notifica el coste al `BudgetGuard` y evalua incertidumbre
  tras la respuesta del modelo.
- `execute_actions()` evalua riesgo estructural tras la ejecucion.
- `step()` invoca al controlador, aplica la decision resultante
  (`PROCEED`, `INJECT_FEEDBACK`, `RUN_VALIDATION` o `FINALIZE`) y
  registra el `StepTrace` del paso.
- `serialize()` produce el bloque `robust_agent` (formato
  `robust-agent-1.0`) con la lista ordenada de `StepTrace`.

PR1 se verifica en `run()`, antes del primer paso, y produce salida
controlada `PreconditionFailed` si no se cumple. El `EpisodeState` es
compartido por los modulos y es el almacen unico del run.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from minisweagent.agents.default import DefaultAgent

from agent.config import RobustAgentConfig
from agent.episode_state import EpisodeState, Profile
from agent.controller_module import ControllerDecision, ControllerModule
from agent.step_trace import StepTrace
from agent.structural_metrics_module import StructuralMetricsModule, StructuralRiskResult
from agent.uncertainty_module import UncertaintyModule, UncertaintyResult

if TYPE_CHECKING:
    from minisweagent import Environment, Model

# Version de la traza del agente robusto
ROBUST_TRACE_FORMAT_VERSION = "robust-agent-1.0"

_EXIT_STATUS_TO_TERMINATION: dict[str, str] = {
    "Submitted": "model_submitted",
    "LimitsExceeded": "limits_exceeded",
    "PreconditionFailed": "precondition_failed",
    "ControllerFinalize": "controller_finalize",
}


class RobustAgent(DefaultAgent):
    """Agente robusto del TFG. Clase heredada del DefaultAgent del mini-swe-agent"""

    config: RobustAgentConfig
    episode_state: EpisodeState
    structural_metrics_module: StructuralMetricsModule
    uncertainty_module: UncertaintyModule
    controller_module: ControllerModule

    def __init__(
        self,
        model: "Model",
        env: "Environment",
        *,
        config_class: type = RobustAgentConfig,
        **kwargs: Any,
    ) -> None:
        """Inicializa el agente delegando en `DefaultAgent` y crea el `EpisodeState`.

        Acepta los mismos kwargs que `RobustAgentConfig` (incluidos los
        heredados de `AgentConfig`). Construye los tres modulos del
        bloque del agente y los conecta al estado compartido del run.
        """
        super().__init__(model, env, config_class=config_class, **kwargs)
        self.episode_state = EpisodeState(profile=Profile(name=self.config.profile))
        self.episode_state.budget.cost_limit = self.config.cost_limit
        self.episode_state.budget.step_limit = self.config.step_limit

        # Creación y configuración de los modulos
        self.structural_metrics_module = StructuralMetricsModule(
            config=self.config.structural_metrics,
            episode_state=self.episode_state,
            repo_path=self._resolve_repo_path(),
            env=self.env,
        )
        self.uncertainty_module = UncertaintyModule(
            config=self.config.uncertainty,
            episode_state=self.episode_state,
        )
        self.controller_module = ControllerModule(
            config=self.config.controller,
            episode_state=self.episode_state,
        )
        # Lista ordenada de StepTrace (spec general 9: `steps`).
        self._step_records: list[StepTrace] = []
        # Acumuladores por paso para construir el StepTrace al final del mismo.
        self._reset_step_accumulators()

    # -------------------------- OVERRIDE RUN METHOD --------------------------

    def run(self, task: str = "", **kwargs) -> dict:
        """Override del metodo de DefaultAgent.

        Establece el baseline del modulo de metricas estructurales,
        ejecuta el bucle base y limpia recursos. Si la precondicion falla, termina
        el run con PreconditionFailed antes de cualquier llamada al
        modelo.
        """
        try:
            # Establece la baseline del modulo de metricas estructurales
            self.structural_metrics_module.initialize_baseline()
        except Exception as exc:
            return self._exit_precondition_failed(exc)
        try:
            # Ejecución del bucle base con el metodo de DefaultAgent
            return super().run(task, **kwargs)
        finally:
            # Limpieza de recursos
            self.structural_metrics_module.cleanup()

    # -------------------------- OVERRIDE QUERY METHOD --------------------------
    def query(self) -> dict:
        """Override del metodo de DefaultAgent.

        Llama al modelo, imputa el coste de la llamada al BudgetGuard
        con atribucion agent.query y, tras la respuesta, evalúa la
        incertidumbre del paso.
        """
        # Ejecución del metodo de DefaultAgent (puede lanzar LimitsExceeded
        # antes de incrementar n_calls; si lo hace, el paso no se inicia).
        message = super().query()
        # Marca de inicio efectivo del paso para el StepTrace.
        self._current_step_started = True

        # Imputacion del coste de la llamada al BudgetGuard
        cost = float(message.get("extra", {}).get("cost", 0.0))
        self.controller_module.budget_guard.notify_cost(cost, attribution="agent.query")

        # Calculo de la ventana reciente: los ultimos mensajes excluyendo el que acabamos de recibir.
        window = self.config.uncertainty.history_window
        if window > 0 and len(self.messages) > 1:
            recent_history = list(self.messages[-(window + 1) : -1])
        else:
            recent_history = []

        # Evaluación de la incertidumbre
        result = self.uncertainty_module.evaluate(message=message, recent_history=recent_history)
        self._current_step_uncertainty = result
        self.episode_state.signals_history.append(
            {
                "uncertainty": result.to_dict(),
                "step_index": self.n_calls,
            }
        )
        return message

    # ---------------------- OVERRIDE EXECUTE ACTIONS METHOD ----------------------
    def execute_actions(self, message: dict) -> list[dict]:
        """Ejecuta acciones y evalua riesgo estructural post-ejecucion."""
        # Ejecución del metodo de DefaultAgent (puede lanzar Submitted desde el entorno).
        outputs = super().execute_actions(message)
        # Actualización del indice de paso
        self.episode_state.step_index = self.n_calls
        # Evaluación del riesgo estructural post-ejecucion
        result = self.structural_metrics_module.evaluate(action_outputs=outputs)
        self._current_step_structural_risk = result
        return outputs

    # -------------------------- OVERRIDE STEP METHOD --------------------------
    def step(self) -> list[dict]:
        """Override del metodo de DefaultAgent.

        Reinicia los acumuladores del paso, ejecuta el bucle base,
        invoca al controlador cuando hay señales suficientes y registra
        un StepTrace en finally (parcial si el paso aborta por
        Submitted/LimitsExceeded).
        """
        self._reset_step_accumulators()
        self._current_step_timestamp = time.time()
        self._step_errors_baseline = len(self.episode_state.errors)
        try:
            # Ejecución del metodo de DefaultAgent (query + execute_actions).
            outputs = super().step()
            # Si los hooks no completaron, no hay senales para el controlador.
            if (
                self._current_step_uncertainty is None
                or self._current_step_structural_risk is None
            ):
                return outputs
            # Invocación del controlador
            decision = self.controller_module.decide(
                uncertainty=self._current_step_uncertainty,
                structural_risk=self._current_step_structural_risk,
            )
            self._current_step_decision = decision
            # Aplicación de la decisión
            self._apply_decision(decision)
            return outputs
        finally:
            self._record_step_trace_if_started()

    def _apply_decision(self, decision: ControllerDecision) -> None:
        """Aplica el ControllerDecision sobre el bucle del agente."""
        if decision.action == "PROCEED":
            return
        if decision.action == "INJECT_FEEDBACK":
            self.add_messages(
                self.model.format_message(
                    role="user",
                    content=decision.payload.get("feedback_text", ""),
                )
            )
            return
        if decision.action == "RUN_VALIDATION":
            self._run_validation(decision)
            return
        if decision.action == "FINALIZE":
            submission = self._collect_submission_for_finalize(decision)
            self.add_messages(
                self.model.format_message(
                    role="exit",
                    content=decision.rationale,
                    extra={
                        "exit_status": "ControllerFinalize",
                        "submission": submission,
                        "decision": decision.to_dict(),
                    },
                )
            )

    def _collect_submission_for_finalize(self, decision: ControllerDecision) -> str:
        """Recoge el parche acumulado cuando el controlador decide ``FINALIZE(submit)``.

        Para ``subtype='submit'`` obtiene el diff real desde la referencia
        base del run hasta el estado actual del repositorio. Para cualquier
        otro subtipo (``abort``, etc.) devuelve cadena vacía.
        """
        if decision.subtype != "submit":
            return ""
        initial_ref = self.episode_state.git_refs.get("initial", "")
        if not initial_ref:
            return ""
        return self.structural_metrics_module.get_cumulative_patch(initial_ref)

    def _run_validation(self, decision: ControllerDecision) -> None:
        """Aplica RUN_VALIDATION segun subtipo efectivo y registra validation_outcome.

        Validacion `tests` (modelo dirigido, spec general 5.4):
            inyecta una directiva en el historial del agente (con la lista
            opcional de `forbidden_test_ids` rendida como bloque de
            restriccion). El agente decide en su siguiente paso natural
            si ejecutar tests existentes del repo o crear y ejecutar tests
            nuevos. **No se invoca al modelo ni se ejecutan acciones
            durante este paso**: el coste de la directiva es 0 y la
            ejecucion real corre por el bucle estandar del agente. El
            `validation_outcome` registra la directiva inyectada con
            `status=directive_issued` para auditoria.

        Validacion `self_review`:
            inyecta el prompt y lanza una llamada extra al modelo cuya
            respuesta se persiste en el historial. El coste se imputa al
            `BudgetGuard` con atribucion `validation.self_review`.

        Validacion `combined`:
            inyecta primero la directiva de tests (sin query) y a
            continuacion ejecuta el self-review.
        """
        subtype = decision.subtype
        directive = decision.payload.get("directive")
        forbidden = list(decision.payload.get("forbidden_test_ids") or [])
        prompt = decision.payload.get("prompt")

        # Acumuladores locales para construir validation_outcome al final.
        tests_evidence: dict[str, Any] | None = None
        review_evidence: dict[str, Any] | None = None
        review_cost: float = 0.0

        # Componente `tests` o tramo `tests` de `combined` (modelo dirigido).
        if subtype in {"tests", "combined"} and directive:
            self.add_messages(self.model.format_message(role="user", content=directive))
            tests_evidence = {
                "directive": directive,
                "forbidden_test_ids": forbidden,
            }

        # Componente `self_review` o tramo `self_review` de `combined`.
        if subtype in {"self_review", "combined"} and prompt:
            self.add_messages(self.model.format_message(role="user", content=prompt))
            response = self.model.query(self.messages)
            review_cost = float(response.get("extra", {}).get("cost", 0.0))
            self.controller_module.budget_guard.notify_cost(
                review_cost, attribution="validation.self_review"
            )
            self.add_messages(response)
            review_evidence = {
                "prompt": prompt,
                "review_received": True,
                "cost": review_cost,
            }

        outcome = self._build_validation_outcome(
            subtype=subtype,
            tests_evidence=tests_evidence,
            review_evidence=review_evidence,
            review_cost=review_cost,
        )
        self._current_step_validation_outcome = outcome
        self.episode_state.validation_state = outcome

    def _build_validation_outcome(
        self,
        *,
        subtype: str | None,
        tests_evidence: dict[str, Any] | None,
        review_evidence: dict[str, Any] | None,
        review_cost: float,
    ) -> dict[str, Any]:
        """Construye el contrato `validation_outcome` (spec general 8.1)."""
        # Status efectivo a partir de los componentes ejecutados.
        status = self._validation_status(subtype, tests_evidence, review_evidence)
        # Resumen breve del resultado.
        summary = self._validation_summary(subtype, tests_evidence, review_evidence, status)
        # Evidence agregada de los componentes que se ejecutaron.
        evidence: dict[str, Any] = {}
        if tests_evidence is not None:
            evidence.update(tests_evidence)
        if review_evidence is not None:
            evidence.update(review_evidence)
        return {
            "subtype": subtype,
            "status": status,
            "summary": summary,
            "cost": review_cost,
            "evidence": evidence,
        }

    @staticmethod
    def _tests_status(tests_evidence: dict[str, Any] | None) -> str:
        """Discretiza el resultado del componente `tests` (modelo dirigido).

        En el modelo de validacion dirigida, `tests` solo inyecta una
        directiva en el historial; la ejecucion real ocurre en pasos
        posteriores del agente. Por tanto el estado de este paso es
        `directive_issued` cuando hay evidencia (directiva inyectada) y
        `skipped` cuando no.
        """
        if tests_evidence is None:
            return "skipped"
        return "directive_issued"

    @staticmethod
    def _review_status(review_evidence: dict[str, Any] | None) -> str:
        """Discretiza el resultado del componente `self_review`."""
        if review_evidence is None:
            return "skipped"
        return "passed" if review_evidence.get("review_received") else "error"

    def _validation_status(
        self,
        subtype: str | None,
        tests_evidence: dict[str, Any] | None,
        review_evidence: dict[str, Any] | None,
    ) -> str:
        """Combina los status individuales segun el subtipo efectivo."""
        if subtype == "tests":
            return self._tests_status(tests_evidence)
        if subtype == "self_review":
            return self._review_status(review_evidence)
        if subtype == "combined":
            tests_status = self._tests_status(tests_evidence)
            review_status = self._review_status(review_evidence)
            # Errores en self_review propagan; si la directiva no se inyecto,
            # el subtipo combined queda como skipped.
            if review_status == "error":
                return "error"
            if tests_status == "skipped" and review_status == "skipped":
                return "skipped"
            if review_status == "passed" and tests_status == "directive_issued":
                return "directive_issued_with_review"
            if tests_status == "directive_issued":
                return "directive_issued"
            return review_status

        return "skipped"

    @staticmethod
    def _validation_summary(
        subtype: str | None,
        tests_evidence: dict[str, Any] | None,
        review_evidence: dict[str, Any] | None,
        status: str,
    ) -> str:
        """Resumen breve y legible del resultado de validacion."""
        parts: list[str] = []
        if tests_evidence is not None:
            forbidden = tests_evidence.get("forbidden_test_ids") or []
            parts.append(f"tests directive issued (forbidden={len(forbidden)})")
        if review_evidence is not None:
            parts.append("self-review completed")
        if not parts:
            return f"validation skipped (subtype={subtype}, status={status})"
        parts.append(f"status={status}")
        return "; ".join(parts)

    # -------------------------- STEP TRACE BOOKKEEPING --------------------------
    def _reset_step_accumulators(self) -> None:
        """Reinicia los acumuladores per-paso usados para construir StepTrace.

        Se invoca al inicio de cada step() y al construir el agente
        para garantizar que un fallo en un paso no contamina el
        siguiente con señales obsoletas del paso anterior.
        """
        self._current_step_started: bool = False
        self._current_step_timestamp: float = 0.0
        self._current_step_uncertainty: UncertaintyResult | None = None
        self._current_step_structural_risk: StructuralRiskResult | None = None
        self._current_step_decision: ControllerDecision | None = None
        self._current_step_validation_outcome: dict[str, Any] | None = None
        self._step_errors_baseline: int = 0

    def _record_step_trace_if_started(self) -> None:
        """Registra el StepTrace del paso actual si llego a iniciarse.

        Se llama desde el finally de step(). Si el paso no llego a
        iniciarse, no se emite
        ningun StepTrace. Si el paso se inicio pero abortó (p. ej.
        Submitted en execute_actions), se emite un StepTrace
        parcial sin decision.
        """
        if not self._current_step_started:
            return
        # Sincronizamos el snapshot de pasos antes de tomar el budget_state del paso.
        self.episode_state.budget.steps_used = self.n_calls
        # Errores no fatales acumulados durante el paso.
        step_errors = list(self.episode_state.errors[self._step_errors_baseline:])
        # Construcción del contrato.
        trace = StepTrace(
            step_id=self.n_calls,
            timestamp=self._current_step_timestamp,
            uncertainty=(
                self._current_step_uncertainty.to_dict()
                if self._current_step_uncertainty is not None
                else {}
            ),
            structural_risk=(
                self._current_step_structural_risk.to_dict()
                if self._current_step_structural_risk is not None
                else {}
            ),
            decision=(
                self._current_step_decision.to_dict()
                if self._current_step_decision is not None
                else None
            ),
            validation_outcome=self._current_step_validation_outcome,
            budget_state=self.episode_state.budget.to_snapshot(),
            git_refs=dict(self.episode_state.git_refs),
            errors=step_errors,
        )
        self._step_records.append(trace)

    # -------------------------- OVERRIDE SERIALIZE METHOD --------------------------
    def serialize(self, *extra_dicts: dict) -> dict:
        """Override del metodo de `DefaultAgent`.

        Mantiene intacto el formato base `mini-swe-agent-1.1` producido
        por `DefaultAgent.serialize()` y agrega bajo la clave
        `robust_agent` la informacion comprometida por la spec general 9
        (format_version, profile, episode_state_final, steps,
        termination), siendo `steps` la lista ordenada de `StepTrace`
        emitidos paso a paso.
        """
        # Sincronizar steps_used con el contador del agente base
        self.episode_state.budget.steps_used = self.n_calls

        robust_block = {
            "robust_agent": {
                "format_version": ROBUST_TRACE_FORMAT_VERSION,
                "profile": {
                    "name": self.episode_state.profile.name,
                },
                "episode_state_final": self.episode_state.to_snapshot(),
                "steps": [trace.to_dict() for trace in self._step_records],
                "termination": self._infer_termination(),
            }
        }
        return super().serialize(robust_block, *extra_dicts)

    def _infer_termination(self) -> str | None:
        """Deduce la via de terminacion a partir del `exit_status` del ultimo mensaje.

        Devuelve uno de los valores comprometidos por la spec general 9
        (`model_submitted`, `limits_exceeded`, `controller_finalize`,
        `precondition_failed`, `exception`) o `None` si el run todavia
        no ha terminado.
        """
        if not self.messages:
            return None
        last = self.messages[-1]
        if last.get("role") != "exit":
            return None
        exit_status = last.get("extra", {}).get("exit_status", "")
        if not exit_status:
            return None
        return _EXIT_STATUS_TO_TERMINATION.get(exit_status, "exception")

    def _resolve_repo_path(self) -> Path:
        """Resuelve el repositorio objetivo desde `env.config.cwd` o CWD actual."""
        env_cwd = getattr(getattr(self.env, "config", None), "cwd", "")
        return Path(env_cwd) if env_cwd else Path.cwd()

    def _exit_precondition_failed(self, exc: Exception) -> dict:
        """Registra salida controlada cuando falla PR1 antes del primer paso."""
        message = self.model.format_message(
            role="exit",
            content="PreconditionFailed",
            extra={"exit_status": "PreconditionFailed", "submission": "", "exception_str": str(exc)},
        )
        self.messages = [message]
        self.save(self.config.output_path)
        return message.get("extra", {})
