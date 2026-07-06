"""Punto de entrada CLI del Bloque 2.

Permite lanzar un experimento desde linea de comandos:

```
python -m benchmark.run --config experiment.yaml
```

`experiment.yaml` debe contener un `ExperimentConfig` serializado en
YAML. Las claves se mapean directamente a campos pydantic; valores
ausentes toman defaults de `BenchmarkConfig`/`DatasetConfig`.

Las API keys **no** van en el YAML: cargar `.env` via `common.env.load_project_env`
(ver `docs/manual-usuario.md` y `configs/experiment.example.yaml`).

Flags adicionales:

  - `--force-rerun`: ignora reanudacion parcial y archiva el directorio
    previo en `.bak-<timestamp>`.
  - `--experiment-id <id>`: fija el `experiment_id_explicit`.
  - `--runs-root <path>`: sobreescribe `benchmark.runs_root`.
  - `--evaluation-skip`: omite `sb-cli` (metricas locales siempre).
  - `--evaluate`: fuerza la evaluacion sb-cli aunque el YAML tenga
    `evaluation_skip: true` (fase 2 sobre runs ya en disco). Implica
    evaluation-only: NO se ejecuta ningun agente ni se levanta Docker, solo
    se evaluan los `BenchmarkRunRecord` ya persistidos.
  - `--evaluate-agents <id1>,<id2>,...`: junto con `--evaluate`, restringe la
    evaluacion de esta invocacion a esos `agent_id` (para someter a `sb-cli`
    un perfil/ablacion a la vez y controlar el gasto de cuota).
  - `--force-reevaluate-agents <id1>,<id2>,...`: uso excepcional, junto con
    `--evaluate`; vuelve a someter a `sb-cli` estos `agent_id` aunque ya
    tengan `functional.status=evaluated` cacheado, bajo un `run_id` nuevo.
    Gasta cuota de nuevo deliberadamente (ver P19).

El comando imprime el `BenchmarkExport` resultante en JSON sobre
stdout, util para encadenar con el Bloque 3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

import litellm
import yaml

from benchmark.benchmark_runner import BenchmarkRunner
from benchmark.config import ExperimentConfig
from benchmark.execution_module.execution_module import BenchmarkExecutionModule
from benchmark.shutdown import clear_stop, force_process_exit, request_stop
from common.env import load_project_env

_RETRY_DELAY_RE = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')
_BUFFER_SECONDS = 2  # margen sobre el retryDelay recomendado por Gemini

# Contador de reintentos consecutivos de ~60s: cuando llega a este umbral se
# considera que la quota esta agotada de forma no recuperable y se detiene el
# benchmark limpiamente (equivalente a Ctrl+C manual).
_consecutive_max_retries: int = 0
_MAX_CONSECUTIVE_60S_RETRIES: int = 3


def _on_rate_limit_error(kwargs: Any, *_: Any) -> None:  # noqa: ANN401
    """Callback de LiteLLM: ajusta retry_after con el retryDelay de Gemini y
    detecta bloqueos no recuperables por quota agotada.

    1. Extrae ``RetryInfo.retryDelay`` del JSON de error y actualiza
       ``litellm.retry_after`` para respetar la ventana recomendada por la API.
    2. Cuenta reintentos consecutivos con delay de ~60s (backoff maximo de
       LiteLLM). Cuando se alcanzan ``_MAX_CONSECUTIVE_60S_RETRIES`` seguidos,
       lanza ``KeyboardInterrupt`` para detener el benchmark limpiamente, igual
       que un Ctrl+C manual, evitando seguir consumiendo TPM inutil.
    """
    global _consecutive_max_retries

    exc = kwargs.get("exception")
    if exc is None:
        return
    try:
        text = str(exc)
        match = _RETRY_DELAY_RE.search(text)
        if match:
            delay = float(match.group(1))
            recommended = delay + _BUFFER_SECONDS
            if recommended > litellm.retry_after:
                litellm.retry_after = recommended
            # Acumular si el delay recomendado por Gemini es maximo (~60s)
            if delay >= 55.0:
                _consecutive_max_retries += 1
            else:
                _consecutive_max_retries = 0
        else:
            # Error 429 sin retryDelay explicito: contar como reintento maximo
            # (suele indicar quota diaria agotada sin ventana de recuperacion)
            _consecutive_max_retries += 1

        if _consecutive_max_retries >= _MAX_CONSECUTIVE_60S_RETRIES:
            print(
                f"\n[benchmark] STOP: {_consecutive_max_retries} reintentos "
                f"consecutivos de 60s detectados. Quota probablemente agotada. "
                f"Deteniendo benchmark para no seguir consumiendo TPM. "
                f"Reanuda manana con: python -m benchmark.run --config <config>",
                flush=True,
            )
            request_stop()
            raise KeyboardInterrupt("quota_exhausted_stop")
    except KeyboardInterrupt:
        request_stop()
        raise
    except Exception:  # noqa: BLE001
        pass


def _on_llm_success(*_: Any) -> None:
    """Callback de LiteLLM: resetea el contador de reintentos en exito."""
    global _consecutive_max_retries
    _consecutive_max_retries = 0


def _install_sigint_handler() -> None:
    """Ctrl+C activa parada global del experimento (no solo del run actual)."""

    def _handler(signum: int, frame: Any) -> None:  # noqa: ANN401, ARG001
        """Solicita parada global y relanza `KeyboardInterrupt`."""
        request_stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)


def _should_fallback_to_studio(exc: BaseException) -> bool:
    """True si conviene reintentar la llamada via AI Studio (``gemini/``)."""
    if isinstance(
        exc,
        (
            litellm.RateLimitError,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
            litellm.InternalServerError,
        ),
    ):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("429", "resource_exhausted", "quota", "rate limit"))


def _setup_vertex_routing() -> None:
    """Enruta ``gemini/*`` a Vertex AI primero, con AI Studio como fallback.

    Wrapper thread-safe sobre ``litellm.completion`` (sin LiteLLM Router, que
    usa asyncio y falla con ``workers > 1``).
    """
    project = os.getenv("VERTEXAI_PROJECT", "").strip()
    location = os.getenv("VERTEXAI_LOCATION", "us-central1").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()

    if not project:
        print(
            "[benchmark] VERTEXAI_PROJECT no configurado: usando AI Studio directamente.",
            flush=True,
        )
        return

    _orig_completion = litellm.completion

    def _routed_completion(model: str, *args: Any, **kwargs: Any) -> Any:
        """Auxiliar interno: routed completion."""
        if not model.startswith("gemini/"):
            return _orig_completion(model, *args, **kwargs)

        model_suffix = model.split("/", 1)[1]
        vertex_kwargs = {
            **kwargs,
            "vertex_project": project,
            "vertex_location": location,
        }
        try:
            return _orig_completion(f"vertex_ai/{model_suffix}", *args, **vertex_kwargs)
        except Exception as exc:
            if not _should_fallback_to_studio(exc):
                raise
            studio_kwargs = dict(kwargs)
            if gemini_key:
                studio_kwargs["api_key"] = gemini_key
            return _orig_completion(model, *args, **studio_kwargs)

    litellm.completion = _routed_completion  # type: ignore[method-assign]

    print(
        f"[benchmark] Vertex AI activo como opcion principal "
        f"(proyecto: {project}, region: {location}). "
        f"Fallback a AI Studio si Vertex AI agota cuota.",
        flush=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Auxiliar interno: build parser."""
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.run",
        description="Ejecuta un experimento del Bloque 2 (benchmark extendido).",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Ruta al YAML con el ExperimentConfig.",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Desactiva la reanudacion parcial y archiva el directorio previo.",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="experiment_id_explicit; sobreescribe la generacion hibrida.",
    )
    parser.add_argument(
        "--runs-root",
        default=None,
        help="Sobreescribe benchmark.runs_root.",
    )
    parser.add_argument(
        "--evaluation-skip",
        action="store_true",
        help="Omite la evaluacion funcional sb-cli (no consume cuota).",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help=(
            "Fuerza la evaluacion sb-cli aunque el YAML tenga evaluation_skip: true. "
            "Implica evaluation-only: no ejecuta ningun agente, solo evalua lo ya "
            "persistido en disco."
        ),
    )
    parser.add_argument(
        "--evaluate-agents",
        default=None,
        metavar="AGENT_ID[,AGENT_ID...]",
        help=(
            "Limita la evaluacion funcional (sb-cli) de esta invocacion a estos "
            "agent_id (lista separada por comas). No afecta a la ejecucion de "
            "agentes ni se persiste en config.yaml: solo restringe cuantos grupos "
            "se someten a sb-cli en esta pasada, para gastar cuota de forma "
            "controlada perfil a perfil. Requiere --evaluate."
        ),
    )
    parser.add_argument(
        "--force-reevaluate-agents",
        default=None,
        metavar="AGENT_ID[,AGENT_ID...]",
        help=(
            "Uso excepcional: vuelve a someter a sb-cli estos agent_id aunque ya "
            "tengan functional.status=evaluated cacheado, bajo un run_id nuevo "
            "(sb-cli bloquea permanentemente las instancias ya sometidas a un "
            "run_id). Consume cuota adicional de forma deliberada; solo tiene sentido si "
            "se sospecha que el resultado 'evaluated' previo es erróneo por un fallo "
            "del backend de sb-cli (ver modulo_evaluacion.md seccion 3). Implica "
            "inclusion en el alcance de evaluacion de esta pasada. Requiere "
            "--evaluate."
        ),
    )
    return parser


def _load_experiment_config(path: Path) -> ExperimentConfig:
    """Lee `path` (YAML) y construye un `ExperimentConfig`."""
    if not path.is_file():
        raise FileNotFoundError(f"--config no apunta a un archivo: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"--config debe contener un mapping en su nivel superior, encontrado: {type(raw).__name__}"
        )
    return ExperimentConfig.model_validate(raw)


def _apply_cli_overrides(
    config: ExperimentConfig,
    *,
    force_rerun: bool,
    experiment_id: str | None,
    runs_root: str | None,
    evaluation_skip: bool,
    evaluate: bool,
) -> ExperimentConfig:
    """Aplica los overrides simples de CLI sobre el `ExperimentConfig`.

    Pydantic permite construir copias inmutables con `model_copy`; los
    sub-modelos se sustituyen explicitamente cuando hace falta.
    """
    update: dict[str, Any] = {}
    if force_rerun:
        update["force_rerun"] = True
    if experiment_id is not None:
        update["experiment_id_explicit"] = experiment_id
    new_cfg = config.model_copy(update=update) if update else config
    if runs_root is not None:
        new_benchmark = new_cfg.benchmark.model_copy(update={"runs_root": runs_root})
        new_cfg = new_cfg.model_copy(update={"benchmark": new_benchmark})
    if evaluation_skip:
        new_benchmark = new_cfg.benchmark.model_copy(update={"evaluation_skip": True})
        new_cfg = new_cfg.model_copy(update={"benchmark": new_benchmark})
    elif evaluate:
        new_benchmark = new_cfg.benchmark.model_copy(update={"evaluation_skip": False})
        new_cfg = new_cfg.model_copy(update={"benchmark": new_benchmark})
    return new_cfg


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la CLI. Devuelve el exit code (0 = OK)."""
    load_project_env()
    clear_stop()
    _install_sigint_handler()
    litellm.retry_after = 10  # espera minima (s) entre reintentos por 429
    litellm.failure_callback = [_on_rate_limit_error]
    litellm.success_callback = [_on_llm_success]
    _setup_vertex_routing()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.evaluate_agents and not args.evaluate:
        parser.error("--evaluate-agents requiere --evaluate")
    if args.force_reevaluate_agents and not args.evaluate:
        parser.error("--force-reevaluate-agents requiere --evaluate")

    only_agent_ids = (
        frozenset(a.strip() for a in args.evaluate_agents.split(",") if a.strip())
        if args.evaluate_agents
        else None
    )
    force_reevaluate_agents = (
        frozenset(a.strip() for a in args.force_reevaluate_agents.split(",") if a.strip())
        if args.force_reevaluate_agents
        else None
    )
    if force_reevaluate_agents:
        # Forzar reevaluacion implica estar en el alcance de esta pasada,
        # aunque no se haya listado explicitamente en --evaluate-agents.
        only_agent_ids = (
            force_reevaluate_agents
            if only_agent_ids is None
            else only_agent_ids | force_reevaluate_agents
        )

    cfg = _load_experiment_config(args.config)
    cfg = _apply_cli_overrides(
        cfg,
        force_rerun=args.force_rerun,
        experiment_id=args.experiment_id,
        runs_root=args.runs_root,
        evaluation_skip=args.evaluation_skip,
        evaluate=args.evaluate,
    )

    runner = BenchmarkRunner(cfg)
    try:
        export = runner.run(
            only_agent_ids=only_agent_ids,
            evaluation_only=args.evaluate,
            force_reevaluate_agents=force_reevaluate_agents,
        )
    except KeyboardInterrupt:
        request_stop()
        BenchmarkExecutionModule._cleanup_orphaned_containers()
        print(
            "\n[benchmark] Experimento interrumpido (Ctrl+C o quota agotada)."
            "\nLos runs completados quedan guardados en disco."
            "\nReanuda con:  python -m benchmark.run --config <config>",
            flush=True,
        )
        force_process_exit(130)

    json.dump(export.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
