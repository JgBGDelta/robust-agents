"""Configuracion del bloque del benchmark.

Configuraciones declarativas consumidas por el `BenchmarkRunner` y los
cuatro modulos. Todas son `pydantic.BaseModel`.

Los perfiles del agente robusto se codifican en `agent_id`
(`robust_strict`, `robust_balanced`, `robust_permissive`) con
`config_overrides`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SliceStrategy = Literal["stratified_by_repo", "random", "sequential", "full"]
EvaluationBackend = Literal["sb_cli", "none"]


class DatasetConfig(BaseModel):
    """Configuracion declarativa del subconjunto experimental."""

    name: str = "SWE-bench/SWE-bench_Lite"
    subset: str = "lite"
    split: str = "test"
    slice_strategy: SliceStrategy = "stratified_by_repo"
    slice_size: int | None = None
    slice_seed: int = 42
    shuffle: bool = False
    instance_ids: list[str] | None = None
    """Si se define, solo se conservan estas instancias (tras validacion)."""


class AgentRunConfig(BaseModel):
    """Configuracion de una celda de la matriz experimental.

    El perfil del agente robusto va codificado en `agent_id`
    (`robust_balanced`, `robust_strict`, `robust_permissive`) con los
    parametros de perfil en `config_overrides`.
    """

    agent_id: str
    agent_class: str
    model_id: str
    environment_class: str = "minisweagent.environments.docker.DockerEnvironment"
    cost_limit: float | None = None
    step_limit: int | None = None
    seed: int | None = None
    api_key_env: str | None = None
    """Nombre de la variable de entorno con la API key (p. ej. `GOOGLE_API_KEY`).

    Si es ``None``, LiteLLM usa la convencion estandar del proveedor inferida
    desde ``model_id``. El valor nunca va en el YAML del experimento.
    """
    mini_agent_config: str | None = None
    """Ruta al YAML de mini-swe-agent especifica para este agente.

    Permite a cada entrada de la lista ``agents:`` usar prompts o
    parametros de modelo distintos (p.ej. ablacion con controlador
    desactivado, variante v2 con umbrales distintos).
    Si es ``None``, hereda el valor global de ``BenchmarkConfig.mini_agent_config``.
    """
    dataset_slice_size: int | None = None
    """Sub-slice del pool global solo para este agente.

    Si se define, este agente solo corre en las primeras ``dataset_slice_size``
    instancias seleccionadas del pool global mediante la misma ``slice_strategy``
    y ``slice_seed`` del ``DatasetConfig`` del experimento. Permite mezclar en
    un mismo fichero agentes principales (pool completo) con ablaciones (subset).
    ``None`` = usa todas las instancias del pool global.
    """
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class BenchmarkConfig(BaseModel):
    """Parametros operativos transversales del benchmark."""

    runs_root: str = "runs"
    workers: int = 4
    inter_run_delay_seconds: int = 0
    """Segundos de espera entre runs consecutivos. Util para evitar que runs
    con contextos grandes agoten el cupo de tokens/minuto de la API y causen
    429s en cascada al inicio del run siguiente. Con workers=1, un valor de 60
    garantiza que la ventana de 1M tokens/minuto de Gemini se resetee."""
    retry_policy: dict[str, Any] = Field(
        default_factory=lambda: {"max_retries": 1, "transient_errors_only": True}
    )
    functional_backend: EvaluationBackend = "sb_cli"
    evaluation_skip: bool = False
    """Si es ``True``, omite la evaluacion funcional (`sb-cli`) pero conserva metricas locales."""
    sb_cli_timeout_seconds: int = 1800
    sb_cli_poll_interval_seconds: int = 10
    benchmark_format_version: str = "robust-benchmark-1.0"
    mini_agent_config: str | None = None
    """Ruta opcional al YAML de mini-swe-agent (prompts). ``None`` = ``mini.yaml`` del paquete."""
    disk_min_free_gb: float = 10.0
    """Espacio libre minimo (GB) requerido antes de arrancar cada instancia Docker.

    Si el disco donde vive el VHDX de Docker Desktop (normalmente C:) tiene menos
    espacio libre que este valor, el benchmark se detiene antes de descargar la
    imagen, evitando fallos en cadena por disco lleno.

    El valor se comprueba en ambos modos (secuencial y paralelo). Se evalua sobre
    el directorio temporal del sistema (``tempfile.gettempdir()``) si Docker usa
    WSL2/VHDX en Windows, o sobre ``/var/lib/docker`` en Linux.

    Recomendaciones:
    - Ejecucion local en Windows (VHDX en C:): ``10.0`` como minimo; ``15.0`` si
      hay imagenes matplotlib (~12 GB c/u).
    - Cambia a ``0.0`` para deshabilitar la comprobacion (no recomendado).
    """


class ExperimentConfig(BaseModel):
    """Configuracion completa de un lote del benchmark."""

    experiment_id_explicit: str | None = None
    dataset: DatasetConfig = Field(default_factory=DatasetConfig)
    agents: list[AgentRunConfig] = Field(default_factory=list)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    force_rerun: bool = False
