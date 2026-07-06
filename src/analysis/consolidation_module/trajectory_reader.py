"""`TrajectoryReader`: parseo y agregacion de `trajectory.traj.json`.

Encapsula el anidamiento de la traza `robust_agent` (`especificacion_agente.md`
S7-8): cada entrada de `episode_state_final.signals_history` trae **una unica**
senal (`uncertainty` **o** `structural_risk`, nunca ambas) etiquetada con
`step_index`; cada entrada de `episode_state_final.decisions_history` tiene la
forma `{"decision": {...}, "step_index": N}` (el campo `action` cuelga de
`decision`, no del nivel superior). `robust_agent.steps` en cambio trae ambas
senales juntas (`uncertainty` y `structural_risk` como hermanos de `decision`
y `validation_outcome`), por lo que es la fuente mas comoda para construir
`datos_pasos_consolidados.csv` (S7 de la spec general).

Los tokens (`prompt_tokens`/`completion_tokens`/`total_tokens`) se agregan
sobre `messages[].extra.response.usage`, disponibles para **cualquier** agente
con traza (no solo `robust_agent`): `default` tambien produce estos campos.

Sin dependencia de `src/agent`: toda la informacion se obtiene parseando el
JSON de la traza, nunca instanciando clases del Bloque 1.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any

from analysis.consolidation_module.consolidated_step_row import ConsolidatedStepRow

logger = logging.getLogger(__name__)

# Estados de `validation_outcome.status` considerados "exito"/"fallo" para
# `validation_success_count`/`validation_failure_count` (S6.2 "Controlador").
# La spec general no fija los valores exactos del contrato `validation_outcome`
# (ver `src/agent/robust_agent.py::_validation_status`); "skipped" (no se
# ejecuto validacion ese paso) no cuenta ni como exito ni como fallo.
_VALIDATION_SUCCESS_STATUSES = frozenset(
    {"passed", "directive_issued", "directive_issued_with_review"}
)
_VALIDATION_FAILURE_STATUSES = frozenset({"error"})


@dataclass
class TrajectoryMetrics:
    """Campos derivados de una traza, listos para `RowBuilder`."""

    # Disponibles para cualquier agente con traza.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    api_calls: int | None = None

    # Solo disponibles si la traza es `robust_agent`.
    has_robust_agent: bool = False
    termination: str | None = None

    uncertainty_mean: float | None = None
    uncertainty_max: float | None = None
    uncertainty_final: float | None = None
    uncertainty_std: float | None = None
    uncertainty_p95: float | None = None
    uncertainty_high_ratio: float | None = None
    cycle_mean: float | None = None
    failure_rate_mean: float | None = None
    hedging_mean: float | None = None
    volatility_mean: float | None = None
    uncertainty_missing_components: int | None = None

    structural_risk_mean: float | None = None
    structural_risk_max: float | None = None
    structural_risk_final: float | None = None
    structural_risk_p95: float | None = None
    structural_risk_high_ratio: float | None = None
    files_changed_ratio: float | None = None
    hunks_per_file: float | None = None
    net_lines: int | None = None
    cumulative_lines_changed: int | None = None
    surface_final_size: int | None = None

    controller_intervention_count: int | None = None
    proceed_count: int | None = None
    feedback_count: int | None = None
    validation_count: int | None = None
    abort_count: int | None = None
    first_intervention_step: int | None = None
    high_high_encounter_count: int | None = None
    final_controller_action: str | None = None
    validation_success_count: int | None = None
    validation_failure_count: int | None = None


class TrajectoryReader:
    """Parsea `trajectory.traj.json` (ya cargado como dict) y agrega sus campos."""

    def read(self, trajectory_data: dict[str, Any]) -> TrajectoryMetrics:
        """Extrae `TrajectoryMetrics` de una traza ya parseada.

        Parametros
        ----------
        trajectory_data:
            Contenido de `trajectory.traj.json` ya deserializado.

        Retorna
        -------
        `TrajectoryMetrics` con los campos disponibles; los campos exclusivos
        de `robust_agent` quedan a `None` si la traza no trae ese bloque.
        """
        metrics = TrajectoryMetrics()
        self._read_tokens(trajectory_data, metrics)
        self._read_api_calls(trajectory_data, metrics)

        robust_agent = trajectory_data.get("robust_agent")
        if not isinstance(robust_agent, dict):
            return metrics

        metrics.has_robust_agent = True
        metrics.termination = robust_agent.get("termination")

        episode_state = robust_agent.get("episode_state_final") or {}
        self._read_uncertainty(episode_state.get("signals_history") or [], metrics)
        self._read_structural_risk(episode_state.get("signals_history") or [], metrics)
        self._read_controller(episode_state.get("decisions_history") or [], metrics)
        self._read_validation_outcomes(robust_agent.get("steps") or [], metrics)
        return metrics

    def build_step_rows(
        self,
        trajectory_data: dict[str, Any],
        *,
        run_id: str,
        instance_id: str,
        configuration_id: str,
    ) -> list[ConsolidatedStepRow]:
        """Construye `datos_pasos_consolidados.csv` para un run `robust_agent`.

        Devuelve lista vacia si la traza no trae bloque `robust_agent` (p. ej.
        agente `default`, sin instrumentacion de incertidumbre/riesgo).
        """
        robust_agent = trajectory_data.get("robust_agent")
        if not isinstance(robust_agent, dict):
            return []

        rows: list[ConsolidatedStepRow] = []
        for step in robust_agent.get("steps") or []:
            uncertainty = step.get("uncertainty") or {}
            structural_risk = step.get("structural_risk") or {}
            decision = step.get("decision") or {}
            rows.append(
                ConsolidatedStepRow(
                    run_id=run_id,
                    instance_id=instance_id,
                    configuration_id=configuration_id,
                    step_index=step.get("step_id"),
                    uncertainty_score=uncertainty.get("score"),
                    uncertainty_level=uncertainty.get("level"),
                    structural_risk_score=structural_risk.get("score"),
                    structural_risk_level=structural_risk.get("level"),
                    controller_action=decision.get("action"),
                )
            )
        return rows

    # ------------------------------------------------------------------
    # Tokens / api_calls (disponibles para cualquier agente con traza)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_tokens(trajectory_data: dict[str, Any], metrics: TrajectoryMetrics) -> None:
        """Auxiliar interno: read tokens."""
        prompt_total = 0
        completion_total = 0
        total_total = 0
        found_any = False
        for message in trajectory_data.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            usage = ((message.get("extra") or {}).get("response") or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            found_any = True
            prompt_total += usage.get("prompt_tokens") or 0
            completion_total += usage.get("completion_tokens") or 0
            total_total += usage.get("total_tokens") or 0

        if found_any:
            metrics.prompt_tokens = prompt_total
            metrics.completion_tokens = completion_total
            metrics.total_tokens = total_total

    @staticmethod
    def _read_api_calls(trajectory_data: dict[str, Any], metrics: TrajectoryMetrics) -> None:
        """Auxiliar interno: read api calls."""
        model_stats = (trajectory_data.get("info") or {}).get("model_stats") or {}
        metrics.api_calls = model_stats.get("api_calls")

    # ------------------------------------------------------------------
    # Incertidumbre y riesgo estructural (S6.2, solo `robust_agent`)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_uncertainty(
        signals_history: list[dict[str, Any]], metrics: TrajectoryMetrics
    ) -> None:
        """Auxiliar interno: read uncertainty."""
        entries = [s["uncertainty"] for s in signals_history if "uncertainty" in s]
        if not entries:
            return

        scores = [e.get("score") for e in entries if e.get("score") is not None]
        if scores:
            metrics.uncertainty_mean = statistics.fmean(scores)
            metrics.uncertainty_max = max(scores)
            metrics.uncertainty_final = scores[-1]
            metrics.uncertainty_std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
            metrics.uncertainty_p95 = _percentile(scores, 0.95)

        high_count = sum(1 for e in entries if e.get("level") == "high")
        metrics.uncertainty_high_ratio = high_count / len(entries)

        for component, attr in (
            ("cycle", "cycle_mean"),
            ("failure_rate", "failure_rate_mean"),
            ("hedging", "hedging_mean"),
            ("volatility", "volatility_mean"),
        ):
            values = [
                (e.get("components") or {}).get(component)
                for e in entries
                if (e.get("components") or {}).get(component) is not None
            ]
            if values:
                setattr(metrics, attr, statistics.fmean(values))

        last_evidence = entries[-1].get("evidence") or {}
        metrics.uncertainty_missing_components = len(
            last_evidence.get("omitted_components") or []
        )

    @staticmethod
    def _read_structural_risk(
        signals_history: list[dict[str, Any]], metrics: TrajectoryMetrics
    ) -> None:
        """Auxiliar interno: read structural risk."""
        # Solo entradas con contenido real: el ultimo paso del episodio puede
        # traer `structural_risk: {}` (sin cambios incrementales), que no debe
        # contarse como una senal valida.
        entries = [
            s["structural_risk"]
            for s in signals_history
            if s.get("structural_risk")
        ]
        if not entries:
            return

        scores = [e.get("score") for e in entries if e.get("score") is not None]
        if scores:
            metrics.structural_risk_mean = statistics.fmean(scores)
            metrics.structural_risk_max = max(scores)
            metrics.structural_risk_final = scores[-1]
            metrics.structural_risk_p95 = _percentile(scores, 0.95)

        high_count = sum(1 for e in entries if e.get("level") == "high")
        metrics.structural_risk_high_ratio = high_count / len(entries)

        last = entries[-1]
        last_metrics = last.get("metrics") or {}
        metrics.files_changed_ratio = last_metrics.get("files_changed_ratio")
        metrics.hunks_per_file = last_metrics.get("hunks_per_file")
        metrics.net_lines = last_metrics.get("net_lines")

        last_diff_summary = last.get("diff_summary") or {}
        metrics.cumulative_lines_changed = last_diff_summary.get("cumulative_lines_changed")
        files_touched = last_diff_summary.get("files_touched")
        metrics.surface_final_size = len(files_touched) if files_touched is not None else None

    # ------------------------------------------------------------------
    # Controlador (S6.2, solo `robust_agent`)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_controller(
        decisions_history: list[dict[str, Any]], metrics: TrajectoryMetrics
    ) -> None:
        """Auxiliar interno: read controller."""
        if not decisions_history:
            metrics.controller_intervention_count = 0
            metrics.proceed_count = 0
            metrics.feedback_count = 0
            metrics.validation_count = 0
            metrics.abort_count = 0
            metrics.high_high_encounter_count = 0
            return

        proceed = feedback = validation = abort = 0
        intervention = 0
        high_high = 0
        first_intervention_step: int | None = None

        for entry in decisions_history:
            decision = entry.get("decision") or {}
            action = decision.get("action")
            step_index = entry.get("step_index")

            if action == "PROCEED":
                proceed += 1
            else:
                intervention += 1
                if first_intervention_step is None:
                    first_intervention_step = step_index
                if action == "INJECT_FEEDBACK":
                    feedback += 1
                elif action == "RUN_VALIDATION":
                    validation += 1
                elif action == "FINALIZE" and decision.get("subtype") == "abort":
                    abort += 1

            snapshot = decision.get("policy_snapshot") or {}
            if snapshot.get("uncertainty_level") == "high" and snapshot.get("risk_level") == "high":
                high_high += 1

        metrics.controller_intervention_count = intervention
        metrics.proceed_count = proceed
        metrics.feedback_count = feedback
        metrics.validation_count = validation
        metrics.abort_count = abort
        metrics.first_intervention_step = first_intervention_step
        metrics.high_high_encounter_count = high_high
        metrics.final_controller_action = (decisions_history[-1].get("decision") or {}).get("action")

    @staticmethod
    def _read_validation_outcomes(
        steps: list[dict[str, Any]], metrics: TrajectoryMetrics
    ) -> None:
        """Auxiliar interno: read validation outcomes."""
        success = failure = 0
        for step in steps:
            outcome = step.get("validation_outcome")
            if not isinstance(outcome, dict):
                continue
            status = outcome.get("status")
            if status in _VALIDATION_SUCCESS_STATUSES:
                success += 1
            elif status in _VALIDATION_FAILURE_STATUSES:
                failure += 1
        metrics.validation_success_count = success
        metrics.validation_failure_count = failure


def _percentile(values: list[float], pct: float) -> float:
    """Percentil con interpolacion lineal (compatible con `numpy.percentile`)."""
    if not values:
        raise ValueError("values no puede estar vacio")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
