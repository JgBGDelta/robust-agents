"""Helpers compartidos para cargar `mini.yaml` e instanciar modelos/agentes."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from common.env import api_key_error_for_model


def resolve_mini_config_path(path: str | Path | None = None) -> Path:
    """Resuelve la ruta al YAML de mini-swe-agent usado como base de prompts."""
    if path is None:
        from minisweagent import package_dir

        return package_dir / "config" / "mini.yaml"
    candidate = Path(path)
    if candidate.is_file():
        return candidate.resolve()
    from common.env import project_root

    rooted = project_root() / candidate
    if rooted.is_file():
        return rooted.resolve()
    raise FileNotFoundError(f"No se encontro el YAML de mini-swe-agent: {path}")


def load_mini_config(path: str | Path | None = None) -> dict[str, Any]:
    """Lee y parsea el YAML de mini-swe-agent."""
    resolved = resolve_mini_config_path(path)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"El YAML de mini-swe-agent debe ser un mapping: {resolved}")
    return raw


def import_agent_class(agent_class: str) -> type:
    """Importa dinamicamente la clase de agente indicada por ruta dotted."""
    module_name, class_name = agent_class.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def build_litellm_model(
    model_id: str,
    mini_config: dict[str, Any],
    *,
    api_key_env: str | None = None,
) -> Any:
    """Construye un LitellmModel (o subclase) tras validar que exista la API key.

    Si el bloque ``model:`` del YAML incluye ``model_class``, se instancia esa
    clase en lugar de ``LitellmModel`` por defecto. Esto permite inyectar
    variantes externas (p. ej. ``JsonBashModel``) sin tocar mini-swe-agent.
    """
    if err := api_key_error_for_model(model_id, api_key_env=api_key_env):
        raise ValueError(err)

    model_cfg = dict(mini_config.get("model", {}))
    # El model_id del experimento manda sobre el default del YAML.
    model_cfg.pop("model_name", None)
    # model_class es un campo propio de este proyecto, no de LitellmModelConfig.
    model_class_path = model_cfg.pop("model_class", None)

    if model_class_path:
        model_cls = import_agent_class(model_class_path)
    else:
        from minisweagent.models.litellm_model import LitellmModel
        model_cls = LitellmModel

    return model_cls(model_name=model_id, **model_cfg)


def base_agent_kwargs(
    mini_config: dict[str, Any],
    *,
    cost_limit: float | None = None,
    step_limit: int | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Extrae kwargs base del bloque `agent` de mini.yaml mas limites opcionales."""
    kwargs = dict(mini_config.get("agent", {}))
    if cost_limit is not None:
        kwargs["cost_limit"] = cost_limit
    if step_limit is not None:
        kwargs["step_limit"] = step_limit
    if output_path is not None:
        kwargs["output_path"] = output_path
    return kwargs
