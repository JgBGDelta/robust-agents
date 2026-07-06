"""Punto de entrada CLI del Bloque 1 — RobustAgent sobre repositorio local.

Permite ejecutar ``RobustAgent`` directamente sobre cualquier repositorio git
local sin pasar por el benchmark ni Docker. Util para pruebas manuales y smoke
tests del agente.

Uso:

    python -m agent.cli                                           # smoke test deterministico
    python -m agent.cli --model gemini/gemini-2.0-flash --task "Fix add() in calc.py"
    python -m agent.cli --repo /ruta/al/repo --profile strict --model gpt-4o

Equivale al anterior ``scripts/robust_agent_cli.py`` (eliminado en favor de este
patron coherente con ``python -m benchmark.run`` y ``python -m analysis.run``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# MSWEA_SILENT_STARTUP se establece en agent/__init__.py antes de los imports,
# por lo que mini-swe-agent no imprimira el emoji de bienvenida.

from agent.robust_agent import RobustAgent
from agent.environments import build_local_environment, describe_shell
from common.env import api_key_error_for_model, load_project_env, project_root
from common.mini_config import (
    base_agent_kwargs,
    build_litellm_model,
    load_mini_config,
    resolve_mini_config_path,
)
from minisweagent.models.test_models import DeterministicModel

_PROJECT_ROOT = project_root()
_DEFAULT_REPO = _PROJECT_ROOT / "tests" / "test_repo"
_DEFAULT_OUTPUT = _PROJECT_ROOT / "tests" / "agent" / "tmp" / "robust_agent_cli.traj.json"
_DEFAULT_TASK = "Find and solve the error"

_DEFAULT_DETERMINISTIC_OUTPUTS: list[dict[str, Any]] = [
    {
        "role": "assistant",
        "content": "Inspecting the repository.",
        "extra": {
            "actions": [{"command": "python calc.py"}],
            "cost": 0.01,
            "timestamp": time.time(),
        },
    }
]


def _parse_args() -> argparse.Namespace:
    """Auxiliar interno: parse args."""
    parser = argparse.ArgumentParser(
        description="Ejecuta RobustAgent sobre un repo git local.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=_DEFAULT_REPO,
        help=f"Directorio del repositorio git (default: {_DEFAULT_REPO})",
    )
    parser.add_argument(
        "--model",
        default="deterministic",
        help="Modelo LiteLLM (p. ej. gemini/gemini-2.0-flash) o 'deterministic'",
    )
    parser.add_argument(
        "--profile",
        choices=("strict", "balanced", "permissive"),
        default="balanced",
        help="Perfil del RobustAgent (default: balanced)",
    )
    parser.add_argument(
        "--task",
        default=_DEFAULT_TASK,
        help=f"Enunciado de la tarea (default: {_DEFAULT_TASK!r})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Ruta de la traza .traj.json (default: tests/agent/tmp/robust_agent_cli.traj.json)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML de mini-swe-agent (default: mini.yaml del paquete para modelos reales)",
    )
    parser.add_argument(
        "--api-key-env",
        default=None,
        help="Variable de entorno con la API key (opcional; por defecto convencion LiteLLM)",
    )
    parser.add_argument("--step-limit", type=int, default=15, help="Limite de pasos (default: 15)")
    parser.add_argument("--cost-limit", type=float, default=1.0, help="Limite de coste (default: 1.0)")
    parser.add_argument(
        "--shell",
        choices=("auto", "bash", "cmd"),
        default="auto",
        help="Interprete de comandos: auto (bash si disponible), bash o cmd (default: auto)",
    )
    return parser.parse_args()


def _build_model(
    model_name: str,
    mini_config: dict[str, Any],
    *,
    api_key_env: str | None,
) -> Any:
    """Auxiliar interno: build model."""
    if model_name == "deterministic":
        return DeterministicModel(outputs=_DEFAULT_DETERMINISTIC_OUTPUTS)
    return build_litellm_model(model_name, mini_config, api_key_env=api_key_env)


def main() -> int:
    """Punto de entrada de la CLI. Devuelve el exit code (0 = OK)."""
    load_project_env()
    args = _parse_args()
    repo = args.repo.resolve()
    output_path = args.output.resolve()

    if not (repo / ".git").is_dir():
        print(f"Error: {repo} no es un repositorio git. Ejecuta git init y un commit inicial.", file=sys.stderr)
        return 1

    use_deterministic = args.model == "deterministic"
    if not use_deterministic and (key_err := api_key_error_for_model(args.model, api_key_env=args.api_key_env)):
        print(f"Error: {key_err}", file=sys.stderr)
        return 1

    mini_config = load_mini_config(args.config)
    model = _build_model(args.model, mini_config, api_key_env=args.api_key_env)
    env_cfg = mini_config.get("environment", {})
    try:
        env = build_local_environment(cwd=str(repo), shell=args.shell, **env_cfg)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if use_deterministic:
        agent_cfg = {
            "system_template": "You are a coding agent.",
            "instance_template": "{{task}}",
            "profile": args.profile,
            "output_path": output_path,
            "step_limit": args.step_limit,
            "cost_limit": args.cost_limit,
        }
    else:
        agent_cfg = base_agent_kwargs(
            mini_config,
            cost_limit=args.cost_limit,
            step_limit=args.step_limit,
            output_path=output_path,
        )
        agent_cfg["profile"] = args.profile

    output_path.parent.mkdir(parents=True, exist_ok=True)
    agent = RobustAgent(model=model, env=env, **agent_cfg)

    print(f"Repo:    {repo}")
    print(f"Model:   {args.model}")
    print(f"Profile: {args.profile}")
    print(f"Shell:   {describe_shell(args.shell)}")
    print(f"Task:    {args.task}")
    print(f"Output:  {output_path}")
    if args.config:
        print(f"Config:  {resolve_mini_config_path(args.config)}")
    print("---")

    try:
        result = agent.run(args.task)
    except IndexError:
        print("Deterministic model exhausted outputs (expected smoke run).")
        if output_path.is_file():
            print(f"Trace saved to {output_path}")
            termination = json.loads(output_path.read_text(encoding="utf-8")).get("robust_agent", {}).get(
                "termination"
            )
            if termination:
                print(f"Termination: {termination}")
        return 0
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        if output_path.is_file():
            print(f"Partial trace: {output_path}")
        return 1

    print("Result:", result)
    print(f"Trace saved to {output_path}")
    if output_path.is_file():
        termination = json.loads(output_path.read_text(encoding="utf-8")).get("robust_agent", {}).get("termination")
        if termination:
            print(f"Termination: {termination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
