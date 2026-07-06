"""`SbCliClient` (`modulo_evaluacion.md` MEV.8).

Wrapper sobre la CLI `sb-cli submit` con manejo de timeouts, retries y
descarga del reporte nativo. Para tests, el cliente acepta un
``runner`` inyectable con la firma de `subprocess.run`, de forma que
no se invoca la CLI real (no requiere `sb-cli` ni red).

La spec compromete:
  - submission via `sb-cli submit swe-bench_lite --predictions_path ...`
    (MEV.4.2),
  - hasta 1 reintento ante errores transitorios 5xx,
  - distincion clara entre `evaluation_error` (CLI ejecutada con error
    no transitorio) y `unavailable` (CLI no instalada o sin red).

El cliente devuelve un `SbCliSubmissionResult` con el JSON parseado del
reporte nativo (cuando el backend responde) o con un motivo de fallo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from common.env import SWEBENCH_API_KEY_ENV

# Tipo del runner de la CLI: equivalente a `subprocess.run(args, ...) -> CompletedProcess`.
# Se inyecta en tests con un fake que devuelve `CompletedProcess` controlados.
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]

_HTTP_5XX_RE = re.compile(r"\b5\d{2}\b")
"""Heuristica de status 5xx en stdout/stderr para clasificar transitorios."""


@dataclass
class SbCliSubmissionResult:
    """Resultado de una submission a `sb-cli` para un grupo."""

    status: str
    """`evaluated`, `evaluation_error`, `unavailable`."""
    backend_run_id: str | None = None
    """`run_id` propio del backend cuando esta disponible."""
    report: dict[str, Any] | None = None
    """Reporte nativo de `sb-cli` (JSON parseado)."""
    report_path: Path | None = None
    """Ruta local donde se persistio el reporte nativo."""
    command: list[str] = field(default_factory=list)
    log_excerpt: str | None = None
    error_reason: str | None = None
    """Motivo cuando `status != evaluated`."""


class SbCliClient:
    """Cliente CLI inyectable para `sb-cli submit`."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = "sb-cli",
        max_retries: int = 1,
        timeout_seconds: int = 1800,
        sleep_fn: Callable[[float], None] = time.sleep,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        """Configura el cliente.

        ``runner`` permite inyectar una funcion equivalente a
        `subprocess.run` (los tests pasan un fake). ``binary`` es el
        ejecutable, normalmente `sb-cli` en el `PATH`.
        """
        self._runner = runner or subprocess.run
        self._binary = binary
        self._max_retries = max(0, int(max_retries))
        self._timeout_seconds = int(timeout_seconds)
        self._sleep_fn = sleep_fn
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        self._api_key_env = SWEBENCH_API_KEY_ENV

    # ------------------------------------------------------------------
    # API publica

    def is_available(self) -> bool:
        """Comprueba si el binario `sb-cli` esta accesible."""
        return shutil.which(self._binary) is not None or self._runner is not subprocess.run

    def submit(
        self,
        *,
        subset: str,
        split: str,
        predictions_path: Path,
        report_dir: Path,
        run_id: str | None = None,
    ) -> SbCliSubmissionResult:
        """Somete un `preds.json` a `sb-cli` y descarga el reporte.

        ``subset`` es el subset de SWE-bench (`swe-bench_lite`,
        `swe-bench_verified`, ...). ``split`` es la particion del dataset
        (`test`, `dev`, ...). ``report_dir`` es el directorio donde el
        cliente persistira el reporte nativo.

        ``run_id``: si se omite, `sb-cli` usa por defecto el nombre del
        directorio padre de ``predictions_path`` (comportamiento historico,
        estable mientras no cambie el layout de `groups/<group_id>/`). Un
        `run_id` explicito permite someter un reintento bajo una identidad
        nueva: `sb-cli` bloquea permanentemente las instancias ya sometidas
        bajo un `run_id` dado ("estas no se pueden cambiar"), asi que
        reenviar el mismo `run_id` tras un fallo del backend solo devuelve el
        reporte viejo sin re-evaluar nada — ver `modulo_evaluacion.md` seccion 3.
        """
        if not self.is_available():
            return SbCliSubmissionResult(
                status="unavailable",
                error_reason="sb_cli_not_installed",
                command=[],
            )

        report_dir.mkdir(parents=True, exist_ok=True)
        command = self._build_command(
            subset=subset,
            split=split,
            predictions_path=predictions_path,
            report_dir=report_dir,
            run_id=run_id,
        )

        attempt = 0
        last_error: str | None = None
        sb_cli_run_label = run_id or predictions_path.parent.name
        while True:
            attempt += 1
            if attempt == 1:
                _print_sb_cli(
                    f"[benchmark] Evaluacion funcional sb-cli: sometiendo grupo "
                    f"'{sb_cli_run_label}' ({subset}/{split}, "
                    f"{predictions_path.name})..."
                )
                _print_sb_cli(
                    "[benchmark] La salida de sb-cli aparecera a continuacion; "
                    "puede tardar varios minutos."
                )
            elif attempt > 1:
                _print_sb_cli(
                    f"[benchmark] Reintentando sb-cli (intento {attempt})..."
                )
            try:
                # Heredar stdout/stderr para que la barra de progreso de sb-cli
                # sea visible; capture_output=True dejaba la fase en silencio total.
                completed = self._runner(
                    command,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                    env=self._subprocess_env(),
                )
            except FileNotFoundError:
                return SbCliSubmissionResult(
                    status="unavailable",
                    error_reason="sb_cli_not_installed",
                    command=command,
                )
            except subprocess.TimeoutExpired:
                return SbCliSubmissionResult(
                    status="evaluation_error",
                    error_reason="polling_timeout",
                    command=command,
                )

            if completed.returncode == 0:
                _print_sb_cli("[benchmark] sb-cli submit finalizado correctamente.")
                return self._parse_success(
                    completed=completed, command=command, report_dir=report_dir
                )

            # Error: clasificar como transient (5xx) o no.
            log = (completed.stdout or "") + (completed.stderr or "")
            last_error = _truncate(log) if log.strip() else f"exit_code={completed.returncode}"
            if attempt > self._max_retries or not _is_transient(log):
                return SbCliSubmissionResult(
                    status="evaluation_error",
                    error_reason=last_error,
                    command=command,
                    log_excerpt=last_error,
                )
            self._sleep_fn(self._retry_backoff_seconds)

    # ------------------------------------------------------------------
    # Internos

    def _build_command(
        self,
        *,
        subset: str,
        split: str,
        predictions_path: Path,
        report_dir: Path,
        run_id: str | None = None,
    ) -> list[str]:
        """Construye la lista de argumentos para `sb-cli submit`."""
        command = [
            self._binary,
            "submit",
            subset,
            split,
            "--predictions_path",
            str(predictions_path),
            "--output_dir",
            str(report_dir),
        ]
        if run_id:
            command.extend(["--run_id", run_id])
        api_key = os.getenv(self._api_key_env)
        if api_key:
            command.extend(["--api_key", api_key])
        return command

    def _subprocess_env(self) -> dict[str, str]:
        """Entorno del subproceso: hereda el actual forzando UTF-8 en la salida.

        ``PYTHONIOENCODING=utf-8`` y ``PYTHONUTF8=1`` evitan que sb-cli crashee
        al imprimir caracteres Unicode (p. ej. ✓) en consolas Windows con cp1252.
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def _parse_success(
        self,
        *,
        completed: subprocess.CompletedProcess[str],
        command: list[str],
        report_dir: Path,
    ) -> SbCliSubmissionResult:
        """Parsea el reporte nativo escrito por `sb-cli` en `report_dir`."""
        report_files = sorted(report_dir.glob("*.json"))
        if not report_files:
            return SbCliSubmissionResult(
                status="evaluation_error",
                error_reason="report_not_found",
                command=command,
                log_excerpt=_truncate(completed.stdout or ""),
            )
        report_path = report_files[0]
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return SbCliSubmissionResult(
                status="evaluation_error",
                error_reason=f"report_unreadable: {exc.__class__.__name__}",
                command=command,
                report_path=report_path,
            )
        backend_run_id = (
            report.get("run_id") if isinstance(report, dict) else None
        )
        return SbCliSubmissionResult(
            status="evaluated",
            backend_run_id=backend_run_id if isinstance(backend_run_id, str) else None,
            report=report if isinstance(report, dict) else None,
            report_path=report_path,
            command=command,
            log_excerpt=_truncate(completed.stdout or ""),
        )


def _is_transient(log: str) -> bool:
    """Heuristica: error 5xx o mensajes habituales de transitorios."""
    if _HTTP_5XX_RE.search(log):
        return True
    lowered = log.lower()
    needles = ("timeout", "temporarily unavailable", "connection reset", "service unavailable")
    return any(needle in lowered for needle in needles)


def _truncate(text: str, *, max_chars: int = 1024) -> str:
    """Trunca un texto con sufijo `...` si excede `max_chars`."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _print_sb_cli(msg: str) -> None:
    """Imprime mensajes de progreso de sb-cli a stderr con flush inmediato."""
    print(msg, file=sys.stderr, flush=True)
