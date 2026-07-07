"""Fachada de alto nivel del Bloque 2 (`BenchmarkRunner`).

Orquesta los cuatro modulos del benchmark:

  1. `load_instances` + construir lista experimental.
  2. **Preflight `sb-cli`** (si `benchmark.functional_backend == "sb_cli"`).
  3. `results_module.initialize(matrix_size=...)`.
  4. `mark_running()`.
  5. `execution_module.run_matrix(...)` — solo slots pendientes.
  6. `evaluation_module.evaluate_runs(...)` — idempotente.
  7. `results_module.finalize(...)`.

Excepciones en cascada: cualquier fallo entre `initialize()` y `finalize()`
se traduce en `mark_aborted(reason)` y se re-propaga. El preflight que falla
**antes** de `initialize()` propaga directo (no hay layout en disco que
abortar).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from benchmark.config import AgentRunConfig, ExperimentConfig
from benchmark.dataset_module import BenchmarkDatasetModule, BenchmarkInstance
from benchmark.evaluation_module import BenchmarkEvaluationModule
from benchmark.evaluation_module.sb_cli_client import SbCliClient
from benchmark.execution_module import BenchmarkExecutionModule, BenchmarkRunRecord
from benchmark.results_module import BenchmarkExport, BenchmarkResultsModule
from benchmark.results_module.layout import run_directory
from benchmark.run_progress import EvalSummary, RunProgress, _print
from benchmark.shutdown import request_stop
from common.env import swebench_api_key_error


class BenchmarkRunner:
    """Punto de entrada unico del Bloque 2.

    Las dependencias son inyectables para tests; en uso normal se omiten y el
    runner las construye con sus defaults.
    """

    def __init__(
        self,
        experiment_config: ExperimentConfig,
        *,
        results_module: BenchmarkResultsModule | None = None,
        dataset_module: BenchmarkDatasetModule | None = None,
        execution_module: BenchmarkExecutionModule | None = None,
        evaluation_module: BenchmarkEvaluationModule | None = None,
        sb_cli_client: SbCliClient | None = None,
    ) -> None:
        """Instancia los cuatro modulos con la configuracion del experimento.

        ``sb_cli_client`` se inyecta en tests para controlar el preflight. En
        uso normal se construye con el binario `sb-cli` del sistema.
        """
        self._experiment_config = experiment_config
        _validate_experiment_config(experiment_config)

        self._sb_cli_client = sb_cli_client or SbCliClient()

        self.results_module = results_module or BenchmarkResultsModule(
            experiment_config=experiment_config,
            runs_root=experiment_config.benchmark.runs_root,
            experiment_id_explicit=experiment_config.experiment_id_explicit,
            force_rerun=experiment_config.force_rerun,
        )

        self.dataset_module = dataset_module or BenchmarkDatasetModule(
            config=experiment_config.benchmark
        )

        self.execution_module = execution_module or BenchmarkExecutionModule(
            config=experiment_config.benchmark,
            results_module=self.results_module,
        )

        self.evaluation_module = evaluation_module or BenchmarkEvaluationModule(
            config=experiment_config.benchmark,
            results_module=self.results_module,
            sb_cli_client=self._sb_cli_client,
        )

    # ------------------------------------------------------------------
    # API publica

    @property
    def experiment_id(self) -> str:
        """Identificador del experimento resuelto en construccion."""
        return self.results_module.experiment_id

    def run(
        self,
        *,
        only_agent_ids: frozenset[str] | None = None,
        evaluation_only: bool = False,
        force_reevaluate_agents: frozenset[str] | None = None,
    ) -> BenchmarkExport:
        """Ejecuta el lote completo y devuelve el `BenchmarkExport`.

        Flujo:
          1. Cargar dataset y construir lista experimental.
          2. Preflight sb-cli (aborta el lote si no esta disponible).
          3. Inicializar layout + manifiesto.
          4. Ejecutar runs pendientes (salvo ``evaluation_only``).
          5. Evaluar (idempotente).
          6. Finalizar.

        ``only_agent_ids``: si se especifica, la fase de evaluacion (paso 5)
        solo procesa los `BenchmarkRunRecord` cuyo `agent_id` este en el
        conjunto dado; el resto se deja tal cual (ni se re-evalua ni se
        toca su `evaluation_result.json`). No afecta a `ExperimentConfig`
        (no se persiste en `config.yaml`, no interviene en la validacion de
        reanudacion): es puramente un filtro de esta invocacion, pensado
        para someter a `sb-cli` un perfil/ablacion a la vez y controlar el
        gasto de cuota (ver docs_propios/problemas_encontrados.md). El
        `manifest.json` de esta pasada reflejara solo los runs procesados;
        una pasada final sin filtro corrige `runs_completed` al total real.

        ``evaluation_only``: si es ``True``, el paso 4 (`execution_module.
        run_matrix`) se omite por completo — no se ejecuta ningun agente, no
        se levanta ningun contenedor Docker, no se llama al LLM. Solo se
        recopilan los `BenchmarkRunRecord` ya persistidos en disco (via
        `_collect_all_records`) para pasarlos a evaluacion. Es indispensable
        para que la fase 2 (`--evaluate`) sea realmente "solo evalua lo ya en
        disco": sin este flag, cualquier run no terminal (p. ej. `failed` por
        Ctrl+C o timeout) se trataria como pendiente y se re-ejecutaria de
        verdad -consumiendo cuota del LLM y tiempo- durante lo que se creia una
        pasada de solo evaluacion. Ver `docs_propios/problemas_encontrados.md`
        P18. (`EmptySubmission` es terminal desde P20 y ya no entra en ese
        riesgo.)

        ``force_reevaluate_agents``: agentes para los que se ignora un
        `functional.status == "evaluated"` cacheado y se vuelve a someter a
        `sb-cli` bajo un `run_id` nuevo (sufijo de reintento). Uso
        excepcional y deliberado: gasta cuota de nuevo en un grupo que
        `sb-cli` ya marco como "evaluado" pero cuyo resultado se sospecha
        basura del backend (p. ej. 100% `Failed runs` sin fallo tecnico
        propio) — ver `docs_propios/problemas_encontrados.md` P19.
        """
        instances = self.dataset_module.load_instances(self._experiment_config.dataset)
        self.results_module.set_dataset_summary(self.dataset_module.selection_summary())
        run_list = BenchmarkExecutionModule.build_run_list(self._experiment_config, instances)

        _preflight_sb_cli(
            self._sb_cli_client,
            functional_backend=self._experiment_config.benchmark.functional_backend,
            evaluation_skip=self._experiment_config.benchmark.evaluation_skip,
        )
        if not evaluation_only:
            _preflight_docker(self._experiment_config)

        total_runs = len(run_list)
        self.results_module.initialize(matrix_size=total_runs)
        pending_runs = self.results_module.pending_runs(run_list)
        already_done = total_runs - len(pending_runs)
        progress = RunProgress(total=total_runs, already_done=already_done)

        try:
            self.results_module.mark_running()
            new_records = (
                []
                if evaluation_only
                else self.execution_module.run_matrix(
                    self._experiment_config, instances, progress=progress
                )
            )
            all_records = self._collect_all_records(
                instances=instances, new_records=new_records
            )
            records_for_eval = (
                [r for r in all_records if r.agent_run_config.agent_id in only_agent_ids]
                if only_agent_ids is not None
                else all_records
            )
            bench_cfg = self._experiment_config.benchmark
            if (
                not bench_cfg.evaluation_skip
                and bench_cfg.functional_backend != "none"
                and records_for_eval
            ):
                eval_groups = {
                    (r.agent_run_config.agent_id, r.agent_run_config.model_id)
                    for r in records_for_eval
                }
                _print(
                    f"\n[benchmark] Iniciando evaluacion funcional: "
                    f"{len(eval_groups)} grupo(s), {len(records_for_eval)} runs..."
                )
                if force_reevaluate_agents:
                    _print(
                        "[benchmark] Reintento forzado sb-cli para: "
                        + ", ".join(sorted(force_reevaluate_agents))
                    )
            task_results = self.evaluation_module.evaluate_runs(
                records_for_eval,
                instances,
                self._experiment_config,
                force_agent_ids=force_reevaluate_agents,
            )
            export = self.results_module.finalize(list(task_results.values()))
            eval_summary = _build_eval_summary(
                task_results=task_results if isinstance(task_results, dict) else {},
                experiment_config=self._experiment_config,
                sb_cli_client=self._sb_cli_client,
            )
            progress.print_summary(all_records, eval_summary=eval_summary)
            return export
        except KeyboardInterrupt:
            request_stop()
            try:
                BenchmarkExecutionModule._cleanup_orphaned_containers()
                self.results_module.mark_aborted("KeyboardInterrupt: experimento interrumpido por el usuario")
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            reason = f"{exc.__class__.__name__}: {exc}"
            try:
                self.results_module.mark_aborted(reason)
            except Exception:  # noqa: BLE001
                pass
            raise

    # ------------------------------------------------------------------
    # Helpers

    def _collect_all_records(
        self,
        *,
        instances: list[BenchmarkInstance],
        new_records: list[BenchmarkRunRecord],
    ) -> list[BenchmarkRunRecord]:
        """Une los records nuevos con los persistidos en intentos previos."""
        new_run_ids = {rec.run_id for rec in new_records}
        previous = _load_previous_records(
            results_module=self.results_module,
            experiment_config=self._experiment_config,
            instances=instances,
            exclude_run_ids=new_run_ids,
        )
        for record in previous:
            self.results_module.register_existing_run(
                instance_id=record.instance_id,
                agent_id=record.agent_run_config.agent_id,
                run_id=record.run_id,
            )
        return list(new_records) + list(previous)


# ----------------------------------------------------------------------
# Helpers de modulo


def _preflight_sb_cli(
    client: SbCliClient, *, functional_backend: str, evaluation_skip: bool = False
) -> None:
    """Comprueba disponibilidad de `sb-cli` antes de inicializar el layout.

    Solo se ejecuta si `functional_backend == "sb_cli"` y `evaluation_skip` es
    ``False``. Si el binario no esta disponible, lanza `RuntimeError` y el runner **no**
    inicializa el layout (no hace falta `mark_aborted`).
    """
    if functional_backend != "sb_cli" or evaluation_skip:
        return
    if not client.is_available():
        raise RuntimeError(
            "sb-cli no esta disponible en el PATH. "
            "Instala sb-cli o usa `functional_backend='none'` para deshabilitar "
            "la evaluacion funcional."
        )
    if (key_err := swebench_api_key_error()) is not None:
        raise RuntimeError(key_err)


def _preflight_docker(cfg: ExperimentConfig) -> None:
    """Comprueba que Docker está disponible antes de inicializar el layout.

    Solo se ejecuta si al menos un agente usa ``DockerEnvironment``. Falla
    rápido con mensaje claro si Docker Desktop no está arrancado o el binario
    no está en el PATH.
    """
    uses_docker = any(
        "docker" in (a.environment_class or "").lower()
        for a in cfg.agents
    )
    if not uses_docker:
        return

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Docker Desktop no está arrancado o no responde.\n"
                "Arranca Docker Desktop y vuelve a intentarlo.\n"
                f"(docker info salió con código {result.returncode})"
            )
    except FileNotFoundError:
        raise RuntimeError(
            "El binario 'docker' no está en el PATH.\n"
            "Instala Docker Desktop: https://www.docker.com/products/docker-desktop/"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Docker no respondió en 10 segundos.\n"
            "Comprueba que Docker Desktop está arrancado y funcionando."
        ) from None


def _validate_experiment_config(cfg: ExperimentConfig) -> None:
    """Validacion cruzada minima del `ExperimentConfig`."""
    if not cfg.agents:
        raise ValueError(
            "ExperimentConfig.agents no puede estar vacio: la matriz experimental requiere "
            "al menos una `AgentRunConfig`."
        )
    seen: set[tuple[str, str, int | None]] = set()
    for agent in cfg.agents:
        key = (agent.agent_id, agent.model_id, agent.seed)
        if key in seen:
            raise ValueError(
                "ExperimentConfig.agents contiene celdas duplicadas en la matriz: "
                f"(agent_id={agent.agent_id}, model_id={agent.model_id}, seed={agent.seed})."
            )
        seen.add(key)


def _load_previous_records(
    *,
    results_module: BenchmarkResultsModule,
    experiment_config: ExperimentConfig,
    instances: list[BenchmarkInstance],
    exclude_run_ids: set[str],
) -> list[BenchmarkRunRecord]:
    """Reconstruye `BenchmarkRunRecord`s persistidos en intentos previos."""
    root = results_module.export_path()
    instances_root = root / "instances"
    if not instances_root.is_dir():
        return []

    instance_index = {inst.instance_id: inst for inst in instances}
    agent_index = {cfg.agent_id: cfg for cfg in experiment_config.agents}

    out: list[BenchmarkRunRecord] = []
    for instance_dir in sorted(instances_root.iterdir()):
        if not instance_dir.is_dir():
            continue
        instance_id = instance_dir.name
        if instance_id not in instance_index:
            continue
        for agent_dir in sorted(instance_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            cfg = agent_index.get(agent_dir.name)
            if cfg is None:
                continue
            for run_dir in sorted(agent_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                if run_dir.name in exclude_run_ids:
                    continue
                record = _read_run_record(run_dir / "run_record.json", agent_run_config=cfg)
                if record is not None:
                    out.append(record)
    return out


def _read_run_record(
    path: Path, *, agent_run_config: AgentRunConfig
) -> BenchmarkRunRecord | None:
    """Deserializa un `run_record.json`. Devuelve `None` si no se puede leer."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return BenchmarkRunRecord(
        run_id=payload.get("run_id", ""),
        instance_id=payload.get("instance_id", ""),
        agent_run_config=agent_run_config,
        status=payload.get("status", "failed"),
        started_at=float(payload.get("started_at", 0.0)),
        ended_at=float(payload.get("ended_at", 0.0)),
        duration_seconds=float(payload.get("duration_seconds", 0.0)),
        exit_status=payload.get("exit_status"),
        trajectory_path=payload.get("trajectory_path"),
        model_patch_path=payload.get("model_patch_path"),
        preds_entry=payload.get("preds_entry"),
        error=payload.get("error"),
        environment=_dict_or_empty(payload.get("environment")),
        contamination_detected=bool(payload.get("contamination_detected", False)),
        contamination_evidence=payload.get("contamination_evidence"),
    )


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """Auxiliar interno: dict or empty."""
    return dict(value) if isinstance(value, dict) else {}


def _build_eval_summary(
    *,
    task_results: dict[str, Any],
    experiment_config: ExperimentConfig,
    sb_cli_client: Any,
) -> EvalSummary:
    """Construye el ``EvalSummary`` a partir de los resultados de evaluacion.

    Consulta la cuota restante de sb-cli solo si hubo evaluacion funcional real.
    """
    cfg = experiment_config.benchmark
    summary = EvalSummary(evaluation_skip=cfg.evaluation_skip)

    if cfg.evaluation_skip or cfg.functional_backend == "none":
        return summary

    # Contadores por resultado individual
    groups_seen: set[str] = set()
    for result in task_results.values():
        functional = result.functional or {}
        status = functional.get("status", "")
        agent_id = result.agent_id or ""
        if agent_id not in groups_seen:
            groups_seen.add(agent_id)
            summary.groups_evaluated += 1
            if status == "evaluation_error":
                summary.groups_error += 1
        resolved = functional.get("resolved")
        if resolved is True:
            summary.resolved += 1
        elif resolved is False:
            summary.not_resolved += 1
        elif status == "evaluation_error":
            summary.eval_errors += 1

    # Consultar cuota restante via API directa (evita encoding issues de subprocess)
    try:
        import requests as _requests
        api_key = __import__("os").getenv("SWEBENCH_API_KEY", "")
        if not api_key:
            summary.quota_error = "SWEBENCH_API_KEY no definida"
        else:
            resp = _requests.get(
                "https://api.swebench.com/get-quotas",
                headers={"x-api-key": api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                quotas = resp.json().get("remaining_quotas", {})
                lite = quotas.get("swe-bench_lite", {})
                remaining = lite.get("test")
                if remaining is not None:
                    summary.quota_remaining = int(remaining)
                else:
                    summary.quota_error = "swe-bench_lite/test no encontrado"
            else:
                summary.quota_error = f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        summary.quota_error = str(exc)[:60] or "error desconocido"

    return summary
