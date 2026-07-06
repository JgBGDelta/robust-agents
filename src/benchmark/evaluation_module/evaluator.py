"""Orquestador de evaluacion por grupo `(agent_id, model_id)`.

Combina evaluacion funcional (via `sb-cli`) y evaluacion ampliada
(extractores locales) en un solo `EvaluationResult` por run.

Flujo por grupo:
  1. Construir `preds.json` con los runs completados.
  2. Someter a `sb-cli` -> `functional_dict` por run.
  3. Extraer `run_metrics` y `patch_metrics` de la traza y el parche.
  4. Combinar en `EvaluationResult`.
  5. Persistir via `results_module.persist_evaluation_result`.

Idempotente: si `evaluation_result.json` ya existe y es valido para un
run, se carga en lugar de recomputar.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmark.config import BenchmarkConfig, ExperimentConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.evaluation_module.extractors.patch_metrics import (
    PatchMetricsExtractor,
    _EMPTY_PATCH_METRICS,
)
from benchmark.evaluation_module.extractors.run_metrics import RunMetricsExtractor
from benchmark.evaluation_module.sb_cli_client import SbCliClient, SbCliSubmissionResult
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.results_module.artifact_store import ArtifactStore
from benchmark.results_module.layout import group_id as build_group_id

_RETRYABLE_FUNCTIONAL_STATUSES = frozenset({"evaluation_error", "skipped", "unavailable"})
"""Estados cacheados de `functional.status` que se reintentan si la
evaluacion esta activa. `evaluated` (exito real de sb-cli) nunca esta aqui:
un grupo ya evaluado con exito no debe volver a gastar cuota."""


class Evaluator:
    """Evalua runs por grupo y produce `EvaluationResult` por run."""

    def __init__(
        self,
        *,
        config: BenchmarkConfig,
        results_module: Any,
        sb_cli_client: SbCliClient | None = None,
    ) -> None:
        """Inicializa `Evaluator`."""
        self._config = config
        self._results_module = results_module
        self._client = sb_cli_client or SbCliClient(
            timeout_seconds=config.sb_cli_timeout_seconds,
            max_retries=int(config.retry_policy.get("max_retries", 1)),
        )

    def evaluate_runs(
        self,
        run_records: list[BenchmarkRunRecord],
        instances: list[BenchmarkInstance],
        experiment_config: ExperimentConfig,
        *,
        force_agent_ids: frozenset[str] | None = None,
    ) -> dict[str, EvaluationResult]:
        """Evalua todos los runs y devuelve `{run_id: EvaluationResult}`.

        Idempotente: carga el artefacto persistido si ya existe.

        ``force_agent_ids``: agentes para los que se ignora el cacheado
        incluso si `functional.status == "evaluated"`, y se somete de nuevo a
        `sb-cli` bajo un `run_id` nuevo (con sufijo de reintento). Uso
        excepcional: solo cuando se sospecha que un `evaluated` previo es
        erróneo por un fallo del backend de `sb-cli` (p. ej. `sb-cli` devolvio
        `Failed runs` para el 100% de las instancias sin fallo tecnico) y se
        decide consumir cuota deliberadamente en un reintento — ver `modulo_evaluacion.md` seccion 3
        (`force_agent_ids`). No confundir con
        `only_agent_ids` (que solo filtra el alcance, no fuerza reintento).
        """
        instance_by_id = {inst.instance_id: inst for inst in instances}
        artifact_root = self._artifact_root()
        force_agent_ids = force_agent_ids or frozenset()

        # Separar los runs que ya tienen resultado persistido.
        #
        # Se re-evaluan (si la evaluacion funcional esta activa) los cacheados
        # con status en _RETRYABLE_FUNCTIONAL_STATUSES:
        #   - "evaluation_error": el error puede ser transitorio (p. ej.
        #     encoding en Windows).
        #   - "skipped": resultado de una fase 1 con evaluation_skip=True (o
        #     functional_backend="none"); al activar la evaluacion (fase 2,
        #     `--evaluate`) estos runs nunca se habian sometido de verdad a
        #     sb-cli y deben promoverse. Los que ademas no sean evaluables
        #     (run fallido, sin patch) vuelven a caer en `_skipped_functional`
        #     sin gastar cuota.
        #   - "unavailable": sb-cli no estaba instalado/accesible en el
        #     intento anterior; puede estarlo ahora.
        # Un resultado ya "evaluated" (sb-cli respondio con exito) NUNCA se
        # reintenta salvo que su `agent_id` este en `force_agent_ids`.
        evaluation_active = (
            not self._config.evaluation_skip
            and self._config.functional_backend != "none"
        )
        to_evaluate: list[BenchmarkRunRecord] = []
        forced_group_ids: set[str] = set()
        results: dict[str, EvaluationResult] = {}
        for record in run_records:
            cached = self._try_load_cached(record)
            if cached is not None:
                functional_status = cached.functional.get("status", "")
                agent_id = record.agent_run_config.agent_id
                force_this = evaluation_active and agent_id in force_agent_ids
                if force_this:
                    forced_group_ids.add(
                        build_group_id(agent_id=agent_id, model_id=record.agent_run_config.model_id)
                    )
                if evaluation_active and (
                    functional_status in _RETRYABLE_FUNCTIONAL_STATUSES or force_this
                ):
                    to_evaluate.append(record)
                else:
                    results[record.run_id] = cached
            else:
                to_evaluate.append(record)

        if not to_evaluate:
            return results

        # Agrupar por (agent_id, model_id) para la evaluacion funcional.
        sb_cli_subset = _sb_cli_subset_alias(experiment_config.dataset.subset)
        sb_cli_split = experiment_config.dataset.split
        groups = _group_by_agent_model(to_evaluate)

        for (agent_id, model_id), runs in groups.items():
            gid = build_group_id(agent_id=agent_id, model_id=model_id)
            functional_by_run = self._evaluate_group_functional(
                group_id=gid,
                runs=runs,
                sb_cli_subset=sb_cli_subset,
                sb_cli_split=sb_cli_split,
                force_new_run_id=gid in forced_group_ids,
            )
            for record in runs:
                instance = instance_by_id.get(record.instance_id) or _fallback_instance(
                    record.instance_id
                )
                result = self._build_result(
                    record=record,
                    instance=instance,
                    artifact_root=artifact_root,
                    functional=functional_by_run.get(record.run_id, _skipped_functional(record)),
                )
                self._results_module.persist_evaluation_result(result)
                results[record.run_id] = result

        return results

    # ------------------------------------------------------------------
    # Evaluacion funcional por grupo

    def _evaluate_group_functional(
        self,
        *,
        group_id: str,
        runs: list[BenchmarkRunRecord],
        sb_cli_subset: str,
        sb_cli_split: str,
        force_new_run_id: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Devuelve `{run_id: functional_dict}` para todos los runs del grupo."""
        evaluable = [r for r in runs if _is_functionally_evaluable(r)]
        non_evaluable = [r for r in runs if r not in evaluable]

        out: dict[str, dict[str, Any]] = {}
        for record in non_evaluable:
            out[record.run_id] = _skipped_functional(record)

        if not evaluable:
            return out

        if self._config.evaluation_skip or self._config.functional_backend == "none":
            cause = "evaluation_skip" if self._config.evaluation_skip else "functional_backend_none"
            for record in evaluable:
                out[record.run_id] = _functional_skipped_by_config(cause=cause)
            return out

        preds_path = self._write_preds_json(group_id=group_id, runs=evaluable)
        sb_cli_run_id = (
            f"{group_id}-retry{int(time.time())}" if force_new_run_id else None
        )
        submission = self._submit(
            group_id=group_id,
            preds_path=preds_path,
            subset=sb_cli_subset,
            split=sb_cli_split,
            sb_cli_run_id=sb_cli_run_id,
        )

        for record in evaluable:
            out[record.run_id] = _build_functional_dict(
                record=record, group_id=group_id, submission=submission, preds_path=preds_path
            )
        return out

    def _write_preds_json(self, *, group_id: str, runs: list[BenchmarkRunRecord]) -> Path:
        """Auxiliar interno: write preds json."""
        group_dir = self._results_module.groups_directory(group_id)
        preds_path = group_dir / "preds.json"
        entries: list[dict[str, Any]] = []
        for record in runs:
            patch_text = self._read_patch(record)
            entry = dict(record.preds_entry) if isinstance(record.preds_entry, dict) else {}
            entry["model_name_or_path"] = group_id
            entry.setdefault("instance_id", record.instance_id)
            entry["model_patch"] = patch_text or entry.get("model_patch", "")
            entries.append(entry)
        ArtifactStore.write_json(preds_path, entries)
        return preds_path

    def _submit(
        self,
        *,
        group_id: str,
        preds_path: Path,
        subset: str,
        split: str,
        sb_cli_run_id: str | None = None,
    ) -> SbCliSubmissionResult:
        """Auxiliar interno: submit."""
        report_dir = self._results_module.groups_directory(group_id) / "functional_report"
        return self._client.submit(
            subset=subset,
            split=split,
            predictions_path=preds_path,
            report_dir=report_dir,
            run_id=sb_cli_run_id,
        )

    def _read_patch(self, record: BenchmarkRunRecord) -> str | None:
        """Auxiliar interno: read patch."""
        if not record.model_patch_path:
            return None
        path = Path(record.model_patch_path)
        if not path.is_absolute():
            try:
                path = self._results_module.run_artifact_path(
                    record.instance_id, record.run_id, Path(record.model_patch_path).name
                )
            except RuntimeError:
                pass
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Construccion del resultado ampliado

    def _build_result(
        self,
        *,
        record: BenchmarkRunRecord,
        instance: BenchmarkInstance,
        artifact_root: Path | None,
        functional: dict[str, Any],
    ) -> EvaluationResult:
        """Calcula metricas locales y compone el `EvaluationResult`."""
        traj_path = _resolve(record.trajectory_path, artifact_root)
        patch_path = _resolve(record.model_patch_path, artifact_root)
        model_patch = _read_text_safe(patch_path)
        gold_patch = instance.gold_patch

        errors: list[dict[str, Any]] = []

        # run_metrics
        try:
            run_metrics, rm_avail, trace_format = RunMetricsExtractor.extract(
                trajectory_path=traj_path, run_record=record
            )
        except Exception as exc:  # noqa: BLE001
            run_metrics = _empty_run_metrics(record)
            rm_avail = {"status": "error", "cause": exc.__class__.__name__}
            trace_format = None
            errors.append({"block": "run_metrics", "cause": str(exc)})

        # patch_metrics
        pm_avail = PatchMetricsExtractor.availability(model_patch=model_patch, gold_patch=gold_patch)
        try:
            patch_metrics = PatchMetricsExtractor.extract(
                model_patch=model_patch, gold_patch=gold_patch
            )
        except Exception as exc:  # noqa: BLE001
            patch_metrics = dict(_EMPTY_PATCH_METRICS)
            pm_avail = {"status": "error", "cause": exc.__class__.__name__}
            errors.append({"block": "patch_metrics", "cause": str(exc)})

        extended = {
            "run_metrics": run_metrics,
            "patch_metrics": patch_metrics,
            "availability": {
                "run_metrics": rm_avail,
                "patch_metrics": pm_avail,
            },
            "errors": errors,
        }
        return EvaluationResult(
            run_id=record.run_id,
            instance_id=record.instance_id,
            agent_id=record.agent_run_config.agent_id,
            trace_format=trace_format,
            functional=functional,
            extended=extended,
        )

    # ------------------------------------------------------------------
    # Helpers

    def _try_load_cached(self, record: BenchmarkRunRecord) -> EvaluationResult | None:
        """Auxiliar interno: try load cached."""
        path = self._results_module.evaluation_result_path(record.instance_id, record.run_id)
        return EvaluationResult.try_load(path) if path is not None else None

    def _artifact_root(self) -> Path | None:
        """Auxiliar interno: artifact root."""
        try:
            return self._results_module.export_path()
        except (RuntimeError, AttributeError):
            return None


# ---------------------------------------------------------------------------
# Helpers de modulo
# ---------------------------------------------------------------------------


def _group_by_agent_model(
    records: list[BenchmarkRunRecord],
) -> dict[tuple[str, str], list[BenchmarkRunRecord]]:
    """Auxiliar interno: group by agent model."""
    groups: dict[tuple[str, str], list[BenchmarkRunRecord]] = defaultdict(list)
    for record in records:
        key = (record.agent_run_config.agent_id, record.agent_run_config.model_id)
        groups[key].append(record)
    return dict(groups)


def _has_model_patch(record: BenchmarkRunRecord) -> bool:
    """Auxiliar interno: has model patch."""
    if not isinstance(record.preds_entry, dict):
        return False
    return bool(str(record.preds_entry.get("model_patch", "")).strip())


def _is_functionally_evaluable(record: BenchmarkRunRecord) -> bool:
    """Auxiliar interno: is functionally evaluable."""
    return record.status == "completed" and _has_model_patch(record)


def _skipped_functional(record: BenchmarkRunRecord) -> dict[str, Any]:
    """Auxiliar interno: skipped functional."""
    if record.status != "completed":
        cause = f"run_status={record.status}"
    elif not _has_model_patch(record):
        cause = "empty_model_patch"
    else:
        cause = "no_model_patch"
    return _functional_skipped_by_config(cause=cause)


def _functional_skipped_by_config(*, cause: str) -> dict[str, Any]:
    """Auxiliar interno: functional skipped by config."""
    return {
        "resolved": None,
        "status": "skipped",
        "evaluation_backend": "none",
        "report_path": None,
        "evidence": {"cause": cause},
    }


def _build_functional_dict(
    *,
    record: BenchmarkRunRecord,
    group_id: str,
    submission: SbCliSubmissionResult,
    preds_path: Path,
) -> dict[str, Any]:
    """Auxiliar interno: build functional dict."""
    evidence: dict[str, Any] = {
        "group_id": group_id,
        "preds_path": preds_path.as_posix(),
        "command": submission.command,
    }
    if submission.backend_run_id:
        evidence["backend_run_id"] = submission.backend_run_id
    if submission.log_excerpt:
        evidence["log_excerpt"] = submission.log_excerpt
    if submission.error_reason:
        evidence["error_reason"] = submission.error_reason

    if submission.status == "evaluated":
        return {
            "resolved": _read_resolved(submission.report, record.instance_id),
            "status": "evaluated",
            "evaluation_backend": "sb_cli",
            "report_path": (
                submission.report_path.as_posix() if submission.report_path else None
            ),
            "evidence": evidence,
        }
    if submission.status == "unavailable":
        return {
            "resolved": None,
            "status": "unavailable",
            "evaluation_backend": "none",
            "report_path": None,
            "evidence": evidence,
        }
    return {
        "resolved": None,
        "status": "evaluation_error",
        "evaluation_backend": "sb_cli",
        "report_path": (
            submission.report_path.as_posix() if submission.report_path else None
        ),
        "evidence": evidence,
    }


def _fallback_instance(instance_id: str) -> Any:
    """Auxiliar interno: fallback instance."""
    from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
    return BenchmarkInstance(
        instance_id=instance_id,
        dataset_name="unknown",
        subset="unknown",
        split="unknown",
        repo="unknown",
        base_commit="",
        problem_statement="",
        gold_patch=None,
        fail_to_pass=[],
        pass_to_pass=[],
        metadata={"fallback": True},
    )


def _resolve(path_str: str | None, artifact_root: Path | None) -> Path | None:
    """Auxiliar interno: resolve."""
    if not path_str:
        return None
    p = Path(path_str)
    if p.is_absolute() or artifact_root is None:
        return p
    return artifact_root / p


def _read_text_safe(path: Path | None) -> str | None:
    """Auxiliar interno: read text safe."""
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _empty_run_metrics(record: BenchmarkRunRecord) -> dict[str, Any]:
    """Auxiliar interno: empty run metrics."""
    return {
        "trajectory_format": None,
        "exit_status": record.exit_status,
        "termination": None,
        "steps_used": None,
        "cost_total": None,
        "wallclock_seconds": record.duration_seconds,
    }


def _sb_cli_subset_alias(subset: str) -> str:
    """Mapea el subset del dataset al nombre que espera `sb-cli submit`."""
    normalized = subset.lower().strip()
    _aliases = {
        "lite": "swe-bench_lite",
        "verified": "swe-bench_verified",
        "m": "swe-bench-m",
        "swe-bench-lite": "swe-bench_lite",
        "swe-bench-verified": "swe-bench_verified",
    }
    if normalized in _aliases:
        return _aliases[normalized]
    if normalized.startswith("swe-bench"):
        return normalized
    return f"swe-bench_{normalized}" if normalized else "swe-bench_lite"


def _read_resolved(report: dict[str, Any] | None, instance_id: str) -> bool | None:
    """Auxiliar interno: read resolved."""
    if not isinstance(report, dict):
        return None
    resolved_ids = report.get("resolved_ids")
    if not isinstance(resolved_ids, list):
        resolved_ids = report.get("resolved") if isinstance(report.get("resolved"), list) else None
    unresolved_ids = report.get("unresolved_ids")
    if not isinstance(unresolved_ids, list):
        unresolved_ids = (
            report.get("unresolved") if isinstance(report.get("unresolved"), list) else None
        )
    if resolved_ids is not None and instance_id in resolved_ids:
        return True
    if unresolved_ids is not None and instance_id in unresolved_ids:
        return False
    if resolved_ids is not None:
        return False
    if unresolved_ids is not None:
        return True
    for key in ("instances", "results"):
        container = report.get(key)
        if isinstance(container, dict):
            entry = container.get(instance_id)
            if isinstance(entry, dict) and isinstance(entry.get("resolved"), bool):
                return bool(entry["resolved"])
    return None
