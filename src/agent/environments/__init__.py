"""Entornos locales del agente robusto."""

from agent.environments.local_factory import (
    BashLocalEnvironment,
    build_local_environment,
    describe_shell,
    resolve_bash,
    resolve_shell_choice,
)

__all__ = [
    "BashLocalEnvironment",
    "build_local_environment",
    "describe_shell",
    "resolve_bash",
    "resolve_shell_choice",
]
