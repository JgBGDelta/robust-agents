"""Modulo principal de persistencia de resultados del benchmark.

Gestiona el `experiment_id`, el layout en disco, la escritura atomica de
artefactos por run, las transiciones de estado del manifiesto, la
reanudacion parcial y la exportacion final.

No produce `results.jsonl`. El Bloque 3 lee directamente `instances/`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.config import ExperimentConfig
from benchmark.evaluation_module.evaluation_result import EvaluationResult
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.results_module.artifact_store import ArtifactStore
from benchmark.results_module.contracts import BenchmarkExport, PlannedRun
from benchmark.results_module.layout import ExperimentLayout, run_directory
from benchmark.results_module.resume import ResumeDetector

_OPERATIONAL_FIELDS = ("experiment_id_explicit", "force_rerun")


class BenchmarkResultsModule:
    """Persistencia, manifiestos y reanudacion del experimento."""

    def __init__(
        self,
        experiment_config: ExperimentConfig,
        dataset_summary: dict[str, object] | None = None,
        runs_root: Path | str | None = None,
        *,
        experiment_id_explicit: str | None = None,
        force_rerun: bool = False,
        timestamp: datetime | None = None,
    ) -> None:
        """Construye el modulo y resuelve el `experiment_id` sin tocar disco.

        ``timestamp`` fija el componente temporal del id hibrido en tests.
        """
        self._experiment_config = experiment_config
        self._dataset_summary = dict(dataset_summary or {})
        self._runs_root = (
            Path(runs_root)
            if runs_root is not None
            else Path(experiment_config.benchmark.runs_root)
        )
        self._experiment_id_explicit = (
            experiment_id_explicit or experiment_config.experiment_id_explicit
        )
        self._force_rerun = bool(force_rerun or experiment_config.force_rerun)
        self._experiment_id = self._resolve_id(
            experiment_config,
            experiment_id_explicit=self._experiment_id_explicit,
            timestamp=timestamp,
        )
        self._layout: ExperimentLayout | None = None
        self._manifest: dict[str, Any] | None = None
        self._resumed_from: str | None = None
        self._run_dir_cache: dict[tuple[str, str], Path] = {}

    # -- API publica ---------------------------------------------------------

    @property
    def experiment_id(self) -> str:
        """Identificador del experimento resuelto en construccion."""
        return self._experiment_id

    def set_dataset_summary(self, dataset_summary: dict[str, object]) -> None:
        """Sustituye el `dataset_summary` antes de `initialize()`."""
        self._dataset_summary = dict(dataset_summary or {})

    def initialize(self, *, matrix_size: int = 0) -> ExperimentLayout:
        """Crea el layout en disco y escribe el manifiesto inicial.

        Si el directorio ya existe sin `force_rerun`, entra en flujo de
        reanudacion. Con `force_rerun=True`, mueve el directorio a un backup.
        """
        root = self._runs_root / self._experiment_id
        resume_detected = root.exists() and not self._force_rerun
        if root.exists() and self._force_rerun:
            self._backup_existing_root(root)

        self._layout = self._build_layout(root)
        self._ensure_directories(self._layout)

        if resume_detected:
            self._initialize_resume()
        else:
            self._initialize_new(matrix_size=matrix_size)
        return self._layout

    def pending_runs(self, run_list: list[PlannedRun]) -> list[Any]:
        """Filtra la lista experimental y devuelve los runs pendientes."""
        layout = self._require_layout()
        return ResumeDetector(layout).pending_runs(run_list)

    def mark_running(self) -> None:
        """Transiciona el manifiesto a `running`."""
        manifest = self._require_manifest()
        self._manifest = self._write_running(manifest)

    def persist_run_record(self, run_record: BenchmarkRunRecord) -> None:
        """Escribe `run_record.json` en la ruta del run."""
        run_dir = self._run_directory_for_record(run_record)
        ArtifactStore.write_json(run_dir / "run_record.json", run_record.to_dict())
        self._run_dir_cache[(run_record.instance_id, run_record.run_id)] = run_dir

    def persist_evaluation_result(self, result: EvaluationResult) -> None:
        """Escribe `evaluation_result.json` unificado en la ruta del run."""
        run_dir = self._resolve_run_dir(result.instance_id, result.run_id)
        ArtifactStore.write_json(run_dir / "evaluation_result.json", result.to_dict())

    def evaluation_result_path(self, instance_id: str, run_id: str) -> Path | None:
        """Ruta a `evaluation_result.json` del run si el run_dir esta en cache."""
        cached = self._run_dir_cache.get((instance_id, run_id))
        return (cached / "evaluation_result.json") if cached else None

    def register_existing_run(
        self,
        *,
        instance_id: str,
        agent_id: str,
        run_id: str,
    ) -> Path:
        """Repobla el cache `run_dir` para un run ya persistido en disco.

        Usado al reanudar un experimento: los runs anteriores existen en disco
        pero el cache esta vacio.
        """
        layout = self._require_layout()
        path = run_directory(
            layout,
            instance_id=instance_id,
            agent_id=agent_id,
            run_id=run_id,
        )
        self._run_dir_cache[(instance_id, run_id)] = path
        return path

    def persist_functional_group_report(self, group_id: str, report_path: Path) -> None:
        """Copia el reporte funcional nativo bajo `groups/<group_id>/functional_report/`."""
        layout = self._require_layout()
        destination = layout.groups_dir / group_id / "functional_report"
        source = Path(report_path)
        if source.is_dir():
            ArtifactStore.copy_tree(source, destination)
            return
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / source.name)

    def groups_directory(self, group_id: str) -> Path:
        """Devuelve (creandola) la ruta del grupo `groups/<group_id>/`."""
        layout = self._require_layout()
        path = layout.groups_dir / group_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_artifact_path(self, instance_id: str, run_id: str, filename: str) -> Path:
        """Resuelve la ruta absoluta a un artefacto persistido del run."""
        cached = self._run_dir_cache.get((instance_id, run_id))
        if cached is None:
            raise RuntimeError(
                "run_artifact_path requiere que `persist_run_record` se haya "
                f"llamado antes para conocer la ruta del run ({instance_id}, {run_id})."
            )
        return cached / filename

    def finalize(self, task_results: list[EvaluationResult]) -> BenchmarkExport:
        """Finaliza el manifiesto y devuelve el export."""
        layout = self._require_layout()
        manifest = self._require_manifest()
        self._manifest = self._write_finalized(manifest, runs_completed=len(task_results))
        return self._build_export(layout, self._manifest, runs_count=len(task_results), status="finalized")

    def mark_aborted(self, reason: str) -> None:
        """Marca el manifiesto como `aborted` ante un fallo irrecuperable."""
        manifest = self._require_manifest()
        self._manifest = self._write_aborted(manifest, reason)

    def export_path(self) -> Path:
        """Devuelve la raiz del experimento (`runs/<experiment_id>/`)."""
        return self._require_layout().root

    # -- ManifestWriter (inlined) --------------------------------------------

    def _write_initial(self, *, matrix_size: int) -> dict[str, Any]:
        """Auxiliar interno: write initial."""
        layout = self._require_layout()
        manifest: dict[str, Any] = {
            "benchmark_format_version": self._experiment_config.benchmark.benchmark_format_version,
            "experiment_id": layout.experiment_id,
            "status": "initialized",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "matrix_size": matrix_size,
            "runs_completed": None,
            "abort_reason": None,
        }
        ArtifactStore.write_json(layout.manifest_path, manifest)
        return manifest

    def _write_running(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Auxiliar interno: write running."""
        layout = self._require_layout()
        manifest = dict(manifest)
        manifest["status"] = "running"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        ArtifactStore.write_json(layout.manifest_path, manifest)
        return manifest

    def _write_finalized(self, manifest: dict[str, Any], *, runs_completed: int) -> dict[str, Any]:
        """Auxiliar interno: write finalized."""
        layout = self._require_layout()
        manifest = dict(manifest)
        manifest["status"] = "finalized"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["runs_completed"] = runs_completed
        ArtifactStore.write_json(layout.manifest_path, manifest)
        return manifest

    def _write_aborted(self, manifest: dict[str, Any], reason: str) -> dict[str, Any]:
        """Auxiliar interno: write aborted."""
        layout = self._require_layout()
        manifest = dict(manifest)
        manifest["status"] = "aborted"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["abort_reason"] = reason
        ArtifactStore.write_json(layout.manifest_path, manifest)
        return manifest

    def _read_manifest(self) -> dict[str, Any]:
        """Auxiliar interno: read manifest."""
        layout = self._require_layout()
        return ArtifactStore.read_json(layout.manifest_path)

    # -- Exporter (inlined) --------------------------------------------------

    @staticmethod
    def _build_export(
        layout: ExperimentLayout,
        manifest: dict[str, Any],  # noqa: ARG004
        *,
        runs_count: int,
        status: str,
    ) -> BenchmarkExport:
        """Auxiliar interno: build export."""
        return BenchmarkExport(
            experiment_id=layout.experiment_id,
            experiment_path=layout.root,
            manifest_path=layout.manifest_path,
            dataset_summary_path=layout.dataset_summary_path,
            config_path=layout.config_path,
            runs_count=runs_count,
            status=status,  # type: ignore[arg-type]
        )

    # -- ExperimentIdResolver (inlined) --------------------------------------

    @staticmethod
    def _resolve_id(
        experiment_config: ExperimentConfig,
        *,
        experiment_id_explicit: str | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """Auxiliar interno: resolve id."""
        if experiment_id_explicit:
            return experiment_id_explicit
        if experiment_config.experiment_id_explicit:
            return experiment_config.experiment_id_explicit
        return BenchmarkResultsModule._build_hybrid_id(experiment_config, timestamp=timestamp)

    @staticmethod
    def _config_hash(experiment_config: ExperimentConfig) -> str:
        """Auxiliar interno: config hash."""
        payload = experiment_config.model_dump(mode="json")
        for field in _OPERATIONAL_FIELDS:
            payload.pop(field, None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _build_hybrid_id(
        experiment_config: ExperimentConfig,
        *,
        timestamp: datetime | None = None,
    ) -> str:
        """Auxiliar interno: build hybrid id."""
        moment = timestamp or datetime.now(timezone.utc)
        stamp = moment.strftime("%Y%m%d-%H%M%S")
        return f"{stamp}__{BenchmarkResultsModule._config_hash(experiment_config)}"

    # -- Inicializacion interna ----------------------------------------------

    def _initialize_new(self, *, matrix_size: int) -> None:
        """Auxiliar interno: initialize new."""
        layout = self._require_layout()
        ArtifactStore.write_yaml(
            layout.config_path, self._experiment_config.model_dump(mode="json")
        )
        ArtifactStore.write_json(layout.dataset_summary_path, self._dataset_summary)
        self._manifest = self._write_initial(matrix_size=matrix_size)

    def _initialize_resume(self) -> None:
        """Auxiliar interno: initialize resume."""
        layout = self._require_layout()
        ResumeDetector.validate_resume(
            layout, self._experiment_config, self._dataset_summary
        )
        self._resumed_from = self._experiment_id
        manifest = self._read_manifest()
        self._manifest = self._write_running(manifest)

    def _build_layout(self, root: Path) -> ExperimentLayout:
        """Auxiliar interno: build layout."""
        return ExperimentLayout(
            experiment_id=self._experiment_id,
            root=root,
            manifest_path=root / "manifest.json",
            config_path=root / "config.yaml",
            dataset_summary_path=root / "dataset_summary.json",
            instances_dir=root / "instances",
            groups_dir=root / "groups",
        )

    @staticmethod
    def _ensure_directories(layout: ExperimentLayout) -> None:
        """Auxiliar interno: ensure directories."""
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.instances_dir.mkdir(parents=True, exist_ok=True)
        layout.groups_dir.mkdir(parents=True, exist_ok=True)

    def _backup_existing_root(self, root: Path) -> None:
        """Auxiliar interno: backup existing root."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = root.with_name(f"{root.name}.bak-{stamp}")
        shutil.move(str(root), str(backup))

    def _run_directory_for_record(self, run_record: BenchmarkRunRecord) -> Path:
        """Auxiliar interno: run directory for record."""
        layout = self._require_layout()
        return run_directory(
            layout,
            instance_id=run_record.instance_id,
            agent_id=run_record.agent_run_config.agent_id,
            run_id=run_record.run_id,
        )

    def _resolve_run_dir(self, instance_id: str, run_id: str) -> Path:
        """Auxiliar interno: resolve run dir."""
        cached = self._run_dir_cache.get((instance_id, run_id))
        if cached is None:
            raise RuntimeError(
                "persist_evaluation_result requiere que `persist_run_record` se "
                "haya llamado antes para conocer la ruta del run "
                f"({instance_id}, {run_id})."
            )
        return cached

    def _require_layout(self) -> ExperimentLayout:
        """Auxiliar interno: require layout."""
        if self._layout is None:
            raise RuntimeError(
                "BenchmarkResultsModule.initialize() debe llamarse antes de persistir."
            )
        return self._layout

    def _require_manifest(self) -> dict[str, Any]:
        """Auxiliar interno: require manifest."""
        if self._manifest is None:
            self._manifest = self._read_manifest()
        return self._manifest
