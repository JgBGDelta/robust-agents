"""`RowBuilder`: construccion de `ConsolidatedRunRow`/`ConsolidatedStepRow`.

Mapea campo a campo el esquema de `especificacion_analisis.md` S6.2/S7 a partir
de los ficheros ya parseados de un run propio (`run_record.json`,
`evaluation_result.json`, `trajectory.traj.json`) o de un `ExternalMetricsRecord`.
Aplica degradacion limpia campo a campo (S6 "Politica ante fallos" de
`modulo_consolidacion.md`): si una fuente falta, el campo queda `None`, nunca
se omite la fila.

Reutiliza el `patch_metrics` ya calculado por `PatchMetricsExtractor` (en
`evaluation_result.json.extended.patch_metrics` para runs propios, en
`ExternalMetricsRecord.patch_metrics` para externos) en vez de recalcularlo:
misma formula, sin duplicar la comparacion con el `gold_patch` (S4.4 de la
spec general, "reutilizacion sobre reimplementacion"). Las dos metricas que
`PatchMetricsExtractor` no calcula (`dispersion_score`, `test_touch_ratio`) se
reimplementan aqui como funciones puras (S7 de `modulo_consolidacion.md`),
reproduciendo la formula de `StructuralMetricsComputer`
(`src/agent/structural_metrics_module/computer.py`) sin importar `src/agent`.
"""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from analysis.consolidation_module.consolidated_run_row import ConsolidatedRunRow
from analysis.consolidation_module.consolidated_step_row import ConsolidatedStepRow
from analysis.consolidation_module.runs_scanner import ScannedRun
from analysis.consolidation_module.trajectory_reader import TrajectoryMetrics, TrajectoryReader

if TYPE_CHECKING:
    from analysis.external_metrics_module import ExternalMetricsRecord

DEFAULT_TEST_GLOBS = ["tests/**", "**/test_*.py", "**/*_test.py", "**/*test*.py"]

# Campos de la familia "Parche" + "Comparacion con golden patch" (S6.2),
# derivados uniformemente de un `patch_metrics` de `PatchMetricsExtractor`.
_PATCH_FAMILY_FIELDS = (
    "files_modified_count",
    "lines_added",
    "lines_deleted",
    "churn_total",
    "hunks_count",
    "matching_files_count",
    "unexpected_files_count",
    "missing_files_count",
    "jaccard_files",
    "jaccard_lines",
)


class RowBuilder:
    """Construye filas de `datos_consolidados.csv`/`datos_pasos_consolidados.csv`."""

    def __init__(self, test_globs: list[str] | None = None) -> None:
        """
        Parametros
        ----------
        test_globs:
            Patrones para detectar ficheros de test en `test_touch_ratio`.
            Por defecto, los mismos que `StructuralMetricsConfig.test_globs`
            (`src/agent/config.py`).
        """
        self._test_globs = test_globs or DEFAULT_TEST_GLOBS
        self._trajectory_reader = TrajectoryReader()

    # ------------------------------------------------------------------
    # Runs propios
    # ------------------------------------------------------------------

    def build_own(
        self,
        scanned: ScannedRun,
        run_record: dict[str, Any],
        evaluation_result: dict[str, Any] | None,
        evaluation_path: Path | None,
        trajectory_data: dict[str, Any] | None,
    ) -> tuple[ConsolidatedRunRow, list[ConsolidatedStepRow]]:
        """Construye la fila de run y (si aplica) las filas de paso de un run propio.

        Parametros
        ----------
        scanned:
            Run logico canonico ya seleccionado por `RunsScanner`.
        run_record:
            Contenido de `run_record.json` (siempre presente, S6 "run_record.json ausente").
        evaluation_result:
            Contenido de `evaluation_result.json`, o `None` si no existe/es invalido (PA6).
        evaluation_path:
            Ruta a `evaluation_result.json` usada, o `None` si no se leyo.
        trajectory_data:
            Contenido de `trajectory.traj.json`, o `None` si no existe/es invalido.

        Retorna
        -------
        Tupla `(ConsolidatedRunRow, list[ConsolidatedStepRow])`. La lista de
        pasos esta vacia si la traza no trae bloque `robust_agent`.
        """
        trajectory_metrics = (
            self._trajectory_reader.read(trajectory_data) if trajectory_data else TrajectoryMetrics()
        )
        row = self._build_own_row(scanned, run_record, evaluation_result, evaluation_path, trajectory_metrics)
        step_rows = (
            self._trajectory_reader.build_step_rows(
                trajectory_data,
                run_id=row.run_id,
                instance_id=row.instance_id,
                configuration_id=row.configuration_id,
            )
            if trajectory_data
            else []
        )
        return row, step_rows

    def _build_own_row(
        self,
        scanned: ScannedRun,
        run_record: dict[str, Any],
        evaluation_result: dict[str, Any] | None,
        evaluation_path: Path | None,
        trajectory_metrics: TrajectoryMetrics,
    ) -> ConsolidatedRunRow:
        """Auxiliar interno: build own row."""
        agent_run_config = run_record.get("agent_run_config") or {}
        model_id = agent_run_config.get("model_id")
        dataset_slice_size = agent_run_config.get("dataset_slice_size")

        run_status = run_record.get("status")
        exit_status = run_record.get("exit_status")
        has_error = run_record.get("error") is not None

        model_patch = ((run_record.get("preds_entry") or {}).get("model_patch")) or None
        patch_available = bool(model_patch)

        functional = (evaluation_result or {}).get("functional") or {}
        extended = (evaluation_result or {}).get("extended") or {}
        run_metrics = extended.get("run_metrics") or {}
        patch_metrics = extended.get("patch_metrics") if evaluation_result is not None else None

        steps_used = run_metrics.get("steps_used") if evaluation_result is not None else None
        if steps_used is None:
            steps_used = trajectory_metrics.api_calls

        patch_family = _patch_family_from_metrics(patch_metrics)

        return ConsolidatedRunRow(
            run_id=scanned.run_dir.name,
            instance_id=scanned.instance_id,
            repository=_repository_from_instance_id(scanned.instance_id),
            agent_id=scanned.agent_id,
            configuration_id=_configuration_id(scanned.agent_id, model_id),
            source_type="own",
            model_id=model_id,
            sample_group="ablation" if dataset_slice_size is not None else "main",
            run_status=run_status,
            exit_status=exit_status,
            termination=trajectory_metrics.termination,
            failure_category=_failure_category(run_status, exit_status, has_error),
            patch_available=patch_available,
            submitted=exit_status == "Submitted",
            empty_submission=exit_status == "EmptySubmission",
            evaluable=(functional.get("status") == "evaluated") if evaluation_result is not None else None,
            resolved=functional.get("resolved") if evaluation_result is not None else None,
            steps_used=steps_used,
            cost_usd=run_record.get("cost_usd"),
            duration_seconds=run_record.get("duration_seconds"),
            prompt_tokens=trajectory_metrics.prompt_tokens,
            completion_tokens=trajectory_metrics.completion_tokens,
            total_tokens=trajectory_metrics.total_tokens,
            run_sequence=scanned.run_sequence,
            retry_count=scanned.retry_count,
            started_at=run_record.get("started_at"),
            completed_at=run_record.get("ended_at"),
            uncertainty_mean=trajectory_metrics.uncertainty_mean,
            uncertainty_max=trajectory_metrics.uncertainty_max,
            uncertainty_final=trajectory_metrics.uncertainty_final,
            uncertainty_std=trajectory_metrics.uncertainty_std,
            uncertainty_p95=trajectory_metrics.uncertainty_p95,
            uncertainty_high_ratio=trajectory_metrics.uncertainty_high_ratio,
            cycle_mean=trajectory_metrics.cycle_mean,
            failure_rate_mean=trajectory_metrics.failure_rate_mean,
            hedging_mean=trajectory_metrics.hedging_mean,
            volatility_mean=trajectory_metrics.volatility_mean,
            uncertainty_missing_components=trajectory_metrics.uncertainty_missing_components,
            structural_risk_mean=trajectory_metrics.structural_risk_mean,
            structural_risk_max=trajectory_metrics.structural_risk_max,
            structural_risk_final=trajectory_metrics.structural_risk_final,
            structural_risk_p95=trajectory_metrics.structural_risk_p95,
            structural_risk_high_ratio=trajectory_metrics.structural_risk_high_ratio,
            files_changed_ratio=trajectory_metrics.files_changed_ratio,
            hunks_per_file=trajectory_metrics.hunks_per_file,
            net_lines=trajectory_metrics.net_lines,
            cumulative_lines_changed=trajectory_metrics.cumulative_lines_changed,
            surface_final_size=trajectory_metrics.surface_final_size,
            controller_intervention_count=trajectory_metrics.controller_intervention_count,
            proceed_count=trajectory_metrics.proceed_count,
            feedback_count=trajectory_metrics.feedback_count,
            validation_count=trajectory_metrics.validation_count,
            abort_count=trajectory_metrics.abort_count,
            first_intervention_step=trajectory_metrics.first_intervention_step,
            controller_intervention_ratio=_safe_ratio(
                trajectory_metrics.controller_intervention_count, steps_used
            ),
            high_high_encounter_count=trajectory_metrics.high_high_encounter_count,
            final_controller_action=trajectory_metrics.final_controller_action,
            validation_success_count=trajectory_metrics.validation_success_count,
            validation_failure_count=trajectory_metrics.validation_failure_count,
            dispersion_score=dispersion_score(_files_modified_for_dispersion(patch_metrics, model_patch)),
            test_touch_ratio=test_touch_ratio(model_patch, self._test_globs),
            patch_path=run_record.get("model_patch_path"),
            trajectory_path=run_record.get("trajectory_path"),
            evaluation_path=str(evaluation_path) if evaluation_path is not None else None,
            external_release=None,
            external_source=None,
            external_model_id=None,
            **patch_family,
        )

    # ------------------------------------------------------------------
    # Runs externos
    # ------------------------------------------------------------------

    def build_external(
        self, record: "ExternalMetricsRecord", external_metrics_path: Path
    ) -> ConsolidatedRunRow:
        """Construye la fila de `datos_consolidados.csv` para un run externo.

        Parametros
        ----------
        record:
            Registro cargado de `extracted_metrics.jsonl`.
        external_metrics_path:
            Ruta al `extracted_metrics.jsonl` de origen (usada como `patch_path`,
            unica referencia disponible al parche publicado).

        Retorna
        -------
        `ConsolidatedRunRow` con `source_type="external"`; todas las familias
        exclusivas de `own` (coste/esfuerzo, incertidumbre, riesgo estructural,
        controlador) a `None`.
        """
        patch_family = _patch_family_from_metrics(record.patch_metrics)

        return ConsolidatedRunRow(
            run_id=f"ext-{record.source_id}-{record.instance_id}",
            instance_id=record.instance_id,
            repository=_repository_from_instance_id(record.instance_id),
            agent_id=record.agent_id,
            configuration_id=record.agent_id,
            source_type="external",
            model_id=record.model_id,
            sample_group="external",
            run_status="completed" if bool(record.model_patch) else None,
            exit_status=None,
            termination=None,
            failure_category=None,
            patch_available=bool(record.model_patch),
            submitted=bool(record.model_patch),
            empty_submission=None,
            evaluable=record.resolved is not None,
            resolved=record.resolved,
            steps_used=None,
            cost_usd=None,
            duration_seconds=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            run_sequence=None,
            retry_count=None,
            started_at=None,
            completed_at=None,
            uncertainty_mean=None,
            uncertainty_max=None,
            uncertainty_final=None,
            uncertainty_std=None,
            uncertainty_p95=None,
            uncertainty_high_ratio=None,
            cycle_mean=None,
            failure_rate_mean=None,
            hedging_mean=None,
            volatility_mean=None,
            uncertainty_missing_components=None,
            structural_risk_mean=None,
            structural_risk_max=None,
            structural_risk_final=None,
            structural_risk_p95=None,
            structural_risk_high_ratio=None,
            files_changed_ratio=None,
            hunks_per_file=None,
            net_lines=None,
            cumulative_lines_changed=None,
            surface_final_size=None,
            controller_intervention_count=None,
            proceed_count=None,
            feedback_count=None,
            validation_count=None,
            abort_count=None,
            first_intervention_step=None,
            controller_intervention_ratio=None,
            high_high_encounter_count=None,
            final_controller_action=None,
            validation_success_count=None,
            validation_failure_count=None,
            dispersion_score=dispersion_score((record.patch_metrics or {}).get("files_modified") or []),
            test_touch_ratio=test_touch_ratio(record.model_patch, self._test_globs),
            patch_path=str(external_metrics_path),
            trajectory_path=None,
            evaluation_path=None,
            external_release=record.external_release,
            external_source=record.external_source_name,
            external_model_id=record.model_id,
            **patch_family,
        )


# ------------------------------------------------------------------
# Funciones puras de recalculo uniforme (S7 de `modulo_consolidacion.md`)
# ------------------------------------------------------------------


def dispersion_score(files: list[str]) -> float:
    """Entropia de Shannon (normalizada) de los ficheros por directorio raiz.

    Reimplementa `StructuralMetricsComputer._dispersion_score`
    (`src/agent/structural_metrics_module/computer.py`) sin importar `src/agent`,
    aplicada una unica vez sobre el conjunto completo de ficheros del diff final
    en lugar de paso a paso.
    """
    if not files:
        return 0.0
    bucket: dict[str, int] = {}
    for file in files:
        parts = PurePosixPath(file).parts
        root = parts[0] if parts else "."
        bucket[root] = bucket.get(root, 0) + 1
    total = len(files)
    entropy = 0.0
    for count in bucket.values():
        p = count / total
        entropy -= p * math.log2(p)
    max_entropy = math.log2(len(bucket)) if len(bucket) > 1 else 1.0
    return min(entropy / max_entropy, 1.0)


def test_touch_ratio(patch_text: str | None, test_globs: list[str]) -> float:
    """Proporcion de lineas modificadas que caen en ficheros de test.

    Reimplementa `StructuralMetricsComputer._test_touch_ratio` operando
    directamente sobre el texto del diff final (no sobre `git diff --numstat`
    incremental de un paso), ya que aqui no hay checkout local del repositorio.
    """
    if not patch_text:
        return 0.0
    per_file = _lines_changed_by_file(patch_text)
    touched_lines = sum(added + deleted for added, deleted in per_file.values())
    if touched_lines == 0:
        return 0.0
    test_lines = 0
    for path, (added, deleted) in per_file.items():
        if any(PurePosixPath(path).match(pattern) for pattern in test_globs):
            test_lines += added + deleted
    return test_lines / touched_lines


def _lines_changed_by_file(patch_text: str) -> dict[str, tuple[int, int]]:
    """Cuenta lineas anadidas/eliminadas por fichero a partir del texto del diff.

    Soporta cabeceras `diff --git` y unified diff estandar (`--- a/X` / `+++ b/X`),
    igual que `PatchMetricsExtractor._files_modified`.
    """
    counts: dict[str, list[int]] = {}
    current_file: str | None = None
    last_minus_path: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            last_minus_path = _strip_ab_prefix(line[4:].split("\t")[0].strip())
            continue
        if line.startswith("+++ "):
            raw = _strip_ab_prefix(line[4:].split("\t")[0].strip())
            current_file = raw if raw != "/dev/null" else last_minus_path
            if current_file:
                counts.setdefault(current_file, [0, 0])
            continue
        if current_file is None:
            continue
        if line.startswith("+"):
            counts[current_file][0] += 1
        elif line.startswith("-"):
            counts[current_file][1] += 1
    return {path: (added, deleted) for path, (added, deleted) in counts.items()}


def _strip_ab_prefix(path: str) -> str:
    """Auxiliar interno: strip ab prefix."""
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


# ------------------------------------------------------------------
# Helpers privados de mapeo de campos
# ------------------------------------------------------------------


def _patch_family_from_metrics(patch_metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Deriva la familia "Parche"/"Comparacion con golden patch" de un `patch_metrics`.

    `patch_metrics` tiene la forma que produce `PatchMetricsExtractor.extract()`
    (identica para runs propios y externos). `None` degrada toda la familia a
    `null` (evaluacion no disponible, PA6).
    """
    if patch_metrics is None:
        return dict.fromkeys(_PATCH_FAMILY_FIELDS)

    files_modified = patch_metrics.get("files_modified")
    lines_added = patch_metrics.get("lines_added")
    lines_deleted = patch_metrics.get("lines_deleted")
    intersection = patch_metrics.get("files_intersection_with_gold")
    unexpected = patch_metrics.get("files_unexpected")
    missing = patch_metrics.get("files_missing_vs_gold")

    return {
        "files_modified_count": len(files_modified) if files_modified is not None else None,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "churn_total": (
            lines_added + lines_deleted if lines_added is not None and lines_deleted is not None else None
        ),
        "hunks_count": patch_metrics.get("hunks_count"),
        "matching_files_count": len(intersection) if intersection is not None else None,
        "unexpected_files_count": len(unexpected) if unexpected is not None else None,
        "missing_files_count": len(missing) if missing is not None else None,
        "jaccard_files": patch_metrics.get("jaccard_files"),
        "jaccard_lines": patch_metrics.get("jaccard_lines"),
    }


def _files_modified_for_dispersion(
    patch_metrics: dict[str, Any] | None, model_patch: str | None
) -> list[str]:
    """Lista de ficheros modificados para `dispersion_score`.

    Prefiere `patch_metrics.files_modified` (ya calculado por
    `PatchMetricsExtractor` si `evaluation_result.json` esta disponible); si no,
    cae a una extraccion minima a partir del propio `model_patch` para no perder
    la metrica solo porque falte la evaluacion (PA6, degradacion campo a campo).
    """
    if patch_metrics is not None and patch_metrics.get("files_modified") is not None:
        return list(patch_metrics["files_modified"])
    if not model_patch:
        return []
    return sorted(_lines_changed_by_file(model_patch).keys())


def _repository_from_instance_id(instance_id: str) -> str:
    """`org/repo` a partir de un `instance_id` SWE-bench (`org__repo-<pr>`)."""
    if "__" not in instance_id:
        return instance_id
    org, rest = instance_id.split("__", 1)
    repo_name = rest.rsplit("-", 1)[0] if "-" in rest else rest
    return f"{org}/{repo_name}"


def _normalize_model_id(model_id: str) -> str:
    """Elimina el prefijo de proveedor LiteLLM (`vertex_ai/`, `gemini/`, ...).

    Reimplementa `ResumeDetector._normalize_model_id`
    (`src/benchmark/results_module/resume.py`) sin importar `src/benchmark`
    mas alla de `PatchMetricsExtractor` (S9 de `modulo_consolidacion.md`).
    """
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def _configuration_id(agent_id: str, model_id: str | None) -> str:
    """`<agent_id>__<model_id normalizado>` (S6.1 de la spec general)."""
    if not model_id:
        return agent_id
    return f"{agent_id}__{_normalize_model_id(model_id)}"


def _failure_category(status: str | None, exit_status: str | None, has_error: bool) -> str:
    """Deriva `failure_category` de `status`/`exit_status` (S6.2 "Estado y resultado")."""
    if status == "precondition_failed":
        return "precondition_failed"
    if exit_status == "LimitsExceeded":
        return "limits_exceeded"
    if exit_status == "EmptySubmission":
        return "empty_submission"
    if status == "failed" or has_error:
        return "exception"
    return "none"


def _safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    """`numerator / denominator`, o `None` si algun operando falta o es cero."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator
