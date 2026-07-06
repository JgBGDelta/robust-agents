"""Reporter de progreso en tiempo real para ejecuciones del benchmark.

Escribe a stderr para no interferir con la salida JSON en stdout.
Diseñado para ser inyectado en ``BenchmarkExecutionModule.run_matrix``
como parámetro opcional; el módulo de ejecución lo invoca en los puntos
clave sin depender del formato concreto de la salida.
"""

from __future__ import annotations

import datetime
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord


@dataclass
class EvalSummary:
    """Resumen de la evaluación funcional para el resumen final.

    Se construye en el runner a partir de los ``EvaluationResult`` y se
    pasa a ``RunProgress.print_summary`` para mostrarlo en la sección de
    evaluación del resumen.
    """

    evaluation_skip: bool = False
    """Si la evaluación funcional estaba desactivada."""
    groups_evaluated: int = 0
    """Número de grupos (agent_id) enviados a sb-cli."""
    groups_error: int = 0
    """Grupos con ``evaluation_error``."""
    resolved: int = 0
    """Instancias marcadas como resueltas por sb-cli."""
    not_resolved: int = 0
    """Instancias evaluadas y no resueltas."""
    eval_errors: int = 0
    """Instancias con ``evaluation_error`` en su resultado."""
    quota_remaining: int | None = None
    """Cuota sb-cli restante tras el experimento (None si no se pudo consultar)."""
    quota_error: str | None = None
    """Motivo si no se pudo obtener la cuota."""


class RunProgress:
    """Rastrea y muestra el progreso de la matriz de runs del benchmark.

    Thread-safe: puede usarse con ``workers > 1`` (ejecución en paralelo).

    Parámetros
    ----------
    total:
        Número total de runs en la matriz (instancias × agentes),
        incluidos los ya completados en sesiones anteriores.
    already_done:
        Runs completados antes de iniciar esta sesión (reanudación parcial).
    """

    def __init__(self, total: int, already_done: int = 0) -> None:
        """Inicializa `RunProgress`."""
        self._total = total
        self._already_done = already_done
        self._session_start = time.monotonic()
        self._completed = 0
        self._errors = 0
        self._durations: list[float] = []
        self._costs: list[float] = []
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Hooks llamados por el módulo de ejecución

    def on_run_start(self, session_index: int, instance_id: str, agent_id: str) -> None:
        """Imprime encabezado al iniciar el run ``session_index`` (0-based)."""
        current = self._already_done + session_index + 1
        elapsed = _fmt_duration(time.monotonic() - self._session_start)
        started_at = datetime.datetime.now().strftime("%H:%M:%S")
        _print(
            f"\n{'-'*60}\n"
            f"[{current}/{self._total}]  {instance_id}  /  {agent_id}\n"
            f"  elapsed: {elapsed}  |  started: {started_at}"
        )

    def on_run_complete(self, record: "BenchmarkRunRecord") -> None:
        """Imprime resultado al terminar un run y actualiza contadores internos."""
        with self._lock:
            duration = record.duration_seconds or 0.0
            cost = record.cost_usd or 0.0
            self._durations.append(duration)
            self._costs.append(cost)
            self._completed += 1
            is_error = record.status == "failed" or bool(record.error)
            if is_error:
                self._errors += 1

            icon = "[OK]" if record.status == "completed" else "[!!]"
            patch_str = _patch_size_str(record.model_patch_path)
            remaining_session = (self._total - self._already_done) - self._completed
            eta_str = _fmt_eta(self._durations, remaining_session)
            total_so_far = sum(self._costs)
            error_tag = f"  error={record.error.get('type','?')}" if record.error else ""

            _print(
                f"  {icon}  exit={record.exit_status or record.status}"
                f"  dur={_fmt_duration(duration)}"
                f"  cost=${cost:.4f}"
                f"  total=${total_so_far:.4f}"
                f"  patch={patch_str}"
                f"  ETA~{eta_str}"
                f"{error_tag}"
            )

    # ------------------------------------------------------------------
    # Resumen final

    def print_summary(
        self,
        records: list["BenchmarkRunRecord"],
        eval_summary: "EvalSummary | None" = None,
    ) -> None:
        """Imprime el resumen completo al terminar todos los runs."""
        total_elapsed = time.monotonic() - self._session_start
        session_runs = len(self._durations)

        # Estadísticas de la sesión
        avg_dur = sum(self._durations) / session_runs if session_runs else 0.0
        min_dur = min(self._durations) if self._durations else 0.0
        max_dur = max(self._durations) if self._durations else 0.0

        # Contadores de exit_status de TODOS los records pasados (sesión actual)
        status_counts: dict[str, int] = {}
        empty = 0
        contaminated = 0
        for r in records:
            key = r.exit_status or r.status
            status_counts[key] = status_counts.get(key, 0) + 1
            if r.exit_status in {"EmptySubmission", "ControllerFinalize"} and r.status == "failed":
                empty += 1
            if r.contamination_detected:
                contaminated += 1

        # Errores de ejecución (status==failed o campo error poblado)
        exec_errors = sum(1 for r in records if r.status == "failed" or bool(r.error))

        _print(f"\n{'='*60}")
        _print(f"  RESUMEN DEL EXPERIMENTO")
        _print(f"{'='*60}")
        _print(f"  Runs totales en matriz : {self._total}")
        _print(f"  Runs ejecutados (sesion): {session_runs}")
        if self._already_done:
            _print(f"  Reutilizados (previo)  : {self._already_done}")
        total_cost = sum(self._costs)
        avg_cost = total_cost / session_runs if session_runs else 0.0

        _print(f"")
        _print(f"  Tiempo total sesion    : {_fmt_duration(total_elapsed)}")
        if session_runs:
            _print(f"  Duracion media/run     : {_fmt_duration(avg_dur)}")
            _print(f"  Duracion min / max     : {_fmt_duration(min_dur)} / {_fmt_duration(max_dur)}")
        _print(f"")
        _print(f"  Coste sesion (USD)     : ${total_cost:.4f}")
        if session_runs:
            _print(f"  Coste medio/run        : ${avg_cost:.4f}")
        _print(f"")
        _print(f"  Exit status:")
        for key, count in sorted(status_counts.items()):
            _print(f"    {key:<30} {count}")
        _print(f"")
        if exec_errors:
            _print(f"  [!!] Errores de ejecucion : {exec_errors}")
        if empty:
            _print(f"  [!!] Parches vacios       : {empty}")
        if contaminated:
            _print(f"  [!!] Contaminacion detect.: {contaminated}")
        if not exec_errors and not empty and not contaminated:
            _print(f"  [OK] Sin errores ni parches vacios")

        # Sección de evaluación funcional
        _print(f"")
        _print(f"  Evaluacion funcional (sb-cli):")
        if eval_summary is None or eval_summary.evaluation_skip:
            _print(f"    [--] Desactivada (evaluation_skip=true)")
        else:
            if eval_summary.groups_error == 0:
                _print(f"    [OK] {eval_summary.groups_evaluated} grupo(s) evaluado(s) sin errores")
            else:
                _print(f"    [!!] {eval_summary.groups_error}/{eval_summary.groups_evaluated} grupo(s) con error")
            total_eval = eval_summary.resolved + eval_summary.not_resolved + eval_summary.eval_errors
            if total_eval:
                _print(f"    Resueltas     : {eval_summary.resolved}/{total_eval}")
                _print(f"    No resueltas  : {eval_summary.not_resolved}/{total_eval}")
            if eval_summary.eval_errors:
                _print(f"    [!!] Errores eval: {eval_summary.eval_errors}")
            if eval_summary.quota_remaining is not None:
                _print(f"    Cuota restante: {eval_summary.quota_remaining} (swe-bench_lite/test)")
            elif eval_summary.quota_error:
                _print(f"    Cuota restante: ? ({eval_summary.quota_error})")

        _print(f"{'='*60}\n")


# ------------------------------------------------------------------
# Helpers internos


def _fmt_duration(seconds: float) -> str:
    """Formatea segundos como 'Xm Ys' o 'Xh Ym Zs'."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _fmt_eta(durations: list[float], remaining: int) -> str:
    """Estima tiempo restante en base a la media de la sesión actual."""
    if not durations or remaining <= 0:
        return "?"
    avg = sum(durations) / len(durations)
    return _fmt_duration(avg * remaining)


def _patch_size_str(model_patch_path: str | None) -> str:
    """Devuelve el tamaño del parche en bytes o '?' si no aplica."""
    if not model_patch_path:
        return "0B"
    try:
        size = Path(model_patch_path).stat().st_size
        return f"{size}B"
    except OSError:
        return "?"


def _print(msg: str) -> None:
    """Imprime a stderr con flush inmediato."""
    print(msg, file=sys.stderr, flush=True)
