"""Carga centralizada de variables de entorno y comprobacion de API keys."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOADED = False


def project_root() -> Path:
    """Raiz del repositorio `robust-agents`."""
    return _PROJECT_ROOT


def load_project_env(*, override: bool = False) -> Path:
    """Carga secretos y variables del proyecto.

    Orden de precedencia (el ultimo archivo cargado no pisa claves ya definidas
    en el entorno del proceso salvo que ``override=True``):

    1. ``.env`` en la raiz del repositorio.
    2. ``.env`` global de mini-swe-agent (``%APPDATA%/mini-swe-agent/.env`` en Windows).

    Idempotente: llamadas repetidas no recargan salvo ``override=True``.
    """
    global _LOADED
    if _LOADED and not override:
        return _PROJECT_ROOT / ".env"

    project_env = _PROJECT_ROOT / ".env"
    load_dotenv(project_env, override=override)

    # Import diferido: evita el banner de mini-swe-agent en modulos que solo
    # necesitan el .env del proyecto.
    from minisweagent import global_config_file

    load_dotenv(global_config_file, override=override)

    _LOADED = True
    return project_env


def api_key_error_for_model(model_id: str, *, api_key_env: str | None = None) -> str | None:
    """Devuelve un mensaje de error si falta la API key; ``None`` si parece OK."""
    if api_key_env:
        if os.getenv(api_key_env):
            return None
        return (
            f"Falta la variable de entorno {api_key_env!r} "
            f"(definela en {project_root() / '.env'} o en el entorno)."
        )

    name = model_id.lower()
    if "gemini" in name or name.startswith("google/") or name.startswith("vertex_ai/gemini"):
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            return None
        return (
            "Falta la API key de Gemini. Define GOOGLE_API_KEY o GEMINI_API_KEY "
            f"en {project_root() / '.env'} o en el entorno."
        )
    if name.startswith("openai/") or name.startswith("gpt-"):
        if os.getenv("OPENAI_API_KEY"):
            return None
        return f"Falta OPENAI_API_KEY en {project_root() / '.env'} o en el entorno."
    if "anthropic" in name or name.startswith("claude"):
        if os.getenv("ANTHROPIC_API_KEY"):
            return None
        return f"Falta ANTHROPIC_API_KEY en {project_root() / '.env'} o en el entorno."
    return None


SWEBENCH_API_KEY_ENV = "SWEBENCH_API_KEY"


def swebench_api_key_error(*, api_key_env: str = SWEBENCH_API_KEY_ENV) -> str | None:
    """Devuelve un mensaje de error si falta la API key de sb-cli; ``None`` si parece OK."""
    if os.getenv(api_key_env):
        return None
    return (
        f"Falta la variable de entorno {api_key_env!r} "
        f"(definela en {project_root() / '.env'}). "
        "El benchmark la carga al arrancar con `python -m benchmark.run`; "
        "para `sb-cli` manual en otra consola, activa el entorno o usa ese comando."
    )
