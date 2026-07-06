"""Deteccion de reanudacion parcial del experimento.

Filtra runs ya completados al reanudar y valida que la configuracion y
la seleccion de instancias coincidan con las persistidas en disco.
"""

from __future__ import annotations

from typing import Any

from benchmark.config import ExperimentConfig
from benchmark.results_module.artifact_store import ArtifactStore
from benchmark.results_module.layout import ExperimentLayout, run_directory
from benchmark.results_module.contracts import PlannedRun, RunSlot

TERMINAL_RUN_STATUSES = frozenset({"completed", "precondition_failed"})
"""Estados terminales que no se reintentan en reanudacion.

``failed`` se excluye deliberadamente: los runs fallidos (429 cascade,
Ctrl+C, timeouts) deben reintentarse en la sesion siguiente. Si vuelven a
fallar tras agotar la politica de reintentos, quedan marcados como ``failed``
hasta la proxima sesion.
"""

TERMINAL_EXIT_STATUSES = frozenset({"LimitsExceeded", "EmptySubmission"})
"""Exit statuses que indican un resultado legitimo aunque ``status==failed``.

``LimitsExceeded``: minisweagent agoto el presupuesto de pasos o coste
(ambos limites usan el mismo exit_status). Resultado experimental valido;
reintentarlo produciria lo mismo consumiendo mas tokens.

``EmptySubmission``: el agente llego al submit sin parche util (p. ej. un
``sed`` no matcheo o estancamiento genuino). Tambien es resultado experimental
valido: refleja que ese agente no produjo parche en esa instancia. No se
reintenta en reanudaciones normales; un re-ejecucion selectiva sigue siendo
posible con ``--force-rerun`` sobre el ``run_id`` concreto.

Nota: ``MaxStepsReached`` no existe en minisweagent; ambos limites (steps y
coste) producen ``LimitsExceeded``.
"""
INTERMEDIATE_RUN_STATUSES = frozenset({"running", "aborted_mid"})


class ResumeDetector:
    """Filtra runs ya completados y valida coherencia al reanudar."""

    def __init__(self, layout: ExperimentLayout) -> None:
        """Inicializa `ResumeDetector`."""
        self._layout = layout

    def pending_runs(self, run_list: list[PlannedRun]) -> list[RunSlot]:
        """Devuelve los runs que aun requieren ejecucion."""
        pending: list[RunSlot] = []
        for entry in run_list:
            rdir = run_directory(
                self._layout,
                instance_id=entry.instance_id,
                agent_id=entry.agent_run_config.agent_id,
                run_id=entry.run_id,
            )
            if self._needs_execution(rdir):
                pending.append(
                    RunSlot(
                        instance_id=entry.instance_id,
                        agent_run_config=entry.agent_run_config,
                        run_id=entry.run_id,
                        run_dir=rdir,
                    )
                )
        return pending

    @staticmethod
    def _needs_execution(run_dir) -> bool:  # noqa: ANN001
        """Indica si el directorio de run requiere una nueva ejecucion."""
        record_path = run_dir / "run_record.json"
        if not record_path.exists():
            return True
        record = ArtifactStore.read_json(record_path)
        status = record.get("status")
        # Los estados terminales exitosos no se repiten; los intermedios y
        # los fallidos (transient: 429, Ctrl+C, timeout) sí se reintentan.
        if status in TERMINAL_RUN_STATUSES:
            return False
        # LimitsExceeded/MaxStepsReached: resultado experimental valido aunque
        # el status sea ``failed``. No reintentar — el agente agoto su presupuesto
        # de pasos/coste; volveria a ocurrir lo mismo consumiendo mas tokens.
        exit_status = record.get("exit_status")
        if exit_status in TERMINAL_EXIT_STATUSES:
            return False
        return True

    # Campos excluidos de la comparacion de reanudacion porque son operativos
    # (no afectan a los resultados experimentales) o son flags de CLI.
    #
    # ``evaluation_skip`` se excluye deliberadamente: es precisamente el campo
    # que las flags de CLI ``--evaluation-skip``/``--evaluate`` alternan entre
    # la fase 1 (ejecucion, sin gastar cuota sb-cli) y la fase 2 (evaluacion
    # funcional sobre los runs ya en disco) del mismo experimento. Si se
    # incluyera en la comparacion, ``--evaluate`` abortaria siempre la
    # reanudacion en cualquier experimento cuyo `config.yaml` persistido
    # tuviera `evaluation_skip: true` (ver modulo_resultados.md seccion 6.1).
    _BENCHMARK_OPERATIONAL_KEYS: frozenset[str] = frozenset(
        {"workers", "inter_run_delay_seconds", "disk_min_free_gb", "evaluation_skip"}
    )

    @staticmethod
    def _normalize_model_id(model_id: str) -> str:
        """Normaliza un model_id eliminando el prefijo de proveedor LiteLLM.

        ``vertex_ai/gemini-2.5-flash``, ``gemini/gemini-2.5-flash`` y
        ``gemini-2.5-flash`` hacen referencia al mismo modelo subyacente de
        Google; solo difieren en el endpoint de API utilizado. Para la
        comparacion de reanudacion son equivalentes: cambiar de proveedor no
        altera los resultados del experimento.
        """
        if "/" in model_id:
            return model_id.split("/", 1)[1]
        return model_id

    @staticmethod
    def _normalize_agents(agents: Any) -> Any:
        """Normaliza los model_id de la lista de agentes."""
        if not isinstance(agents, list):
            return agents
        normalized = []
        for agent in agents:
            if isinstance(agent, dict) and "model_id" in agent:
                agent = {**agent, "model_id": ResumeDetector._normalize_model_id(agent["model_id"])}
            normalized.append(agent)
        return normalized

    @staticmethod
    def _normalize_for_compare(config: dict[str, Any]) -> dict[str, Any]:
        """Elimina campos operativos/CLI y normaliza model_ids antes de comparar."""
        result = {k: v for k, v in config.items() if k != "force_rerun"}
        if "benchmark" in result and isinstance(result["benchmark"], dict):
            result["benchmark"] = {
                k: v
                for k, v in result["benchmark"].items()
                if k not in ResumeDetector._BENCHMARK_OPERATIONAL_KEYS
            }
        if "agents" in result:
            result["agents"] = ResumeDetector._normalize_agents(result["agents"])
        return result

    @staticmethod
    def validate_resume(
        layout: ExperimentLayout,
        experiment_config: ExperimentConfig,
        dataset_summary: dict[str, Any],
    ) -> None:
        """Aborta si la config o la seleccion de instancias no coinciden.

        Los campos operativos (``workers``, ``inter_run_delay_seconds``) y el
        flag de CLI ``force_rerun`` se excluyen de la comparacion porque no
        afectan a los resultados del experimento.
        """
        if not layout.config_path.exists():
            raise ValueError(
                f"Reanudacion abortada: falta {layout.config_path.name} en {layout.root}"
            )
        if not layout.dataset_summary_path.exists():
            raise ValueError(
                f"Reanudacion abortada: falta {layout.dataset_summary_path.name} en {layout.root}"
            )
        persisted_config = ArtifactStore.read_yaml(layout.config_path)
        expected_config = experiment_config.model_dump(mode="json")
        persisted_norm = ResumeDetector._normalize_for_compare(persisted_config)
        expected_norm = ResumeDetector._normalize_for_compare(expected_config)
        if persisted_norm != expected_norm:
            raise ValueError(
                "Reanudacion abortada: el `config.yaml` persistido no coincide con la "
                "configuracion actual del experimento."
            )
        persisted_summary = ArtifactStore.read_json(layout.dataset_summary_path)
        expected_ids = list(dataset_summary.get("instance_ids") or [])
        persisted_ids = list(persisted_summary.get("instance_ids") or [])
        if expected_ids != persisted_ids:
            raise ValueError(
                "Reanudacion abortada: la lista de `instance_id` del dataset no coincide "
                "con la seleccion persistida."
            )
