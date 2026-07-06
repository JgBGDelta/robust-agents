"""Extractor de `run_metrics` para `EvaluationResult.extended`.

Lee y normaliza la traza (`.traj.json`) de forma interna mediante
`ParsedTrace`, un tipo privado de este modulo.

Compatible con trayectorias de `DefaultAgent` (sin bloque `robust_agent`)
y de agentes robustos (con bloque `robust_agent`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord

# ---------------------------------------------------------------------------
# Tipo interno: traza parseada
# ---------------------------------------------------------------------------

ROBUST_TRACE_FORMAT = "robust-agent-1.0"

_EXIT_TO_TERMINATION: dict[str, str] = {
    "Submitted": "model_submitted",
    "submitted": "model_submitted",
    "LimitsExceeded": "limits_exceeded",
    "limits_exceeded": "limits_exceeded",
    # MaxStepsReached no existe en minisweagent; ambos limites usan LimitsExceeded.
    # Se mantiene por compatibilidad con trazas antiguas.
    "MaxStepsReached": "limits_exceeded",
}


@dataclass
class _ParsedTrace:
    """Vista normalizada de la traza. Tipo interno de este modulo."""

    available: bool = False
    not_applicable: bool = False
    error_reason: str | None = None
    trace_format: str | None = None
    episode_state_final: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    termination: str | None = None
    raw: dict[str, Any] | None = None


def _parse_trace(path: Path | None) -> _ParsedTrace:
    """Lee y normaliza una trayectoria. Nunca lanza al caller."""
    if not path:
        return _ParsedTrace(error_reason="trajectory_path_missing")
    if not path.is_file():
        return _ParsedTrace(error_reason="trajectory_unreadable")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _ParsedTrace(error_reason=f"trajectory_unreadable: {exc.__class__.__name__}")
    if not isinstance(raw, dict):
        return _ParsedTrace(error_reason="trajectory_unreadable: top-level not a dict")

    block = raw.get("robust_agent")
    if not isinstance(block, dict):
        return _ParsedTrace(not_applicable=True, raw=raw, error_reason="no_robust_agent_block")

    fmt = block.get("format_version")
    steps = block.get("steps") if isinstance(block.get("steps"), list) else []
    esf = block.get("episode_state_final") if isinstance(block.get("episode_state_final"), dict) else {}
    return _ParsedTrace(
        available=True,
        trace_format=str(fmt) if fmt is not None else None,
        episode_state_final=esf,
        steps=list(steps),
        termination=str(block["termination"]) if isinstance(block.get("termination"), str) else None,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Extractor publico
# ---------------------------------------------------------------------------

class RunMetricsExtractor:
    """Extrae `run_metrics` y detecta el `trace_format` para `EvaluationResult`."""

    @staticmethod
    def extract(
        *,
        trajectory_path: Path | None,
        run_record: BenchmarkRunRecord,
    ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        """Devuelve `(run_metrics, availability_entry, trace_format)`.

        `availability_entry` tiene la forma `{status, cause}` para la clave
        `run_metrics` del bloque `availability` de `extended`.
        """
        trace = _parse_trace(trajectory_path)

        avail = _availability(trace)

        # trajectory_format desde la traza cruda
        trajectory_format: str | None = None
        if trace.raw is not None:
            trajectory_format = trace.raw.get("trajectory_format")

        # exit_status: del record primero; si no, de la traza cruda
        exit_status = run_record.exit_status
        if exit_status is None and trace.raw is not None:
            info = trace.raw.get("info") or {}
            exit_status = info.get("exit_status")

        # termination: del bloque robust_agent o inferida
        if trace.available:
            termination = trace.termination
        else:
            termination = _infer_termination(exit_status)

        steps_used: int | None = None
        cost_total: float | None = None
        if trace.available:
            # Agente robusto: leer de episode_state_final.budget
            budget = trace.episode_state_final.get("budget") or {}
            raw_steps = budget.get("steps_used")
            if raw_steps is not None:
                try:
                    steps_used = int(raw_steps)
                except (TypeError, ValueError):
                    pass
            raw_cost = budget.get("cost_used")
            if raw_cost is not None:
                try:
                    cost_total = float(raw_cost)
                except (TypeError, ValueError):
                    pass
        elif trace.raw is not None:
            # Baseline (sin bloque robust_agent): leer de info.model_stats
            info = trace.raw.get("info") or {}
            stats = info.get("model_stats") or {}
            raw_steps = stats.get("api_calls")
            if raw_steps is not None:
                try:
                    steps_used = int(raw_steps)
                except (TypeError, ValueError):
                    pass
            raw_cost = stats.get("instance_cost")
            if raw_cost is not None:
                try:
                    cost_total = float(raw_cost)
                except (TypeError, ValueError):
                    pass

        metrics = {
            "trajectory_format": trajectory_format,
            "exit_status": exit_status,
            "termination": termination,
            "steps_used": steps_used,
            "cost_total": cost_total,
            "wallclock_seconds": run_record.duration_seconds,
        }
        return metrics, avail, trace.trace_format


def _availability(trace: _ParsedTrace) -> dict[str, Any]:
    """Auxiliar interno: availability."""
    if not trace.available and not trace.not_applicable:
        return {"status": "error", "cause": trace.error_reason or "trajectory_unreadable"}
    return {"status": "available", "cause": None}


def _infer_termination(exit_status: str | None) -> str | None:
    """Auxiliar interno: infer termination."""
    if exit_status is None:
        return None
    return _EXIT_TO_TERMINATION.get(exit_status, exit_status.lower().replace(" ", "_"))
