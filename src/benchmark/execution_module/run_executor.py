"""`RunExecutor` del modulo de ejecucion.

Orquesta el ciclo de vida de **un solo intento** de run:

1. construye el entorno (PR1 incluido),
2. construye el agente con el modelo,
3. invoca `agent.run(problem_statement)`,
4. captura trayectoria + parche,
5. persiste artefactos via `BenchmarkResultsModule`,
6. audita la trayectoria con `ContaminationDetector`,
7. devuelve un `BenchmarkRunRecord`.

La politica de reintentos se aplica **fuera** de este ejecutor
(en `BenchmarkExecutionModule`), que coordina varios intentos como
records encadenados via `environment.retry_of`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from benchmark.config import AgentRunConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.execution_module.agent_factory import AgentFactory
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.execution_module.contamination_detector import ContaminationDetector
from benchmark.execution_module.environment_factory import (
    EnvironmentFactory,
    EnvironmentHandle,
    PreconditionChecker,
    StatusFn,
)
from benchmark.execution_module.retry_policy import RetryPolicy
from benchmark.results_module.layout import run_directory


ClockFn = Callable[[], float]
"""Callable inyectable para timestamps (default `time.time`)."""


class RunExecutor:
    """Ejecuta un unico intento siguiendo el ciclo de vida definido."""

    def __init__(
        self,
        *,
        results_module: Any,
        environment_factory: EnvironmentFactory,
        agent_factory: AgentFactory,
        pr1_status_fn: StatusFn | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        """Almacena dependencias inyectadas; sin estado entre runs."""
        self._results_module = results_module
        self._environment_factory = environment_factory
        self._agent_factory = agent_factory
        self._pr1_status_fn = pr1_status_fn
        self._clock = clock or time.time

    # -- API publica ---------------------------------------------------------

    def execute(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        run_id: str,
        worker_id: int = 0,
        retry_of: str | None = None,
    ) -> BenchmarkRunRecord:
        """Ejecuta un intento y devuelve el `BenchmarkRunRecord`."""
        started_at = self._clock()
        environment_meta: dict[str, Any] = {
            "worker_id": worker_id,
            "image_digest": None,
            "pr1_check": False,
        }
        if retry_of is not None:
            environment_meta["retry_of"] = retry_of

        handle: EnvironmentHandle | None = None
        agent: Any | None = None
        run_dir: Path | None = None
        prev_debug_dir = os.environ.get("MSWEA_LLM_DEBUG_DIR")
        try:
            handle = self._environment_factory.build(
                instance=instance, agent_run_config=agent_run_config
            )
            environment_meta["image_digest"] = handle.image_digest

            clean, reason = PreconditionChecker.is_repo_clean(
                handle, status_fn=self._pr1_status_fn
            )
            environment_meta["pr1_check"] = clean
            # PR1 es informativo, no bloqueante: algunas imagenes SWE-bench oficiales
            # tienen ficheros pre-modificados por configuracion del entorno de test
            # (p. ej. setup.py, tox.ini). sb-cli evalua siempre contra el commit base
            # correcto, por lo que el parche del agente se aplica limpiamente.
            # Registramos el estado sucio en environment_meta para analisis posterior.
            if not clean:
                environment_meta["pr1_detail"] = reason

            run_dir = self._run_dir(instance.instance_id, agent_run_config, run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MSWEA_LLM_DEBUG_DIR"] = str(run_dir)

            agent = self._agent_factory.build(
                instance=instance,
                agent_run_config=agent_run_config,
                env=handle.raw_env,
                output_path=run_dir / "trajectory.traj.json",
            )

            run_result = agent.run(instance.problem_statement)
            trajectory = agent.serialize()

            return self._build_completed_record(
                instance=instance,
                agent_run_config=agent_run_config,
                run_id=run_id,
                started_at=started_at,
                run_result=run_result,
                trajectory=trajectory,
                environment=environment_meta,
            )
        except BaseException as exc:  # noqa: BLE001
            trajectory_path: str | None = None
            if agent is not None and run_dir is not None:
                try:
                    rel_prefix = (
                        f"instances/{instance.instance_id}/{agent_run_config.agent_id}/{run_id}"
                    )
                    self._persist_trajectory(run_dir, agent.serialize())
                    trajectory_path = f"{rel_prefix}/trajectory.traj.json"
                except Exception:  # noqa: BLE001
                    trajectory_path = None
            record = self._build_failed_record(
                instance=instance,
                agent_run_config=agent_run_config,
                run_id=run_id,
                started_at=started_at,
                exception=exc,
                environment=environment_meta,
                trajectory_path=trajectory_path,
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                # Persiste el record del run interrumpido para que el resume
                # lo reconozca (status=failed, no terminal) y lo reintente en
                # la proxima sesion. Luego re-propaga para detener el experimento.
                try:
                    self._results_module.persist_run_record(record)
                except Exception:  # noqa: BLE001
                    pass
                raise
            return record
        finally:
            if prev_debug_dir is None:
                os.environ.pop("MSWEA_LLM_DEBUG_DIR", None)
            else:
                os.environ["MSWEA_LLM_DEBUG_DIR"] = prev_debug_dir
            if handle is not None:
                self._safe_cleanup(handle)

    # -- Construccion de records ---------------------------------------------

    def _build_completed_record(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        run_id: str,
        started_at: float,
        run_result: dict[str, Any],
        trajectory: dict[str, Any],
        environment: dict[str, Any],
    ) -> BenchmarkRunRecord:
        """Auxiliar interno: build completed record."""
        ended_at = self._clock()
        exit_status = run_result.get("exit_status") or trajectory.get("info", {}).get("exit_status")
        submission = run_result.get("submission") or trajectory.get("info", {}).get("submission") or ""

        run_dir = self._run_dir(instance.instance_id, agent_run_config, run_id)
        trajectory_path = self._persist_trajectory(run_dir, trajectory)
        model_patch_path = self._persist_model_patch(run_dir, submission)

        contamination = ContaminationDetector.audit(
            trajectory,
            AgentFactory.forbidden_test_ids(instance)
            if AgentFactory.should_enable_tests_validation(agent_run_config)
            else None,
        )

        had_rate_limit_retries = self._had_rate_limit_retries(run_dir)
        cost_usd = float(
            trajectory.get("info", {}).get("model_stats", {}).get("instance_cost", 0.0) or 0.0
        )

        if exit_status == "PreconditionFailed":
            status = "precondition_failed"
            error = None
            record_exit_status = exit_status
        elif not submission.strip():
            status = "failed"
            record_exit_status = (
                "EmptySubmission" if exit_status == "Submitted" else exit_status
            )
            error = {
                "reason": "empty_model_patch",
                "agent_exit_status": exit_status,
            }
        else:
            status = "completed"
            record_exit_status = exit_status
            error = None

        rel_prefix = (
            f"instances/{instance.instance_id}/{agent_run_config.agent_id}/{run_id}"
        )

        return BenchmarkRunRecord(
            run_id=run_id,
            instance_id=instance.instance_id,
            agent_run_config=agent_run_config,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=max(0.0, ended_at - started_at),
            exit_status=record_exit_status,
            trajectory_path=f"{rel_prefix}/trajectory.traj.json"
            if trajectory_path is not None
            else None,
            model_patch_path=f"{rel_prefix}/model.patch"
            if model_patch_path is not None
            else None,
            preds_entry={
                "instance_id": instance.instance_id,
                "model_name_or_path": agent_run_config.model_id,
                "model_patch": submission,
            },
            error=error,
            environment=environment,
            contamination_detected=contamination.detected,
            contamination_evidence=contamination.to_evidence(),
            had_rate_limit_retries=had_rate_limit_retries,
            cost_usd=cost_usd,
        )

    def _build_failed_record(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        run_id: str,
        started_at: float,
        exception: BaseException,
        environment: dict[str, Any],
        trajectory_path: str | None = None,
    ) -> BenchmarkRunRecord:
        """Auxiliar interno: build failed record."""
        ended_at = self._clock()
        return BenchmarkRunRecord(
            run_id=run_id,
            instance_id=instance.instance_id,
            agent_run_config=agent_run_config,
            status="failed",
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=max(0.0, ended_at - started_at),
            exit_status=None,
            trajectory_path=trajectory_path,
            model_patch_path=None,
            preds_entry=None,
            error=RetryPolicy.describe(exception),
            environment=environment,
            contamination_detected=False,
            contamination_evidence=None,
        )

    def _build_precondition_failed_record(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        run_id: str,
        started_at: float,
        reason: str | None,
        environment: dict[str, Any],
    ) -> BenchmarkRunRecord:
        """Auxiliar interno: build precondition failed record."""
        ended_at = self._clock()
        return BenchmarkRunRecord(
            run_id=run_id,
            instance_id=instance.instance_id,
            agent_run_config=agent_run_config,
            status="precondition_failed",
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=max(0.0, ended_at - started_at),
            exit_status="PreconditionFailed",
            trajectory_path=None,
            model_patch_path=None,
            preds_entry=None,
            error={"reason": "pr1_failed", "detail": reason or "unclean_repo"},
            environment=environment,
            contamination_detected=False,
            contamination_evidence=None,
        )

    # -- Persistencia de artefactos ------------------------------------------

    def _run_dir(
        self,
        instance_id: str,
        agent_run_config: AgentRunConfig,
        run_id: str,
    ) -> Path:
        """Devuelve el `run_dir` absoluto del run en el layout del experimento."""
        layout = self._results_module._require_layout()  # type: ignore[attr-defined]
        return run_directory(
            layout,
            instance_id=instance_id,
            agent_id=agent_run_config.agent_id,
            run_id=run_id,
        )

    @staticmethod
    def _persist_trajectory(run_dir: Path, trajectory: dict[str, Any]) -> Path:
        """Escribe la trayectoria como JSON bajo el `run_dir`."""
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "trajectory.traj.json"
        text = json.dumps(trajectory, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    @staticmethod
    def _persist_model_patch(run_dir: Path, submission: str) -> Path:
        """Escribe el `model_patch` como diff bajo el `run_dir`."""
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "model.patch"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(submission, encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    @staticmethod
    def _had_rate_limit_retries(run_dir: Path) -> bool:
        """Devuelve True si el run tuvo reintentos por RateLimitError (429).

        Lee el ``llm_debug.jsonl`` escrito durante el run y busca entradas con
        ``retry_attempt > 0``. LiteLLM escribe una entrada por cada intento,
        incluyendo los reintentos automaticos por 429. No toca la logica de
        LiteLLM: solo lee el archivo de log ya generado.
        """
        debug_file = run_dir / "llm_debug.jsonl"
        if not debug_file.exists():
            return False
        try:
            for line in debug_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if int(entry.get("retry_attempt", 0)) > 0:
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _safe_cleanup(self, handle: EnvironmentHandle) -> None:
        """Auxiliar interno: safe cleanup."""
        try:
            self._environment_factory.cleanup(handle)
        except Exception:  # noqa: BLE001
            pass
