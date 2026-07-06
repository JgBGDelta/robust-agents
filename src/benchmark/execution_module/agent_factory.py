"""`AgentFactory` del modulo de ejecucion.

Punto unico donde:
- se instancia el modelo (`LitellmModel`) y el agente (`RobustAgent`
  o baseline `DefaultAgent`),
- se construye el `RobustAgentConfig` aplicando la regla de `EA.5.2`:
  cuando el agente es `RobustAgent` con perfil `strict` o `balanced`,
  se pueblan `controller.tests_validation_enabled = True` y
  `controller.forbidden_test_ids = fail_to_pass + pass_to_pass`.
  Para el perfil `strict` se activa adicionalmente
  `controller.self_review_enabled = True`, lo que materializa la
  validacion `combined` (tests + auto-revision) especificada en `EA.5.4`.

El perfil se resuelve en este orden de prioridad:
  1. `config_overrides.get("profile")` (anulacion explicita en YAML).
  2. Sufijo del `agent_id` (`robust_balanced` -> `balanced`).

Inyectable: tests proveen un `build_fn` que devuelve un agente fake
con `run`/`serialize` controlados.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from benchmark.config import AgentRunConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from common.mini_config import (
    base_agent_kwargs,
    build_litellm_model,
    import_agent_class,
    load_mini_config,
)


class AgentLike(Protocol):
    """Protocolo minimo que el `RunExecutor` consume del agente."""

    def run(self, task: str = "") -> dict[str, Any]:
        """Ejecuta el agente sobre la tarea y devuelve el resultado."""
        ...

    def serialize(self) -> dict[str, Any]:
        """Devuelve el estado serializable del agente (traza, metricas, etc.)."""
        ...


BuildAgentFn = Callable[[BenchmarkInstance, AgentRunConfig, Any], AgentLike]
"""Callable inyectable que construye `(agente)` a partir de `(instance, config, env)`."""

_VALIDATION_PROFILES = frozenset({"strict", "balanced"})


def _profile_from_config(agent_run_config: AgentRunConfig) -> str | None:
    """Resuelve el perfil del agente: `config_overrides["profile"]` o sufijo del `agent_id`."""
    explicit = agent_run_config.config_overrides.get("profile")
    if explicit:
        return str(explicit)
    for candidate in ("_strict", "_balanced", "_permissive"):
        if agent_run_config.agent_id.endswith(candidate):
            return candidate.lstrip("_")
    return None


class AgentFactory:
    """Construye agentes para los runs."""

    def __init__(
        self,
        *,
        build_fn: BuildAgentFn | None = None,
        mini_config_path: str | Path | None = None,
    ) -> None:
        """Almacena el callable de construccion (default real, fake en tests)."""
        self._build_fn = build_fn
        self._mini_config_path = mini_config_path

    def build(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        env: Any,
        output_path: Path | None = None,
    ) -> AgentLike:
        """Construye el agente listo para ejecutarse."""
        if self._build_fn is not None:
            return self._build_fn(instance, agent_run_config, env)

        # El override por agente tiene prioridad sobre el global del experimento.
        config_path = agent_run_config.mini_agent_config or self._mini_config_path
        mini_config = load_mini_config(config_path)
        model = build_litellm_model(
            agent_run_config.model_id,
            mini_config,
            api_key_env=agent_run_config.api_key_env,
        )
        agent_cls = import_agent_class(agent_run_config.agent_class)
        kwargs = base_agent_kwargs(
            mini_config,
            cost_limit=agent_run_config.cost_limit,
            step_limit=agent_run_config.step_limit,
            output_path=output_path,
        )
        if "RobustAgent" in agent_run_config.agent_class:
            kwargs.update(
                AgentFactory.build_robust_agent_overrides(
                    agent_run_config=agent_run_config,
                    instance=instance,
                )
            )
        elif agent_run_config.config_overrides:
            kwargs.update(agent_run_config.config_overrides)
        return agent_cls(model, env, **kwargs)

    @staticmethod
    def should_enable_tests_validation(agent_run_config: AgentRunConfig) -> bool:
        """Devuelve `True` si toca poblar `forbidden_test_ids`.

        Solo activa para `RobustAgent` con perfil `strict` o `balanced`.
        El perfil se resuelve desde `config_overrides["profile"]` o del
        sufijo del `agent_id`.
        """
        if "RobustAgent" not in agent_run_config.agent_class:
            return False
        profile = _profile_from_config(agent_run_config)
        return profile in _VALIDATION_PROFILES

    @staticmethod
    def forbidden_test_ids(instance: BenchmarkInstance) -> list[str]:
        """Devuelve la lista canonica para `RobustAgentConfig.controller.forbidden_test_ids`."""
        return list(instance.fail_to_pass) + list(instance.pass_to_pass)

    @staticmethod
    def build_robust_agent_overrides(
        *,
        agent_run_config: AgentRunConfig,
        instance: BenchmarkInstance,
    ) -> dict[str, Any]:
        """Devuelve los overrides a aplicar sobre `RobustAgentConfig`."""
        overrides: dict[str, Any] = dict(agent_run_config.config_overrides)
        profile = _profile_from_config(agent_run_config) or "balanced"
        overrides["profile"] = profile
        if agent_run_config.cost_limit is not None:
            overrides["cost_limit"] = agent_run_config.cost_limit
        if agent_run_config.step_limit is not None:
            overrides["step_limit"] = agent_run_config.step_limit
        if AgentFactory.should_enable_tests_validation(agent_run_config):
            controller = dict(overrides.get("controller", {}))
            controller["tests_validation_enabled"] = True
            controller["forbidden_test_ids"] = AgentFactory.forbidden_test_ids(instance)
            if profile == "strict":
                controller["self_review_enabled"] = True
            overrides["controller"] = controller
        return overrides
