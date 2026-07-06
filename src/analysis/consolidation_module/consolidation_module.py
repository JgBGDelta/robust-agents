"""Modulo de consolidacion (`ConsolidationModule`).

Escanea el arbol de resultados del Bloque 2 (`runs/<experiment_id>/instances/**`),
lo combina con la salida del modulo de metricas de agentes externos
(`extracted_metrics.jsonl`) y produce dos ficheros tabulares limpios:
`datos_consolidados.csv` (una fila por run logico) y
`datos_pasos_consolidados.csv` (una fila por paso, solo runs propios con traza
`robust_agent`).

Es de **lectura pura** sobre `runs/`: nunca escribe dentro del arbol del
experimento. Ver `docs/especificaciones/analisis/modulo_consolidacion.md`.

Acoplamientos:
- Usa `RunsScanner`, `TrajectoryReader`, `RowBuilder` (submodulos internos).
- Usa `ExternalMetricsRecord.load_jsonl` (`analysis.external_metrics_module`).
- No importa nada de `src/agent`; usa `PatchMetricsExtractor` indirectamente
  via el `patch_metrics` ya calculado en `evaluation_result.json`/
  `ExternalMetricsRecord`.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.consolidation_module.consolidated_run_row import ConsolidatedRunRow
from analysis.consolidation_module.consolidated_step_row import ConsolidatedStepRow
from analysis.consolidation_module.row_builder import DEFAULT_TEST_GLOBS, RowBuilder
from analysis.consolidation_module.runs_scanner import RunsScanner
from analysis.external_metrics_module import ExternalMetricsRecord

logger = logging.getLogger(__name__)

_RUN_RECORD_FILENAME = "run_record.json"
_EVALUATION_RESULT_FILENAME = "evaluation_result.json"
_TRAJECTORY_FILENAME = "trajectory.traj.json"

_RUNS_CSV_FILENAME = "datos_consolidados.csv"
_STEPS_CSV_FILENAME = "datos_pasos_consolidados.csv"


@dataclass
class ConsolidationConfig:
    """Configuracion operativa de `ConsolidationModule`.

    `test_globs` define los patrones para detectar ficheros de test en
    `test_touch_ratio`; por defecto los mismos que
    `StructuralMetricsConfig.test_globs` (`src/agent/config.py`).
    """

    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))


class ConsolidationModule:
    """Consolida resultados propios y externos en `datos_consolidados.csv`.

    Uso tipico::

        module = ConsolidationModule()
        run_rows, step_rows = module.consolidate(
            runs_root=Path("runs/full-lite-120inst-6agents"),
            external_metrics_paths=[
                Path("data/external_predictions/agentless_v1.5/extracted_metrics.jsonl"),
            ],
        )
        csv_path, steps_csv_path = module.write_csv(
            run_rows, step_rows, output_dir=Path("data/full-lite-120inst-6agents")
        )
    """

    def __init__(self, config: ConsolidationConfig | None = None) -> None:
        """Inicializa `ConsolidationModule`."""
        self._config = config or ConsolidationConfig()
        self._scanner = RunsScanner()
        self._row_builder = RowBuilder(test_globs=self._config.test_globs)

    # ------------------------------------------------------------------
    # Interfaz publica
    # ------------------------------------------------------------------

    def consolidate(
        self,
        runs_root: Path,
        external_metrics_paths: list[Path] | None = None,
    ) -> tuple[list[ConsolidatedRunRow], list[ConsolidatedStepRow]]:
        """Punto de entrada unico: escaneo -> seleccion canonica -> filas.

        Idempotente: relanzarla sobre el mismo estado en disco produce el
        mismo resultado, sin fusion incremental con ejecuciones anteriores.
        `runs_root` apunta a `runs/<experiment_id>/`. `external_metrics_paths`
        son las rutas a los `extracted_metrics.jsonl` de `ExternalMetricsModule`;
        `None` o lista vacia si no hay externos en esta pasada.
        """
        run_rows: list[ConsolidatedRunRow] = []
        step_rows: list[ConsolidatedStepRow] = []

        for scanned in self._scanner.scan(runs_root):
            run_record = _read_json_safe(scanned.run_dir / _RUN_RECORD_FILENAME)
            if run_record is None:
                # No deberia ocurrir (RunsScanner ya exige el fichero), pero
                # degradamos igual ante corrupcion detectada tarde (S6).
                logger.warning(
                    "Ignorando %s: run_record.json invalido tras el escaneo.",
                    scanned.run_dir,
                )
                continue

            evaluation_path = scanned.run_dir / _EVALUATION_RESULT_FILENAME
            evaluation_result = _read_json_safe(evaluation_path)
            if evaluation_result is None:
                evaluation_path = None  # type: ignore[assignment]

            trajectory_data = _read_json_safe(scanned.run_dir / _TRAJECTORY_FILENAME)

            row, steps = self._row_builder.build_own(
                scanned,
                run_record=run_record,
                evaluation_result=evaluation_result,
                evaluation_path=evaluation_path,
                trajectory_data=trajectory_data,
            )
            run_rows.append(row)
            step_rows.extend(steps)

        for external_path in external_metrics_paths or []:
            for record in _load_external_records(external_path):
                run_rows.append(self._row_builder.build_external(record, external_path))

        return run_rows, step_rows

    def write_csv(
        self,
        run_rows: list[ConsolidatedRunRow],
        step_rows: list[ConsolidatedStepRow],
        output_dir: Path,
    ) -> tuple[Path, Path]:
        """Serializa ambas listas a CSV en `output_dir`, sobrescribiendo si existen.

        Devuelve la tupla `(ruta_datos_consolidados_csv, ruta_datos_pasos_consolidados_csv)`.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        runs_path = output_dir / _RUNS_CSV_FILENAME
        steps_path = output_dir / _STEPS_CSV_FILENAME

        _write_dicts_csv(runs_path, ConsolidatedRunRow.fieldnames(), [r.to_dict() for r in run_rows])
        _write_dicts_csv(steps_path, ConsolidatedStepRow.fieldnames(), [s.to_dict() for s in step_rows])

        logger.info(
            "Consolidacion escrita: %d runs en %s, %d pasos en %s",
            len(run_rows),
            runs_path,
            len(step_rows),
            steps_path,
        )
        return runs_path, steps_path


# ------------------------------------------------------------------
# Funciones privadas de modulo
# ------------------------------------------------------------------


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    """Lee y parsea un JSON; `None` si no existe o esta corrupto (S6).

    Cualquier error de lectura/parseo se trata como "ausente" y se registra
    en el log con la ruta y el error, sin abortar el pipeline.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return None


def _load_external_records(path: Path) -> list[ExternalMetricsRecord]:
    """Carga un `extracted_metrics.jsonl`; lista vacia si falta o esta corrupto."""
    if not path.exists():
        logger.warning("Ignorando fuente externa: no existe %s", path)
        return []
    try:
        return ExternalMetricsRecord.load_jsonl(path)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return []


def _write_dicts_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    """Auxiliar interno: write dicts csv."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
