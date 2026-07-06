"""Modulo principal de metricas estructurales."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.config import StructuralMetricsConfig
from agent.episode_state import EpisodeState
from agent.structural_metrics_module.aggregator import RiskAggregator
from agent.structural_metrics_module.computer import StructuralMetricsComputer
from agent.structural_metrics_module.diff_collector import DiffCollector
from agent.structural_metrics_module.structural_risk_result import StructuralRiskResult


class StructuralMetricsModule:
    """Evalua el riesgo estructural por paso sobre diffs git."""

    def __init__(
        self,
        config: StructuralMetricsConfig,
        episode_state: EpisodeState,
        repo_path: Path | str,
        *,
        env: Any | None = None,
    ) -> None:
        """Inicializa dependencias del modulo para un run."""
        self._config = config
        self._episode_state = episode_state
        self._repo_path = Path(repo_path)
        self._collector = DiffCollector(self._repo_path, env=env)
        self._computer = StructuralMetricsComputer(config.test_globs)
        self._aggregator = RiskAggregator(
            weights=config.weights,
            low_threshold=config.low_threshold,
            high_threshold=config.high_threshold,
        )

    def initialize_baseline(self) -> None:
        """Verifica PR1 y fija la referencia git inicial del run."""
        # PR1 antes de iniciar el bucle.
        self._collector.ensure_pr1()
        # Referencia base compartida por incremental/acumulado.
        initial_ref = self._collector.baseline_ref()
        # Propagación del baseline al estado compartido del episodio.
        self._episode_state.git_refs["initial"] = initial_ref
        self._episode_state.git_refs["previous"] = initial_ref
        self._episode_state.git_refs["current"] = initial_ref

    def evaluate(
        self,
        action_outputs: list[dict[str, Any]],
    ) -> StructuralRiskResult:
        """Evalua el riesgo estructural del paso actual.

        Si el calculo falla, degrada limpiamente a un resultado neutro
        (`score=0.5`, `level=medium`) y registra la incidencia en
        `EpisodeState.errors`.
        """
        del action_outputs
        try:
            # Referencias requeridas para el diff del paso.
            initial_ref = self._require_ref("initial")
            previous_ref = self._require_ref("previous")
            # Referencia estable del estado actual del workspace.
            current_ref = self._collector.materialize_step_ref()
            # Diffs incremental y acumulado.
            payload = self._collector.collect(previous_ref=previous_ref, current_ref=current_ref, initial_ref=initial_ref)
            # Métrica base y resumen de diff.
            metrics, diff_summary = self._computer.compute(
                payload,
                tracked_files_count=self._collector.tracked_files_count(),
            )
            # Avance de referencias para el siguiente paso.
            self._episode_state.git_refs["current"] = current_ref
            self._episode_state.git_refs["previous"] = current_ref
            # Score/level final y persistencia de la señal del paso.
            result = self._aggregator.aggregate(metrics=metrics, diff_summary=diff_summary, evidence={})
            self._episode_state.signals_history.append(
                {
                    "structural_risk": result.to_dict(),
                    "step_index": self._episode_state.step_index,
                }
            )
            return result
        except Exception as exc:
            # Fallback de degradación limpia cuando falla el cálculo estructural.
            error_payload = {"component": "structural_metrics", "error_type": type(exc).__name__, "detail": str(exc)}
            self._episode_state.errors.append(error_payload)
            neutral = StructuralRiskResult(
                score=0.5,
                level="medium",
                metrics={
                    "files_changed": 0,
                    "files_changed_ratio": 0.0,
                    "hunks": 0,
                    "hunks_per_file": 0.0,
                    "lines_added": 0,
                    "lines_deleted": 0,
                    "net_lines": 0,
                    "dispersion_score": 0.0,
                    "test_touch_ratio": 0.0,
                },
                diff_summary={
                    "files_touched": [],
                    "extension_counts": {},
                    "hunks": 0,
                    "surface_signature": "",
                    "cumulative_surface_signature": "",
                    "cumulative_lines_changed": 0,
                },
                cost_overhead=0.0,
                evidence={"error": error_payload},
            )
            self._episode_state.signals_history.append(
                {
                    "structural_risk": neutral.to_dict(),
                    "step_index": self._episode_state.step_index,
                }
            )
            return neutral

    def get_cumulative_patch(self, initial_ref: str) -> str:
        """Devuelve el diff acumulado en texto desde `initial_ref` hasta el estado actual.

        Delega en `DiffCollector.get_cumulative_patch`. Devuelve cadena
        vacía ante cualquier error para no bloquear la salida del run.
        """
        return self._collector.get_cumulative_patch(initial_ref)

    def cleanup(self) -> None:
        """Limpia estado auxiliar del modulo.

        En esta implementacion no se crean refs persistentes (se usa
        `git stash create`), por lo que no hay recursos git que retirar.
        """
        return None

    def _require_ref(self, key: str) -> str:
        """Obtiene una referencia git del estado o lanza error si falta."""
        ref = self._episode_state.git_refs.get(key)
        if not ref:
            raise RuntimeError(f"Referencia git ausente: {key}")
        return ref
