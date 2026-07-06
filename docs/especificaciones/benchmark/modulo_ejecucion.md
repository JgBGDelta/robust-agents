# Módulo de ejecución (`BenchmarkExecutionModule`)

> Documento de especificación de **alto/medio nivel** del módulo. La spec global del Bloque 2 está en `especificacion_benchmark.md`.

## 1. Función

Actúa como harness de ejecución: a partir de la lista de trabajos (cada instancia cruzada con cada `AgentRunConfig` del YAML de experimento), prepara un entorno aislado por run, instancia el agente correspondiente y captura sus artefactos brutos. El módulo **no evalúa** ni calcula métricas comparables; esa responsabilidad es del módulo de evaluación.

Cada run produce tres artefactos: `trajectory.traj.json`, `model.patch` y `run_record.json`.

## 2. Lista de trabajos

La combinación de instancias y agentes no requiere una estructura algebraica dedicada: basta con un doble bucle sobre instancias y sobre las entradas de `experiment_config.agents`, expresado conceptualmente así en `execution_module.py`:

```python
def build_run_list(experiment_config, instances) -> list[PlannedRun]:
    for instance in instances:
        for agent_run_config in experiment_config.agents:
            yield PlannedRun(instance_id, agent_run_config, run_id(...))
```

`PlannedRun` vive en `results_module/contracts.py`, junto al resto de contratos del pipeline de resultados, y el `run_id` de cada trabajo se deriva de forma determinista de `(instance_id, agent_id, model_id, seed)`; el campo `profile` no entra en la clave porque cada perfil de robustez se modela como un `agent_id` distinto (EB.5.1), no como una dimensión adicional. Al ser una simple construcción de lista, esta lógica no necesita un componente propio (`matrix.py`/`MatrixBuilder`): vive como una función auxiliar del módulo.

`run_matrix()` encadena tres pasos: construir la lista completa de trabajos planificados con `build_run_list`, filtrarla a los pendientes con `results_module.pending_runs(planned)` para respetar la reanudación, y ejecutar los pendientes -en paralelo si así se configura- delegando cada intento en `RunExecutor`.

## 3. Perfiles como agentes

El TFG no representa el perfil de robustez como un segmento adicional de ruta o de agrupación. Cada variante robusta (`robust_strict`, `robust_balanced`, etc.) es una fila propia de `experiment_config.agents`, con `RobustAgent` como clase y el perfil correspondiente fijado vía `config_overrides`.

## 4. Separación `execution_module` vs `run_executor`

El módulo separa dos responsabilidades en dos clases distintas:

| Componente | Rol |
|------------|-----|
| `BenchmarkExecutionModule` | Orquestador: lista de trabajos, workers, reintentos, resume |
| `RunExecutor` | Un intento: entorno, PR1, agente, captura, contaminación |

`BenchmarkExecutionModule` decide **qué** ejecutar y **cuántas veces**; `RunExecutor` sabe **cómo** ejecutar un intento concreto. Siguiendo la convención del resto del proyecto, `execution_module.py` aloja la clase pública `BenchmarkExecutionModule`; el executor no se renombra al nombre del módulo para no confundirlo con la fachada.

### 4.1 Clasificación del `BenchmarkRunRecord`

Tras `agent.run()` sin excepción, `RunExecutor` fija `status` así:

| Condición | `status` | `exit_status` típico |
|-----------|----------|----------------------|
| Check PR1 - repo no limpio (informativo) | `completed` o `failed` según el resto | `environment.pr1_check=false` en `run_record.json` |
| `submission` vacío (solo espacios) | `failed` | `EmptySubmission` si el agente reportó `Submitted` |
| `submission` con parche | `completed` | `Submitted` u otro del agente |
| Excepción no capturada | `failed` | `null` + `error` con la excepción |
| Agente reporta `PreconditionFailed` internamente | `precondition_failed` | `PreconditionFailed` |

Un run `failed` por parche vacío no entra en sb-cli y cuenta como no resuelto en `resolved_rate`.

## 5. Gestión de recursos Docker

El módulo gestiona activamente el ciclo de vida de imágenes y contenedores Docker para evitar acumulación de decenas de GB en disco durante una sesión de 120 instancias.

### 5.1 Limpieza al inicio de sesión

Antes de ejecutar cualquier run, `run_matrix()`:

1. **`_cleanup_orphaned_containers()`**: mata contenedores con nombre `minisweagent-*` que hayan quedado corriendo de sesiones anteriores (Ctrl+C mata Python antes de que `_safe_cleanup` pueda ejecutarse).
2. **`_cleanup_stale_images(pending_instance_ids)`**: elimina imágenes `sweb.eval.x86_64.*` de instancias que ya no tienen runs pendientes en esta sesión. Detecta el `instance_id` a partir del nombre de imagen usando la convención `docker_id = instance_id.replace("__", "_1776_")`.

### 5.2 Limpieza entre instancias (modo secuencial)

En el bucle secuencial, cuando se detecta un cambio de `instance_id`, la imagen de la instancia anterior se elimina con `_remove_docker_image()`. Esto garantiza que en disco solo estén la imagen de la instancia en curso y las de instancias futuras ya descargadas.

### 5.3 Comprobación de espacio libre antes de cada instancia

Antes de iniciar el primer run de cada instancia (secuencial) o de cada run individual (paralelo), `_check_disk_space(instance_id)` verifica que el disco donde reside el almacenamiento Docker tenga suficiente espacio libre:

- La ruta comprobada es `tempfile.gettempdir()` (normalmente `C:\Users\...\AppData\Local\Temp` en Windows, apuntando al mismo disco que el VHDX de Docker Desktop; `/tmp` en Linux).
- El umbral configurable es `BenchmarkConfig.disk_min_free_gb` (por defecto: **10.0 GB**).
- Si el espacio libre está por debajo del umbral, el método imprime un mensaje de error descriptivo, activa `shutdown.request_stop()` y lanza `KeyboardInterrupt("disk_space_exhausted")`, que se propaga por el mismo mecanismo de parada cooperativa que Ctrl+C: el experimento se detiene de forma ordenada antes de que Docker intente descargar la siguiente imagen y falle en cascada.
- Si `disk_min_free_gb <= 0`, la comprobación queda deshabilitada.
- Si `shutil.disk_usage` falla (permisos, plataforma inusual), el error se ignora silenciosamente.

**Motivación (P12):** En Windows, el fichero `docker_data.vhdx` de WSL2 crece con cada imagen descargada pero no se compacta automáticamente. Con 4 workers en paralelo pueden acumularse varias imágenes (~3–12 GB/imagen) simultáneamente antes de que la limpieza post-instancia actúe, agotando el espacio del disco y provocando errores `El sistema no puede encontrar la ruta especificada` en todos los runs siguientes. Esta comprobación proactiva corta el experimento en seco antes de que ocurra ese escenario.

**Cómo reanudar:** Liberar espacio con `docker system prune -a` (y, si persiste, compactar el VHDX con `wsl --manage docker-desktop --set-sparse` o `Optimize-VHD`) y relanzar con `python -m benchmark.run --config <cfg>` (la reanudación automática saltará los runs ya completados).

### 5.4 Ordenación de instancias pesadas

Antes de ejecutar, la lista de runs pendientes se reordena para colocar al final las instancias de repositorios con contextos muy largos (actualmente `matplotlib__matplotlib`). Esto minimiza los picos de TPM al inicio del experimento, cuando hay mayor riesgo de saturar la ventana de tokens/minuto de la API.

## 6. Paralelismo y parada cooperativa

### 6.1 Modo secuencial (workers=1) con backoff anti-429

El bucle secuencial mantiene un delay dinámico entre runs:

- Parte de `inter_run_delay_seconds` (default: 0).
- Si un run tuvo reintentos por RateLimitError (`had_rate_limit_retries=True`): el delay sube 15 s (máx. 60 s).
- Si el run fue limpio: el delay baja 5 s (mín. 0).

El campo `had_rate_limit_retries` del `BenchmarkRunRecord` se detecta leyendo `llm_debug.jsonl` del run y buscando entradas con `retry_attempt > 0`.

### 6.2 Modo paralelo (workers>1) con parada cooperativa

En modo paralelo se usa `ThreadPoolExecutor` con `wait(futures, return_when=FIRST_COMPLETED)` en bucle. Tras cada futuro completado se comprueba `shutdown.stop_requested()`. Si hay señal de parada activa, no se encolan nuevos runs pero se espera a que terminen los ya lanzados.

### 6.3 Parada global (Ctrl+C / quota agotada)

La coordinación de parada usa `src/benchmark/shutdown.py`, un módulo de un único `threading.Event` compartido entre el hilo principal, los workers paralelos y los callbacks de LiteLLM:

- **Ctrl+C**: el signal handler de `run.py` activa `request_stop()` + `KeyboardInterrupt`. `BenchmarkRunner` captura el `KeyboardInterrupt`, limpia contenedores huérfanos y llama a `mark_aborted()`.
- **Quota agotada**: el callback `_on_rate_limit_error` de LiteLLM lleva un contador de reintentos consecutivos de ~60 s. Tras 3 seguidos activa `request_stop()` + `KeyboardInterrupt` con el mensaje `quota_exhausted_stop`, deteniendo el benchmark y mostrando instrucciones para reanudar al día siguiente.
- El callback también ajusta `litellm.retry_after` con el `retryDelay` real que devuelve la API de Gemini en el JSON de error.

## 7. Organización del código

```text
src/benchmark/execution_module/
├── execution_module.py       # build_run_list, run_id, BenchmarkExecutionModule
├── run_executor.py
├── agent_factory.py
├── environment_factory.py
├── contamination_detector.py
├── retry_policy.py
└── benchmark_run_record.py
```

## 8. Referencias

- `modulo_resultados.md` - `pending_runs`, layout sin `<profile>/`, política de reanudación
- `especificacion_benchmark.md` EB.3 PB7 - Vertex AI routing y configuración de proveedor
