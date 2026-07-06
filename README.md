# Manual del usuario - TFG GII Jorge Barroso García

Agente robusto sobre [mini-SWE-agent](src/agent/mini-swe-agent/) y benchmark extendido para SWE-bench (TFG).

Documentación ampliada (especificaciones e implementación):
[https://jgbgdelta.github.io/robust-agents/](https://jgbgdelta.github.io/robust-agents/)

Guía paso a paso para instalar el proyecto, configurarlo y ejecutar cada uno de sus
tres bloques principales si fuera necesario:

- **Bloque 1 - Agente robusto** (`python -m agent.cli`): ejecución manual sobre repositorios locales.
- **Bloque 2 - Benchmark** (`python -m benchmark.run`): experimento completo sobre SWE-bench Lite.
- **Bloque 3 - Análisis** (`python -m analysis.run`): consolidación de resultados y generación de figuras.

---

## 1. Requisitos previos

| Requisito | Versión mínima | Notas |
|-----------|----------------|-------|
| Python | 3.10 | Comprueba con `python --version` |
| Git | - | Necesario para clonar y para las métricas estructurales del agente |
| Docker | - | Solo para el benchmark (ver sección 4.1) |
| Cuenta en proveedor LLM | - | Gemini, OpenAI, Anthropic, etc. |
| Cuenta SWE-bench (`sb-cli`) | - | Solo para evaluación funcional del benchmark; [registro gratuito](https://www.swebench.com) |

---

## 2. Instalación

### 2.1 Clonar el repositorio

```powershell
git clone https://github.com/JgBGDelta/robust-agents.git
cd robust-agents
```

### 2.2 Crear el entorno virtual

```powershell
python -m venv .venv
```

Activa el entorno. **Repite este paso cada vez que abras un terminal nuevo:**

```powershell
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
# source .venv/bin/activate    # Linux / macOS
```

### 2.3 Instalar dependencias

```powershell
pip install -e ".[dev]"
pip install -e src/agent/mini-swe-agent
```

`mini-swe-agent` se instala por separado porque está incluido en el repositorio en
`src/agent/mini-swe-agent/` (copia local del upstream, no un paquete PyPI aparte).

Comprueba que la instalación es correcta:

```powershell
python -c "import agent; import benchmark; import analysis; print('OK')"
```

---

## 3. Configuración

El proyecto usa tres capas de configuración:

| Capa | Archivo | Contenido | Versionado |
|------|---------|-----------|------------|
| A - Secretos | `.env` | API keys, variables de entorno | No (gitignored) |
| B - Experimento | `configs/*.yaml` | dataset, agentes, `model_id`, overrides | Sí |
| C - Defaults | `src/agent/config.py`, `src/benchmark/config.py` | valores por defecto en código | Sí |

### 3.1 API keys (`.env`)

Copia la plantilla y rellena las claves del proveedor LLM que vayas a usar:

```powershell
copy .env.example .env
```

Abre `.env` con cualquier editor y añade los valores correspondientes:

```dotenv
# Gemini / Google (usa solo una de las dos)
GOOGLE_API_KEY=AIza...
GEMINI_API_KEY=

# OpenAI
OPENAI_API_KEY=sk-...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# SWE-bench (solo necesaria para la evaluación funcional del benchmark)
SWEBENCH_API_KEY=swe-...
```

> **Seguridad:** Nunca incluyas el fichero `.env` en un commit. Está en `.gitignore`
> por defecto. Nunca copies claves en ficheros `.yaml` ni en `config.py`.

El proyecto carga `.env` automáticamente al arrancar tanto el agente manual como el
benchmark; no es necesario exportar variables ni configurar el entorno del sistema.

### 3.2 Verificar las claves

Para comprobar que una clave se lee correctamente:

```powershell
python -c "from common.env import load_project_env; load_project_env(); import os; print(os.getenv('GOOGLE_API_KEY', 'NO ENCONTRADA'))"
```

Para comprobar la cuota de SWE-bench disponible:

```powershell
python -c "from common.env import load_project_env; load_project_env(); import subprocess, sys; raise SystemExit(subprocess.call(['sb-cli','get-quotas']))"
```

### 3.3 Ficheros de experimento (`configs/`)

Los experimentos del benchmark se definen en YAML bajo `configs/`. No existe un fichero
`.example` aparte: el repositorio ya incluye las configuraciones preparadas para el TFG.

| Fichero | Propósito |
|---------|-----------|
| `configs/experiment.smoke.yaml` | Validación mínima: **1 instancia** × 6 agentes (smoke de integración) |
| `configs/experiment.mini.yaml` | Pre-full: **5 instancias** estratificadas × 6 agentes (**24 runs**) |
| `configs/experiment.full.yaml` | **Experimento por defecto del TFG**: **120 instancias** Lite × 6 agentes (**560 runs**) |

El experimento **por defecto** del trabajo es `configs/experiment.full.yaml`
(`experiment_id_explicit: full-lite-120inst-6agents`). Los ficheros `smoke` y `mini`
sirven para comprobar el pipeline antes de lanzar el experimento principal o tras cambios de
configuración.

Los prompts y parámetros de mini-SWE-agent para cada agente viven en `configs/mini/`
(p. ej. `swebench_gemini_json.yaml` para el baseline y `swebench_gemini_json_robust.yaml`
para las variantes robustas).

**Para modificar un experimento**, edita el YAML correspondiente
(o duplícalo con otro nombre). Los campos más habituales son:

| Campo | Qué controla |
|-------|----------------|
| `dataset.instance_ids` | Lista fija de instancias (como en smoke) |
| `dataset.slice_size`, `slice_strategy`, `slice_seed` | Tamaño y muestreo del pool Lite (como en mini/full) |
| `agents[].model_id` | Modelo LLM de cada variante |
| `agents[].config_overrides` | Perfil robusto, umbrales, matriz del controlador |
| `benchmark.runs_root` | Carpeta de salida (por defecto `runs`) |
| `benchmark.workers` | Paralelismo de ejecución |
| `benchmark.evaluation_skip` | Si `true`, no llama a `sb-cli` (no consume cuota) |
| `benchmark.mini_agent_config` | YAML de prompts por defecto del benchmark |
| Bloques `x-gemini-agent` / `x-robust-base` | Parámetros compartidos vía anchors YAML (`model_id`, límites, clase del agente) |

Los campos omitidos toman los defaults de `src/benchmark/config.py`.

Variable opcional por agente: `api_key_env` - nombre de la variable de entorno con la
clave (no el valor). Si se omite, LiteLLM infiere la convención del proveedor desde
`model_id`.

### 3.4 Defaults en código (capa C)

**Agente** (`src/agent/config.py`): pesos de métricas, incertidumbre, matriz del
controlador, perfil `balanced`, etc. No edites este fichero para un experimento concreto;
usa `config_overrides` en el YAML o argumentos del CLI.

**Benchmark** (`src/benchmark/config.py`): `runs_root`, `workers`, timeouts de `sb-cli`,
etc. El YAML del experimento los sobreescribe.

**Prompts SWE-bench:** en experimentos reales configura
`benchmark.mini_agent_config` apuntando a
`src/agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench.yaml`
(pide `git diff` y envío del parche). Si es `null`, se usa `mini.yaml` del paquete
(no apto para evaluar parches en SWE-bench).

### 3.5 Qué modificar según el objetivo

| Objetivo | Dónde |
|----------|-------|
| API keys | `.env` |
| Modelo por celda del benchmark | `configs/*.yaml` → `agents[].model_id` |
| Dataset, workers, slice | `configs/*.yaml` → `dataset`, `benchmark` |
| Perfil robusto, umbrales, pesos | `configs/*.yaml` → `agents[].config_overrides` |
| Plantillas de prompt al LLM | `swebench.yaml` vía `benchmark.mini_agent_config` |
| Cambiar defaults globales del producto | `src/agent/config.py` o `src/benchmark/config.py` |

---

## 4. Ejecutar el benchmark (SWE-bench)

### 4.1 Requisito: Docker activo

El benchmark ejecuta cada agente dentro de un contenedor Linux con la imagen oficial
de la instancia SWE-bench. Docker debe estar corriendo antes de lanzar el benchmark.

**Windows:** abre Docker Desktop y espera a que aparezca "Docker Desktop is running".

**Linux:** comprueba que el daemon está activo:

```bash
sudo systemctl status docker
```

Verifica que Docker responde correctamente:

```powershell
docker info
```

La sección **Server** debe aparecer sin errores. Si aparece
`The system cannot find the file specified`, el daemon de Docker no está en ejecución.

> **Primera ejecución:** la primera vez que se ejecuta una instancia concreta, Docker
> descarga su imagen (varios GB). Por ejemplo, para el smoke run:
>
> ```powershell
> docker pull docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
> ```

### 4.2 Smoke run (validación mínima)

Antes de lanzar un experimento completo, ejecuta el smoke run para verificar que
el pipeline funciona de extremo a extremo. Usa 1 instancia × 6 agentes (ejecución
real, no simulada):

```powershell
python -m benchmark.run --config configs/experiment.smoke.yaml --force-rerun
```

El smoke run tiene `evaluation_skip: true` por defecto para no consumir cuota
`sb-cli`. Los resultados se guardan en `runs/`.

### 4.3 Experimento completo

```powershell
python -m benchmark.run --config configs/experiment.full.yaml
```

El benchmark es **resumible**: si se interrumpe, al relanzar sin `--force-rerun`
retoma desde el último run completado. Usa `--force-rerun` solo cuando se requiera
reiniciar desde cero:

```powershell
python -m benchmark.run --config configs/experiment.full.yaml --force-rerun
```

### 4.4 Opciones de línea de comandos

| Flag | Descripción |
|------|-------------|
| `--config PATH` | Ruta al YAML del experimento (obligatorio) |
| `--force-rerun` | Descarta runs anteriores y recomienza desde cero |
| `--experiment-id ID` | Sobreescribe `experiment_id_explicit` |
| `--runs-root PATH` | Sobreescribe `benchmark.runs_root` |
| `--evaluation-skip` | Omite la llamada a `sb-cli` (no consume cuota) |
| `--evaluate` | Fuerza la evaluación `sb-cli` aunque el YAML tenga `evaluation_skip: true` (fase 2 sobre runs ya en disco). No ejecuta ningún agente ni levanta Docker: es solo lectura respecto a la ejecución |
| `--evaluate-agents ID1,ID2` | Junto con `--evaluate`, limita la evaluación de esta invocación a esos `agent_id` (un perfil/ablación a la vez, para consumir cuota de `sb-cli` de forma controlada) |
| `--force-reevaluate-agents ID1,ID2` | Uso excepcional: junto con `--evaluate`, vuelve a someter a `sb-cli` esos `agent_id` aunque ya tengan `functional.status=evaluated` cacheado, bajo un `run_id` nuevo. Consume cuota adicional de forma deliberada; solo procede si se sospecha que el resultado previo es erróneo por un fallo del backend de `sb-cli` |

### 4.5 Resultados

Los artefactos de cada run se guardan en:

```
runs/<experiment_id>/<instance_id>/<agent_id>/<run_id>/
  run_record.json        # Metadatos del run: modelo, config, resultado
  trajectory.traj.json   # Traza completa del agente (pasos, decisiones, métricas)
  model.patch            # Parche git generado por el agente (si lo produjo)
  llm_debug.jsonl        # Intercambios LLM (si debug habilitado)
```

---

## 5. Ejecutar el agente manualmente (desarrollo)

El agente puede ejecutarse sobre cualquier repositorio git local sin necesidad de Docker
ni SWE-bench. Resulta útil para desarrollo, depuración y pruebas locales.

### 5.1 Smoke test sin LLM (modelo determinista)

```powershell
python -m agent.cli
```

Usa el modelo `deterministic` (respuestas fijas, sin API) sobre el repositorio de prueba
`tests/test_repo/`. La ejecución es inmediata y no requiere claves de API.

### 5.2 Con modelo LLM real

```powershell
python -m agent.cli --model gemini/gemini-2.0-flash --task "Fix add() in calc.py"
```

Requiere que la clave del proveedor esté en `.env`.

### 5.3 Opciones del CLI

| Flag | Por defecto | Descripción |
|------|-------------|-------------|
| `--model` | `deterministic` | Modelo LiteLLM (p. ej. `gemini/gemini-2.0-flash`, `gpt-4o`, `anthropic/claude-3-5-sonnet`) o `deterministic` |
| `--profile` | `balanced` | Perfil del agente: `strict`, `balanced` o `permissive` |
| `--task` | `"Find and solve the error"` | Enunciado de la tarea |
| `--repo` | `tests/test_repo/` | Ruta al repositorio git donde trabaja el agente |
| `--output` | `tests/agent/tmp/robust_agent_cli.traj.json` | Fichero de salida con la traza |
| `--step-limit` | `15` | Número máximo de pasos del agente |
| `--cost-limit` | `1.0` | Coste máximo acumulado en USD |
| `--shell` | `auto` | Intérprete de comandos: `auto`, `bash` o `cmd` |
| `--config` | `mini.yaml` del paquete | YAML de configuración de mini-swe-agent |
| `--api-key-env` | - | Nombre de la variable de entorno con la API key (opcional) |

### 5.4 Ejemplo: tarea sobre un repo propio

```powershell
python -m agent.cli `
    --repo C:\ruta\a\mi\repo `
    --model gpt-4o `
    --profile strict `
    --task "Refactoriza la función parse_csv para que maneje encoding UTF-8" `
    --step-limit 20
```

El resultado (traza JSON) se guarda en `--output` y el agente imprime un resumen
al terminar.

---

## 6. Ejecutar el pipeline de análisis (Bloque 3)

El sistema de análisis consume los resultados de un experimento completado y produce:

- `datos_consolidados.csv` - una fila por run (propios y externos).
- `datos_pasos_consolidados.csv` - una fila por paso de traza.
- `figures/*.png` - catálogo de figuras comparativas.

### 6.1 Prerequisito: experimento completado

Se requiere un árbol `runs/<experiment_id>/` producido por el benchmark (sección 4)
y, opcionalmente, los ficheros `extracted_metrics.jsonl` de los agentes externos
en `data/external_predictions/<source_id>/`.

### 6.2 Ejecución básica (solo runs propios)

```powershell
python -m analysis.run `
    --runs-root runs/full-lite-120inst-6agents `
    --output-dir data/full-lite-120inst-6agents
```

### 6.3 Ejecución completa (runs propios + 4 agentes externos)

```powershell
python -m analysis.run `
    --runs-root runs/full-lite-120inst-6agents `
    --output-dir data/full-lite-120inst-6agents `
    --external data/external_predictions/agentless_v1.5/extracted_metrics.jsonl `
    --external data/external_predictions/aider_20240523/extracted_metrics.jsonl `
    --external data/external_predictions/moatless_claude35_20241117/extracted_metrics.jsonl `
    --external data/external_predictions/openhands_claude35_20240725/extracted_metrics.jsonl
```

El pipeline es **idempotente**: relanzarlo regenera los CSV y figuras desde cero.

### 6.4 Opciones del CLI

| Flag | Por defecto | Descripción |
|------|-------------|-------------|
| `--runs-root PATH` | - | Árbol de resultados del benchmark (obligatorio) |
| `--output-dir PATH` | - | Directorio de salida para CSV y figuras (obligatorio) |
| `--external PATH` | - | `extracted_metrics.jsonl` de una fuente externa; repetible |
| `--no-diagrams` | desactivado | Solo produce los CSV, omite la generación de figuras |
| `--minimal-only` | desactivado | Genera solo el catálogo mínimo de figuras (S8.1) |

### 6.5 Artefactos de salida

```
data/<experiment_id>/
├── datos_consolidados.csv          # una fila por run (propios + externos)
├── datos_pasos_consolidados.csv    # una fila por paso de traza robust_agent
└── figures/
    ├── resolution_rate.png
    ├── cost_effort.png
    ├── patch_structure.png
    ├── external_agents.png
    └── ...                         # ~16 figuras en total
```

El directorio `data/` está en `.gitignore`; todo su contenido son artefactos
derivados o descargas de terceros reproducibles.

---

## 7. Tests

Ejecuta la suite completa de tests (agente, benchmark y análisis):

```powershell
python -m pytest tests -q
```

Para ejecutar solo un bloque concreto:

```powershell
python -m pytest tests/agent/ -q
python -m pytest tests/benchmark/ -q
python -m pytest tests/analysis/ -q
```

Para tests con salida detallada:

```powershell
python -m pytest tests -v
```

---

## 8. Referencia rápida

| Objetivo | Comando |
|----------|---------|
| Activar entorno virtual | `.\.venv\Scripts\Activate.ps1` |
| Instalar dependencias (agente, benchmark y análisis) | `pip install -e ".[dev]"` + `pip install -e src/agent/mini-swe-agent` |
| Smoke test sin API (agente manual) | `python -m agent.cli` |
| Agente con LLM real | `python -m agent.cli --model gemini/gemini-2.0-flash --task "..."` |
| Smoke run benchmark | `python -m benchmark.run --config configs/experiment.smoke.yaml --force-rerun` |
| Benchmark completo (TFG) | `python -m benchmark.run --config configs/experiment.full.yaml` |
| Pipeline de análisis (básico) | `python -m analysis.run --runs-root runs/exp --output-dir data/exp` |
| Pipeline de análisis (con externos) | `python -m analysis.run --runs-root runs/exp --output-dir data/exp --external data/external_predictions/agentless_v1.5/extracted_metrics.jsonl` |
| Tests completos | `python -m pytest tests -q` |
| Tests de un bloque | `python -m pytest tests/analysis/ -q` |
