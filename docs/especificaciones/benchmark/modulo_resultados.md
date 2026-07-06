# Módulo de resultados (`BenchmarkResultsModule`)

> Documento de especificación de **alto/medio nivel** del módulo. La spec global del Bloque 2 está en `especificacion_benchmark.md` (MR.8).

## 1. Función

Gestiona la persistencia de artefactos, el manifiesto mínimo del experimento, la lógica de reanudación y el export hacia el Bloque 3. El módulo **no calcula métricas**: su responsabilidad es dónde y cómo se guarda lo que otros módulos producen, no su contenido.

## 2. Layout en disco

```text
runs/<experiment_id>/
  manifest.json, config.yaml, dataset_summary.json
  groups/<agent_id>__<model_id>/
    preds.json, functional_report/
  instances/<instance_id>/<agent_id>/<run_id>/
    trajectory.traj.json, model.patch, run_record.json, evaluation_result.json, llm_debug.jsonl
```

El layout no incluye un nivel `<profile>/` (el perfil ya está codificado en el `agent_id`, EB.5.1) ni un índice tabular `results.jsonl`: el Bloque 3 obtiene la vista tabular escaneando directamente el árbol de `instances/`.

## 3. API pública

```text
BenchmarkResultsModule
├─ initialize(matrix_size) -> ExperimentLayout
├─ pending_runs(planned_runs: list[PlannedRun]) -> list[RunSlot]
├─ persist_run_record(record)
├─ persist_evaluation_result(result)         # un solo JSON por run
├─ persist_functional_group_report(group_id, path)
├─ groups_directory(group_id) -> Path
├─ register_existing_run(instance_id, agent_id, run_id) -> Path
├─ mark_running() / mark_aborted(reason)
├─ finalize(task_results: list[EvaluationResult]) -> BenchmarkExport
└─ export_path() -> Path
```

Al persistir un único `evaluation_result.json` por run en lugar de resultados funcional y ampliado separados (EB.7.4), la API no necesita métodos como `persist_functional_result` o `persist_extended_result`, ni un `results_index_path` en el export.

## 4. Reanudación

La reanudación escanea los `run_record.json` en estado terminal; si a alguno le falta `evaluation_result.json`, la fase de evaluación -que es idempotente- lo rellena sin repetir runs ya completados. Esta lógica vive en `resume.py`.

**Estados terminales** (no se reintentan): `{completed, precondition_failed}`.

Los runs con `status=failed` se tratan según el `exit_status`:

| `exit_status` | Comportamiento en reanudación | Razón |
|---|---|---|
| `LimitsExceeded` | **No se reintenta** | El agente agotó pasos o coste; volvería a ocurrir lo mismo consumiendo más tokens |
| `EmptySubmission` | **No se reintenta** | El agente llegó al submit sin parche útil; es un resultado experimental válido (fallo de workflow o estancamiento genuino). Re-ejecución selectiva solo con `--force-rerun` |
| Cualquier otro | **Se reintenta** | Fallo transitorio (429 en cascada, Ctrl+C, timeout de Docker, error de red) |

Esta lógica vive en `ResumeDetector._needs_execution()` vía `TERMINAL_EXIT_STATUSES`. Los runs con `LimitsExceeded` que interesen para análisis pueden reejecutarse de forma selectiva una vez completado el experimento principal (con `--force-rerun` sobre el `run_id` concreto).

Esta tabla solo se aplica a invocaciones de `run()` con `evaluation_only=False` (es decir, cualquier invocación **sin** `--evaluate`), que es cuando `BenchmarkRunner` llama a `execution_module.run_matrix(...)`. Las invocaciones con `--evaluate` (`evaluation_only=True`) omiten por completo la ejecución: ningún `exit_status`, ni siquiera `EmptySubmission`, se reintenta durante una pasada de evaluación — ver `modulo_evaluacion.md`, sección 3.

## 5. Manifiesto mínimo

El manifiesto recorre los estados `initialized` → `running` → `finalized` (o `aborted` si el lote se interrumpe de forma irrecuperable). Sus campos son `benchmark_format_version`, `experiment_id`, `created_at` (ISO 8601 UTC), `updated_at` (ISO 8601 UTC o `null`), `matrix_size`, `runs_completed` (`null` hasta que se invoca `finalize()`) y `abort_reason`. Toda esta lógica cabe directamente en `results_module.py`, sin necesidad de un `manifest_writer.py` separado.

## 6. `experiment_id` y normalización en reanudación

La resolución del `experiment_id` sigue una estrategia híbrida (explícito por configuración o derivado de los parámetros del experimento) implementada en funciones privadas de `results_module.py`, sin un `experiment_id_resolver.py` dedicado: la lógica es lo bastante contenida como para no justificar un fichero propio.

### 6.1 Normalización de `model_id` en reanudación

Al comparar la config persistida con la actual, `ResumeDetector._normalize_for_compare` elimina el prefijo de proveedor LiteLLM del `model_id` de cada agente:

- `vertex_ai/gemini-2.5-flash` → `gemini-2.5-flash`
- `gemini/gemini-2.5-flash` → `gemini-2.5-flash`
- `gemini-2.5-flash` → `gemini-2.5-flash`

Esto permite cambiar de proveedor entre sesiones (p. ej. AI Studio en la sesión 1 → Vertex en la sesión 2) sin romper la validación de coherencia. Los campos operativos excluidos de la comparación son: `workers`, `inter_run_delay_seconds`, `disk_min_free_gb`, `force_rerun` y `evaluation_skip`. Este último se excluye para permitir el flujo de dos fases (`--evaluation-skip` en la ejecución, `--evaluate` después para la evaluación funcional sobre los mismos runs) sin que la reanudación lo trate como un cambio de configuración incompatible.

## 7. Contratos (`contracts.py`)

Los contratos del módulo son `PlannedRun` (una celda del plan de ejecución: instancia, configuración de agente y `run_id`), `RunSlot` (un `PlannedRun` con su `run_dir` ya resuelto), `BenchmarkTaskResult` (incluye el campo `evaluation: EvaluationResult`) y `BenchmarkExport.build(...)`, que ya no incluye `results_index_path` desde que el Bloque 3 pasó a escanear directamente el árbol de `instances/`.

## 8. Organización del código

```text
src/benchmark/results_module/
├── __init__.py
├── results_module.py       # facade + manifest + experiment_id
├── layout.py               # ExperimentLayout + path_safe, group_id, run_directory
├── artifact_store.py       # escritura atómica
├── contracts.py
└── resume.py
```

Cinco archivos bastan para el módulo completo. No hay un `paths.py`, un `experiment_layout.py`, un `experiment_id_resolver.py`, un `manifest_writer.py` ni un `exporter.py` independientes: cada una de esas responsabilidades es lo bastante pequeña como para vivir dentro de `results_module.py` o `layout.py` sin perder claridad, y separarlas en más ficheros solo añadiría indirección.

## 9. Bloque 3

El Bloque 3 consume la salida de este módulo escaneando `instances/` directamente, o bien a través del objeto `BenchmarkExport`. Para cada run necesita `evaluation_result.json` y `run_record.json`; cuando además analiza el proceso robusto paso a paso, recurre a `trajectory.traj.json`.

## 10. Referencias

- `modulo_evaluacion.md`, `ejemplo_evaluation_result.md`
